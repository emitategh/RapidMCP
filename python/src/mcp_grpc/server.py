"""FasterMCP: register tools, resources, prompts and serve them over gRPC."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from grpc import aio as grpc_aio

from mcp_grpc._generated import mcp_pb2, mcp_pb2_grpc
from mcp_grpc.content import Audio, Image
from mcp_grpc.elicitation import ElicitationResult, build_elicitation_schema
from mcp_grpc.elicitation import ElicitationField  # noqa: F401 — re-exported for convenience
from mcp_grpc.errors import McpError
from mcp_grpc.middleware import Middleware, ToolCallContext
from mcp_grpc.session import PendingRequests


@dataclass
class ToolAnnotations:
    """Behavioural hints for a tool, surfaced to MCP clients.

    All fields are optional. Clients use these to decide how to present or
    invoke the tool (e.g. warn the user before calling a destructive tool).
    """

    title: str = ""
    read_only_hint: bool = False
    destructive_hint: bool = False
    idempotent_hint: bool = False
    open_world_hint: bool = False


@dataclass
class RegisteredTool:
    name: str
    description: str
    input_schema: str
    handler: Callable[..., Awaitable[Any]]
    needs_context: bool = False
    output_schema: str = ""           # JSON schema string; empty = no structured output
    annotations: ToolAnnotations | None = None


@dataclass
class RegisteredResource:
    uri: str
    name: str
    description: str
    mime_type: str
    handler: Callable[..., Awaitable[Any]]


@dataclass
class RegisteredPrompt:
    name: str
    description: str
    arguments: list[dict[str, Any]]
    handler: Callable[..., Awaitable[Any]]


@dataclass
class RegisteredResourceTemplate:
    uri_template: str
    name: str
    description: str
    mime_type: str
    handler: Callable[..., Awaitable[Any]]


@dataclass
class RegisteredCompletion:
    ref_name: str
    handler: Callable[..., Awaitable[list[str]]]


class Context:
    """Provides sampling and elicitation to tool handlers via dependency injection."""

    def __init__(
        self,
        client_capabilities: mcp_pb2.ClientCapabilities,
        pending: PendingRequests,
        write_queue: asyncio.Queue,
    ) -> None:
        self._capabilities = client_capabilities
        self._pending = pending
        self._write_queue = write_queue

    async def sample(
        self,
        messages: list,
        max_tokens: int,
        system_prompt: str | None = None,
        model_preferences: dict | mcp_pb2.ModelPreferences | None = None,
        tools: list | None = None,
        tool_choice: str = "",
    ) -> mcp_pb2.SamplingResponse:
        """Request LLM completion from the client.

        Args:
            messages: List of ``mcp_pb2.SamplingMessage`` protos or dicts with
                ``{"role": str, "content": str | list}`` keys.
            max_tokens: Maximum tokens for the completion.
            system_prompt: Optional system prompt.
            model_preferences: Optional model selection hints.  Accepts an
                ``mcp_pb2.ModelPreferences`` proto or a dict with optional keys
                ``hints`` (list of model-name strings), ``cost_priority``,
                ``speed_priority``, ``intelligence_priority`` (each 0–1 float).
            tools: Optional list of tools available during sampling.  Each
                element may be an ``mcp_pb2.SamplingTool`` proto or a dict with
                ``name``, ``description``, and ``input_schema`` keys.
            tool_choice: How the model may use tools — ``"auto"``,
                ``"required"``, ``"none"``, or ``""`` (server default).
        """
        if not self._capabilities.sampling:
            raise McpError(400, "Client does not support sampling")
        rid = self._pending.next_id()
        future = self._pending.create(rid)

        # --- build SamplingMessages (content is now repeated ContentItem) ---
        sampling_messages: list[mcp_pb2.SamplingMessage] = []
        for msg in messages:
            if isinstance(msg, mcp_pb2.SamplingMessage):
                sampling_messages.append(msg)
            else:
                raw_content = msg.get("content", "")
                if isinstance(raw_content, str):
                    content_items = [mcp_pb2.ContentItem(type="text", text=raw_content)]
                elif isinstance(raw_content, list):
                    content_items = []
                    for item in raw_content:
                        if isinstance(item, mcp_pb2.ContentItem):
                            content_items.append(item)
                        elif isinstance(item, str):
                            content_items.append(mcp_pb2.ContentItem(type="text", text=item))
                        else:
                            content_items.append(
                                mcp_pb2.ContentItem(
                                    type=item.get("type", "text"),
                                    text=item.get("text", ""),
                                )
                            )
                else:
                    content_items = [mcp_pb2.ContentItem(type="text", text=str(raw_content))]
                sampling_messages.append(
                    mcp_pb2.SamplingMessage(
                        role=msg.get("role", "user"),
                        content=content_items,
                    )
                )

        # --- build ModelPreferences ---
        model_prefs_proto: mcp_pb2.ModelPreferences | None = None
        if isinstance(model_preferences, mcp_pb2.ModelPreferences):
            model_prefs_proto = model_preferences
        elif isinstance(model_preferences, dict):
            hints = [
                mcp_pb2.ModelHint(name=h) if isinstance(h, str) else mcp_pb2.ModelHint(name=h.get("name", ""))
                for h in model_preferences.get("hints", [])
            ]
            model_prefs_proto = mcp_pb2.ModelPreferences(
                hints=hints,
                cost_priority=float(model_preferences.get("cost_priority", 0.0)),
                speed_priority=float(model_preferences.get("speed_priority", 0.0)),
                intelligence_priority=float(model_preferences.get("intelligence_priority", 0.0)),
            )

        # --- build SamplingTools ---
        sampling_tools: list[mcp_pb2.SamplingTool] = []
        for t in tools or []:
            if isinstance(t, mcp_pb2.SamplingTool):
                sampling_tools.append(t)
            else:
                sampling_tools.append(
                    mcp_pb2.SamplingTool(
                        name=t.get("name", ""),
                        description=t.get("description", ""),
                        input_schema=t.get("input_schema", ""),
                    )
                )

        req = mcp_pb2.SamplingRequest(
            messages=sampling_messages,
            system_prompt=system_prompt or "",
            max_tokens=max_tokens,
            tool_choice=tool_choice,
        )
        if model_prefs_proto is not None:
            req.model_preferences.CopyFrom(model_prefs_proto)
        if sampling_tools:
            req.tools.extend(sampling_tools)

        envelope = mcp_pb2.ServerEnvelope(request_id=rid, sampling=req)
        await self._write_queue.put(envelope)
        return await asyncio.wait_for(future, timeout=30.0)

    async def elicit(
        self,
        message: str,
        schema: str | None = None,
        fields: dict | None = None,
    ) -> ElicitationResult:
        """Request user input from the client.

        Args:
            message: Human-readable prompt shown to the user.
            schema:  Raw JSON Schema string describing the form.  Mutually
                exclusive with *fields*.
            fields:  Mapping of field name → field descriptor
                (``StringField``, ``BoolField``, ``IntField``, ``FloatField``,
                ``EnumField``).  Automatically serialised to a valid MCP
                elicitation schema.

        Returns:
            An :class:`ElicitationResult` with ``action`` (``"accept"``,
            ``"decline"``, or ``"cancel"``) and ``data`` (dict of filled values
            when accepted).
        """
        if not self._capabilities.elicitation:
            raise McpError(400, "Client does not support elicitation")
        if fields is not None and schema is not None:
            raise ValueError("Provide either 'schema' or 'fields', not both")

        resolved_schema = schema or (build_elicitation_schema(fields) if fields else "")

        rid = self._pending.next_id()
        future = self._pending.create(rid)
        envelope = mcp_pb2.ServerEnvelope(
            request_id=rid,
            elicitation=mcp_pb2.ElicitationRequest(
                message=message,
                schema=resolved_schema,
            ),
        )
        await self._write_queue.put(envelope)
        raw: mcp_pb2.ElicitationResponse = await asyncio.wait_for(future, timeout=30.0)
        data: dict = {}
        if raw.content:
            try:
                parsed = json.loads(raw.content)
                if isinstance(parsed, dict):
                    data = parsed
            except (ValueError, TypeError):
                pass
        return ElicitationResult(action=raw.action, data=data)

    async def _log(self, level: str, message: str, extra: dict | None) -> None:
        await self._write_queue.put(
            mcp_pb2.ServerEnvelope(
                request_id=0,
                notification=mcp_pb2.ServerNotification(
                    type=mcp_pb2.ServerNotification.LOG,
                    payload=json.dumps({"level": level, "message": message, "extra": extra}),
                ),
            )
        )

    async def info(self, message: str, extra: dict | None = None) -> None:
        """Send an info-level log to this session's client."""
        await self._log("info", message, extra)

    async def debug(self, message: str, extra: dict | None = None) -> None:
        """Send a debug-level log to this session's client."""
        await self._log("debug", message, extra)

    async def warning(self, message: str, extra: dict | None = None) -> None:
        """Send a warning-level log to this session's client."""
        await self._log("warning", message, extra)

    async def error(self, message: str, extra: dict | None = None) -> None:
        """Send an error-level log to this session's client."""
        await self._log("error", message, extra)

    async def report_progress(self, progress: float, total: float | None = None) -> None:
        """Report progress to this session's client."""
        await self._write_queue.put(
            mcp_pb2.ServerEnvelope(
                request_id=0,
                notification=mcp_pb2.ServerNotification(
                    type=mcp_pb2.ServerNotification.PROGRESS,
                    payload=json.dumps({"progress": progress, "total": total}),
                ),
            )
        )

    async def list_roots(self) -> mcp_pb2.ListRootsResponse:
        """Request the client's registered root URIs."""
        if not self._capabilities.roots:
            raise McpError(400, "Client does not support roots")
        rid = self._pending.next_id()
        future = self._pending.create(rid)
        await self._write_queue.put(
            mcp_pb2.ServerEnvelope(
                request_id=rid,
                roots_request=mcp_pb2.ListRootsRequest(),
            )
        )
        return await asyncio.wait_for(future, timeout=30.0)


