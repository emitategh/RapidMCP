"""FasterMCP: register tools, resources, prompts and serve them over gRPC."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from grpc import aio as grpc_aio

from mcp_grpc._generated import mcp_pb2, mcp_pb2_grpc
from mcp_grpc._utils import _paginate, _prefix_resource_uri, _to_content_items
from mcp_grpc.content import Audio, Image
from mcp_grpc.context import Context
from mcp_grpc.errors import McpError
from mcp_grpc.middleware import Middleware, ToolCallContext
from mcp_grpc.session import PendingRequests

logger = logging.getLogger("mcp_grpc.server")


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
    output_schema: str = ""  # JSON schema string; empty = no structured output
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


def _resolve_hints(fn: Callable) -> dict[str, Any]:
    """Resolve type hints for *fn*, handling ``from __future__ import annotations``.

    Returns the mapping from ``typing.get_type_hints`` when possible.
    Falls back to raw ``inspect.signature`` annotations so that
    un-importable forward references don't crash registration.
    """
    try:
        return typing.get_type_hints(fn)
    except Exception:
        return {
            name: p.annotation
            for name, p in inspect.signature(fn).parameters.items()
            if p.annotation is not inspect.Parameter.empty
        }


def _needs_context(fn: Callable) -> bool:
    """Return True if *fn* declares a ``ctx: Context`` parameter."""
    hints = _resolve_hints(fn)
    return any(v is Context for v in hints.values())


def _build_input_schema(fn: Callable) -> str:
    """Build a JSON Schema from function type hints."""
    hints = _resolve_hints(fn)
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}

    for param_name, param in sig.parameters.items():
        annotation = hints.get(param_name, param.annotation)
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
                    page, next_cursor = _paginate(all_tools, cursor_str, self._server.page_size)
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
                    page, next_cursor = _paginate(all_resources, cursor_str, self._server.page_size)
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
                        try:
                            raw = await res.handler()
                        except Exception:
                            logger.exception("Resource handler for '%s' raised", uri)
                            await write_queue.put(
                                mcp_pb2.ServerEnvelope(
                                    request_id=rid,
                                    error=mcp_pb2.ErrorResponse(
                                        code=500,
                                        message=f"Resource handler for '{uri}' failed",
                                    ),
                                )
                            )
                            continue
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
                    page, next_cursor = _paginate(all_templates, cursor_str, self._server.page_size)
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
                    page, next_cursor = _paginate(all_prompts, cursor_str, self._server.page_size)
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
                        try:
                            text = await prompt.handler(**dict(req.arguments))
                        except Exception:
                            logger.exception("Prompt handler '%s' raised", req.name)
                            await write_queue.put(
                                mcp_pb2.ServerEnvelope(
                                    request_id=rid,
                                    error=mcp_pb2.ErrorResponse(
                                        code=500,
                                        message=f"Prompt handler '{req.name}' failed",
                                    ),
                                )
                            )
                            continue
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
                        try:
                            result = handler(notif.payload)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            logger.exception("Notification handler for '%s' raised", type_name)

                elif msg_type == "cancel":
                    target_id = envelope.cancel.target_request_id
                    task = _tool_tasks.get(target_id)
                    if task and not task.done():
                        task.cancel()

                elif msg_type == "subscribe_res":
                    uri = envelope.subscribe_res.uri
                    for handler in self._server._subscribe_handlers:
                        try:
                            result = handler(uri)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            logger.exception("Subscribe handler for '%s' raised", uri)

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
            # Clean up session queue to prevent memory leaks
            try:
                self._server._session_queues.remove(write_queue)
            except ValueError:
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
            needs_ctx = _needs_context(fn)

            ann: ToolAnnotations | None = None
            if any((read_only, destructive, idempotent, open_world, title)):
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
        arguments: dict[str, Any],
        ctx: Context | None = None,
    ) -> mcp_pb2.CallToolResponse:
        """Invoke a tool with a pre-parsed arguments dict (used by middleware chain)."""
        tool = self._tools.get(name)
        if not tool:
            raise McpError(code=404, message=f"Tool '{name}' not found")
        args = dict(arguments)
        if tool.needs_context and ctx is not None:
            hints = _resolve_hints(tool.handler)
            for param_name, hint in hints.items():
                if hint is Context:
                    args[param_name] = ctx
                    break
        try:
            result = await tool.handler(**args)
            content = _to_content_items(result)
            return mcp_pb2.CallToolResponse(content=content, is_error=False)
        except Exception:
            logger.exception("Tool '%s' raised an exception", name)
            import traceback

            return mcp_pb2.CallToolResponse(
                content=[mcp_pb2.ContentItem(type="text", text=traceback.format_exc())],
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