def _prefix_resource_uri(uri: str, prefix: str) -> str:
    """Insert *prefix* as the first path segment after the scheme.

    "res://greeting"   -> "res://users/greeting"
    "res://items/{id}" -> "res://users/items/{id}"
    "plain/path"       -> "users/plain/path"  (no scheme)
    """
    if "://" not in uri:
        return f"{prefix}/{uri}"
    scheme, rest = uri.split("://", 1)
    return f"{scheme}://{prefix}/{rest}"


def _to_content_items(result: Any) -> list[mcp_pb2.ContentItem]:
    """Convert a tool return value to a list of ContentItem protos.

    Supported types:
    * ``str``   → text content
    * ``Image`` → image content (base64-encoded bytes + mime_type)
    * ``Audio`` → audio content (base64-encoded bytes + mime_type)
    * ``dict``  → JSON-serialised text content (structured output)
    * ``list``  → each element converted recursively (flattened one level)
    * anything else → ``str()`` representation as text
    """
    if isinstance(result, list):
        items: list[mcp_pb2.ContentItem] = []
        for elem in result:
            items.extend(_to_content_items(elem))
        return items
    if isinstance(result, str):
        return [mcp_pb2.ContentItem(type="text", text=result)]
    if isinstance(result, Image):
        return [mcp_pb2.ContentItem(type="image", data=result.data, mime_type=result.mime_type)]
    if isinstance(result, Audio):
        return [mcp_pb2.ContentItem(type="audio", data=result.data, mime_type=result.mime_type)]
    if isinstance(result, dict):
        return [mcp_pb2.ContentItem(type="text", text=json.dumps(result))]
    return [mcp_pb2.ContentItem(type="text", text=str(result))]


def _build_input_schema(fn: Callable) -> str:
    """Build a JSON Schema from function type hints."""
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}

    for param_name, param in sig.parameters.items():
        annotation = param.annotation
        if annotation is Context:
            continue  # skip DI parameters
        json_type = type_map.get(annotation, "string")
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return json.dumps(schema)


class _McpServicer(mcp_pb2_grpc.McpServicer):
    """Handles bidi Session stream with concurrent reader/writer."""

    def __init__(self, server: FasterMCP) -> None:
        self._server = server

    async def Session(self, request_iterator, context):
        write_queue: asyncio.Queue[mcp_pb2.ServerEnvelope] = asyncio.Queue()
        server_pending = PendingRequests()
        client_capabilities = mcp_pb2.ClientCapabilities()

        async def _writer():
            while True:
                envelope = await write_queue.get()
                if envelope is None:
                    break
                yield envelope

        def _paginate(items: list, cursor_str: str) -> tuple[list, str]:
            """Slice *items* according to the server page_size and *cursor_str*.

            *cursor_str* is an opaque decimal integer offset (empty = 0).
            Returns (page, next_cursor_str) where next_cursor_str is empty
            when there are no further pages.
            """
            ps = self._server.page_size
            if ps is None:
                return items, ""
            offset = int(cursor_str) if cursor_str else 0
            page = items[offset : offset + ps]
            next_offset = offset + ps
            next_cursor = str(next_offset) if next_offset < len(items) else ""
            return page, next_cursor

        async def _reader():
            # Maps request_id → running tool Task for cancellation support
            _tool_tasks: dict[int, asyncio.Task] = {}

            async for envelope in request_iterator:
                rid = envelope.request_id
                msg_type = envelope.WhichOneof("message")

                if msg_type == "initialize":
                    nonlocal client_capabilities
                    client_capabilities = envelope.initialize.capabilities
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            initialize=mcp_pb2.InitializeResponse(
                                server_name=self._server.name,
                                server_version=self._server.version,
                                capabilities=mcp_pb2.ServerCapabilities(
                                    tools=bool(self._server._tools),
                                    tools_list_changed=False,
                                    resources=bool(self._server._resources),
                                    prompts=bool(self._server._prompts),
                                ),
                            ),
                        )
                    )

                elif msg_type == "initialized":
                    self._server._session_queues.append(write_queue)

                elif msg_type == "list_tools":
                    cursor_str = envelope.list_tools.cursor
                    all_tools = []
                    for t in self._server._tools.values():
                        ann_proto = None
                        if t.annotations:
                            ann_proto = mcp_pb2.ToolAnnotations(
                                title=t.annotations.title,
                                read_only_hint=t.annotations.read_only_hint,
                                destructive_hint=t.annotations.destructive_hint,
                                idempotent_hint=t.annotations.idempotent_hint,
                                open_world_hint=t.annotations.open_world_hint,
                            )
                        all_tools.append(
                            mcp_pb2.ToolDefinition(
                                name=t.name,
                                description=t.description,
                                input_schema=t.input_schema,
                                output_schema=t.output_schema,
                                annotations=ann_proto,
                            )
                        )
                    page, next_cursor = _paginate(all_tools, cursor_str)
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            list_tools=mcp_pb2.ListToolsResponse(
                                tools=page, next_cursor=next_cursor
                            ),
                        )
                    )

                elif msg_type == "call_tool":
                    req = envelope.call_tool

                    async def _run_tool(_rid, _req, _caps=client_capabilities):
                        try:
                            tool = self._server._tools.get(_req.name)
                            ctx = None
                            if tool and tool.needs_context:
                                ctx = Context(
                                    client_capabilities=_caps,
                                    pending=server_pending,
                                    write_queue=write_queue,
                                )
                            args = json.loads(_req.arguments) if _req.arguments else {}
                            result = await self._server._dispatch_tool(_req.name, args, ctx)
                            await write_queue.put(
                                mcp_pb2.ServerEnvelope(
                                    request_id=_rid,
                                    call_tool=result,
                                )
                            )
                        except asyncio.CancelledError:
                            await write_queue.put(
                                mcp_pb2.ServerEnvelope(
                                    request_id=_rid,
                                    error=mcp_pb2.ErrorResponse(
                                        code=499, message="Tool call cancelled"
                                    ),
                                )
                            )
                        except McpError as e:
                            await write_queue.put(
                                mcp_pb2.ServerEnvelope(
                                    request_id=_rid,
                                    error=mcp_pb2.ErrorResponse(code=e.code, message=e.message),
                                )
                            )
                        finally:
                            _tool_tasks.pop(_rid, None)

                    task = asyncio.create_task(_run_tool(rid, req))
                    _tool_tasks[rid] = task

                elif msg_type == "list_resources":
                    cursor_str = envelope.list_resources.cursor
                    all_resources = [
                        mcp_pb2.ResourceDefinition(
                            uri=r.uri,
                            name=r.name,
                            description=r.description,
                            mime_type=r.mime_type,
                        )
                        for r in self._server._resources.values()
                    ]
                    page, next_cursor = _paginate(all_resources, cursor_str)
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            list_resources=mcp_pb2.ListResourcesResponse(
                                resources=page, next_cursor=next_cursor
                            ),
                        )
                    )

                elif msg_type == "read_resource":
                    uri = envelope.read_resource.uri
                    res = self._server._resources.get(uri)
                    if not res:
                        await write_queue.put(
                            mcp_pb2.ServerEnvelope(
                                request_id=rid,
                                error=mcp_pb2.ErrorResponse(
                                    code=404,
                                    message=f"Resource '{uri}' not found",
                                ),
                            )
                        )
                    else:
                        raw = await res.handler()
                        if isinstance(raw, bytes):
                            mime = res.mime_type
                            if mime.startswith("image/"):
                                content_item = mcp_pb2.ContentItem(
                                    type="image", data=raw, mime_type=mime
                                )
                            elif mime.startswith("audio/"):
                                content_item = mcp_pb2.ContentItem(
                                    type="audio", data=raw, mime_type=mime
                                )
                            else:
                                content_item = mcp_pb2.ContentItem(
                                    type="resource", data=raw, mime_type=mime
                                )
                        else:
                            content_item = mcp_pb2.ContentItem(type="text", text=str(raw))
                        await write_queue.put(
                            mcp_pb2.ServerEnvelope(
                                request_id=rid,
                                read_resource=mcp_pb2.ReadResourceResponse(
                                    content=[content_item],
                                ),
                            )
                        )

                elif msg_type == "list_resource_templates":
                    cursor_str = envelope.list_resource_templates.cursor
                    all_templates = [
                        mcp_pb2.ResourceTemplateDefinition(
                            uri_template=t.uri_template,
                            name=t.name,
                            description=t.description,
                            mime_type=t.mime_type,
                        )
                        for t in self._server._resource_templates.values()
                    ]
                    page, next_cursor = _paginate(all_templates, cursor_str)
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            list_resource_templates=mcp_pb2.ListResourceTemplatesResponse(
                                templates=page, next_cursor=next_cursor
                            ),
                        )
                    )

                elif msg_type == "list_prompts":
                    cursor_str = envelope.list_prompts.cursor
                    all_prompts = [
                        mcp_pb2.PromptDefinition(
                            name=p.name,
                            description=p.description,
                            arguments=[mcp_pb2.PromptArgument(**a) for a in p.arguments],
                        )
                        for p in self._server._prompts.values()
                    ]
                    page, next_cursor = _paginate(all_prompts, cursor_str)
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            list_prompts=mcp_pb2.ListPromptsResponse(
                                prompts=page, next_cursor=next_cursor
                            ),
                        )
                    )

                elif msg_type == "get_prompt":
                    req = envelope.get_prompt
                    prompt = self._server._prompts.get(req.name)
                    if not prompt:
                        await write_queue.put(
                            mcp_pb2.ServerEnvelope(
                                request_id=rid,
                                error=mcp_pb2.ErrorResponse(
                                    code=404,
                                    message=f"Prompt '{req.name}' not found",
                                ),
                            )
                        )
                    else:
                        text = await prompt.handler(**dict(req.arguments))
                        await write_queue.put(
                            mcp_pb2.ServerEnvelope(
                                request_id=rid,
                                get_prompt=mcp_pb2.GetPromptResponse(
                                    messages=[
                                        mcp_pb2.PromptMessage(
                                            role="assistant",
                                            content=mcp_pb2.ContentItem(type="text", text=text),
                                        )
                                    ],
                                ),
                            )
                        )

                elif msg_type == "complete":
                    req = envelope.complete
                    comp = self._server._completions.get(req.ref.name)
                    if not comp:
                        await write_queue.put(
                            mcp_pb2.ServerEnvelope(
                                request_id=rid,
                                complete=mcp_pb2.CompleteResponse(
                                    values=[], has_more=False, total=0
                                ),
                            )
                        )
                    else:
                        values = await comp.handler(req.argument.name, req.argument.value)
                        await write_queue.put(
                            mcp_pb2.ServerEnvelope(
                                request_id=rid,
                                complete=mcp_pb2.CompleteResponse(
                                    values=values,
                                    has_more=False,
                                    total=len(values),
                                ),
                            )
                        )

                elif msg_type == "ping":
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            pong=mcp_pb2.PingResponse(),
                        )
                    )

                elif msg_type == "sampling_reply":
                    server_pending.resolve(rid, envelope.sampling_reply)

                elif msg_type == "elicitation_reply":
                    server_pending.resolve(rid, envelope.elicitation_reply)

                elif msg_type == "roots_reply":
                    server_pending.resolve(rid, envelope.roots_reply)

                elif msg_type == "client_notification":
                    notif = envelope.client_notification
                    type_name = mcp_pb2.ClientNotification.Type.Name(notif.type).lower()
                    for handler in self._server._client_notification_handlers.get(type_name, []):
                        result = handler(notif.payload)
                        if asyncio.iscoroutine(result):
                            await result

                elif msg_type == "cancel":
                    target_id = envelope.cancel.target_request_id
                    task = _tool_tasks.get(target_id)
                    if task and not task.done():
                        task.cancel()

                elif msg_type == "subscribe_res":
                    uri = envelope.subscribe_res.uri
                    for handler in self._server._subscribe_handlers:
                        result = handler(uri)
                        if asyncio.iscoroutine(result):
                            await result

                else:
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            error=mcp_pb2.ErrorResponse(
                                code=400,
                                message=f"Unknown message type: {msg_type}",
                            ),
                        )
                    )

            # Wait for any in-flight tool tasks before signalling writer to stop
            if _tool_tasks:
                await asyncio.gather(*_tool_tasks.values(), return_exceptions=True)
            # Reader done — signal writer to stop
            await write_queue.put(None)

        # Start reader as a background task, yield from writer
        reader_task = asyncio.create_task(_reader())
        try:
            async for envelope in _writer():
                yield envelope
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass


class FasterMCP:
    """Register tools, resources, and prompts, then serve over gRPC."""

    def __init__(
        self,
        name: str,
        version: str,
        middleware: list[Middleware] | None = None,
        page_size: int | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.page_size = page_size
        self._tools: dict[str, RegisteredTool] = {}
        self._resources: dict[str, RegisteredResource] = {}
        self._prompts: dict[str, RegisteredPrompt] = {}
        self._resource_templates: dict[str, RegisteredResourceTemplate] = {}
        self._completions: dict[str, RegisteredCompletion] = {}
        self._session_queues: list[asyncio.Queue] = []
        self._client_notification_handlers: dict[str, list[Callable]] = {}
        self._subscribe_handlers: list[Callable] = []
        self._middleware: list[Middleware] = list(middleware or [])

    def tool(
        self,
        *,
        description: str | None = None,
        output_schema: dict[str, Any] | None = None,
        read_only: bool = False,
        destructive: bool = False,
        idempotent: bool = False,
        open_world: bool = False,
        title: str = "",
    ) -> Callable[[Callable], Callable]:
        """Register a tool on this server.

        Args:
            description: Human-readable description. Falls back to the
                function's docstring if omitted.
            output_schema: JSON Schema dict describing the tool's structured
                output. When provided, the schema is serialised and sent to
                clients in ``ToolDefinition.output_schema``.
            read_only: Hint that this tool does not modify state.
            destructive: Hint that this tool may perform destructive changes.
            idempotent: Hint that repeated calls produce the same result.
            open_world: Hint that this tool interacts with the external world.
            title: Short human-readable title for the tool.
        """

        def decorator(fn: Callable) -> Callable:
            desc = description or (fn.__doc__ or "").strip()
            sig = inspect.signature(fn)
            needs_ctx = any(p.annotation is Context for p in sig.parameters.values())

            ann: ToolAnnotations | None = None
            if any([read_only, destructive, idempotent, open_world, title]):
                ann = ToolAnnotations(
                    title=title,
                    read_only_hint=read_only,
                    destructive_hint=destructive,
                    idempotent_hint=idempotent,
                    open_world_hint=open_world,
                )

            self._tools[fn.__name__] = RegisteredTool(
                name=fn.__name__,
                description=desc,
                input_schema=_build_input_schema(fn),
                handler=fn,
                needs_context=needs_ctx,
                output_schema=json.dumps(output_schema) if output_schema else "",
                annotations=ann,
            )
            return fn

        return decorator

    def resource(
        self,
        uri: str,
        *,
        description: str | None = None,
        mime_type: str = "text/plain",
    ) -> Callable:
        def decorator(fn: Callable) -> Callable:
            desc = description or (fn.__doc__ or "").strip()
            self._resources[uri] = RegisteredResource(
                uri=uri,
                name=fn.__name__,
                description=desc,
                mime_type=mime_type,
                handler=fn,
            )
            return fn

        return decorator

    def prompt(self, *, description: str | None = None) -> Callable[[Callable], Callable]:
        def decorator(fn: Callable) -> Callable:
            desc = description or (fn.__doc__ or "").strip()
            sig = inspect.signature(fn)
            arguments = [
                {
                    "name": p_name,
                    "description": "",
                    "required": p.default is inspect.Parameter.empty,
                }
                for p_name, p in sig.parameters.items()
            ]
            self._prompts[fn.__name__] = RegisteredPrompt(
                name=fn.__name__,
                description=desc,
                arguments=arguments,
                handler=fn,
            )
            return fn

        return decorator

    def completion(self, ref_name: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            self._completions[ref_name] = RegisteredCompletion(
                ref_name=ref_name,
                handler=fn,
            )
            return fn

        return decorator

    def resource_template(
        self,
        uri_template: str,
        *,
        description: str | None = None,
        mime_type: str = "text/plain",
    ) -> Callable:
        def decorator(fn: Callable) -> Callable:
            desc = description or (fn.__doc__ or "").strip()
            self._resource_templates[uri_template] = RegisteredResourceTemplate(
                uri_template=uri_template,
                name=fn.__name__,
                description=desc,
                mime_type=mime_type,
                handler=fn,
            )
            return fn

        return decorator

    def list_registered_tools(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    def list_registered_resources(self) -> list[RegisteredResource]:
        return list(self._resources.values())

    def list_registered_prompts(self) -> list[RegisteredPrompt]:
        return list(self._prompts.values())

    def list_registered_resource_templates(self) -> list[RegisteredResourceTemplate]:
        return list(self._resource_templates.values())

    def add_middleware(self, middleware: Middleware) -> None:
        """Append a middleware to the chain (outermost if added last)."""
        self._middleware.append(middleware)

    def mount(self, sub: FasterMCP, *, prefix: str) -> None:
        """Merge all registries from *sub* into this server under *prefix*.

        Tools and prompts are registered as ``{prefix}_{name}``.
        Resources and resource templates get *prefix* inserted as the first
        path segment after the scheme (``res://x`` -> ``res://{prefix}/x``).
        Completions are re-keyed as ``{prefix}_{ref_name}``.

        Sub-server middleware, notification handlers, and subscribe handlers
        are NOT merged — *sub* acts purely as a registry donor when mounted.
        Main's middleware chain wraps all mounted tools.

        Raises ValueError on any name/URI collision. The operation is atomic:
        either all registrations succeed or none are written.
        """
        # Pass 1: compute all new keys
        new_tools = {f"{prefix}_{n}": t for n, t in sub._tools.items()}
        new_resources = {_prefix_resource_uri(u, prefix): r for u, r in sub._resources.items()}
        new_templates = {
            _prefix_resource_uri(u, prefix): t for u, t in sub._resource_templates.items()
        }
        new_prompts = {f"{prefix}_{n}": p for n, p in sub._prompts.items()}
        new_completions = {f"{prefix}_{r}": c for r, c in sub._completions.items()}

        # Pass 1: validate — no writes yet
        for k in new_tools:
            if k in self._tools:
                raise ValueError(f"mount(prefix={prefix!r}): tool collision '{k}'")
        for k in new_resources:
            if k in self._resources:
                raise ValueError(f"mount(prefix={prefix!r}): resource collision '{k}'")
        for k in new_templates:
            if k in self._resource_templates:
                raise ValueError(f"mount(prefix={prefix!r}): resource template collision '{k}'")
        for k in new_prompts:
            if k in self._prompts:
                raise ValueError(f"mount(prefix={prefix!r}): prompt collision '{k}'")
        for k in new_completions:
            if k in self._completions:
                raise ValueError(f"mount(prefix={prefix!r}): completion collision '{k}'")

        # Pass 2: write — all or nothing
        for new_name, tool in new_tools.items():
            self._tools[new_name] = replace(tool, name=new_name)
        for new_uri, res in new_resources.items():
            self._resources[new_uri] = replace(res, uri=new_uri)
        for new_tmpl, tmpl in new_templates.items():
            self._resource_templates[new_tmpl] = replace(tmpl, uri_template=new_tmpl)
        for new_name, prompt in new_prompts.items():
            self._prompts[new_name] = replace(prompt, name=new_name)
        for new_ref, comp in new_completions.items():
            self._completions[new_ref] = replace(comp, ref_name=new_ref)

    async def _dispatch_tool(
        self,
        name: str,
        arguments: dict,
        ctx: Context | None,
    ) -> mcp_pb2.CallToolResponse:
        """Run a tool call through the full middleware chain.

        Chain is built in reversed registration order so the first-registered
        middleware is the outermost wrapper (runs first on entry, last on exit).
        functools.partial captures each call_next by value at construction time,
        avoiding the classic loop-closure bug.
        """
        registered = self._tools.get(name)
        schema_dict: dict | None = None
        if registered:
            try:
                schema_dict = json.loads(registered.input_schema)
            except (ValueError, TypeError):
                pass
        tool_ctx = ToolCallContext(
            tool_name=name, arguments=arguments, ctx=ctx, input_schema=schema_dict
        )

        async def base(tc: ToolCallContext) -> mcp_pb2.CallToolResponse:
            return await self._call_tool_with_dict(tc.tool_name, tc.arguments, tc.ctx)

        chain = base
        for mw in reversed(self._middleware):
            chain = partial(mw.on_tool_call, call_next=chain)

        return await chain(tool_ctx)

    def on_roots_list_changed(self, handler: Callable) -> None:
        self._client_notification_handlers.setdefault("roots_list_changed", []).append(handler)

    def on_resource_subscribe(self, handler: Callable) -> None:
        self._subscribe_handlers.append(handler)

    def _broadcast(self, notification: mcp_pb2.ServerNotification) -> None:
        """Send a notification to all active sessions."""
        envelope = mcp_pb2.ServerEnvelope(
            request_id=0,
            notification=notification,
        )
        for queue in self._session_queues:
            queue.put_nowait(envelope)

    def notify_tools_list_changed(self) -> None:
        self._broadcast(
            mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.TOOLS_LIST_CHANGED,
            )
        )

    def notify_resources_list_changed(self) -> None:
        self._broadcast(
            mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.RESOURCES_LIST_CHANGED,
            )
        )

    def notify_resource_updated(self, uri: str) -> None:
        self._broadcast(
            mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.RESOURCE_UPDATED,
                payload=json.dumps({"uri": uri}),
            )
        )

    def notify_prompts_list_changed(self) -> None:
        self._broadcast(
            mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.PROMPTS_LIST_CHANGED,
            )
        )

    async def _call_tool_with_dict(
        self,
        name: str,
        arguments: dict,
        ctx: Context | None = None,
    ) -> mcp_pb2.CallToolResponse:
        """Invoke a tool with a pre-parsed arguments dict (used by middleware chain)."""
        tool = self._tools.get(name)
        if not tool:
            raise McpError(code=404, message=f"Tool '{name}' not found")
        args = dict(arguments)
        if tool.needs_context and ctx is not None:
            sig = inspect.signature(tool.handler)
            for param_name, param in sig.parameters.items():
                if param.annotation is Context:
                    args[param_name] = ctx
                    break
        try:
            result = await tool.handler(**args)
            content = _to_content_items(result)
            return mcp_pb2.CallToolResponse(content=content, is_error=False)
        except Exception as e:
            return mcp_pb2.CallToolResponse(
                content=[mcp_pb2.ContentItem(type="text", text=str(e))],
                is_error=True,
            )

    async def handle_call_tool(
        self,
        name: str,
        arguments_json: str,
        context: Context | None = None,
    ) -> mcp_pb2.CallToolResponse:
        """Public API: parse JSON arguments and invoke the tool."""
        args = json.loads(arguments_json) if arguments_json else {}
        return await self._call_tool_with_dict(name, args, context)

    async def _start_grpc(self, port: int) -> grpc_aio.Server:
        grpc_server = grpc_aio.server()
        mcp_pb2_grpc.add_McpServicer_to_server(_McpServicer(self), grpc_server)
        # Try IPv6 first, fall back to IPv4 on Windows
        try:
            actual_port = grpc_server.add_insecure_port(f"[::]:{port}")
            await grpc_server.start()
        except RuntimeError:
            # Fall back to IPv4 (common on Windows)
            grpc_server = grpc_aio.server()
            mcp_pb2_grpc.add_McpServicer_to_server(_McpServicer(self), grpc_server)
            actual_port = grpc_server.add_insecure_port(f"127.0.0.1:{port}")
            await grpc_server.start()
        self._port = actual_port
        return grpc_server

    def run(self, port: int = 50051) -> None:
        """Blocking entry point — starts the gRPC server."""

        async def _run():
            grpc_server = await self._start_grpc(port)
            print(f"FasterMCP '{self.name}' listening on port {self._port}", flush=True)
            await grpc_server.wait_for_termination()

        asyncio.run(_run())

    async def __aenter__(self):
        """Async context manager for tests — port=0 picks a free port."""
        self._grpc_server = await self._start_grpc(0)
        return self

    async def __aexit__(self, *exc):
        await self._grpc_server.stop(grace=0)

    @property
    def port(self) -> int:
        return self._port
