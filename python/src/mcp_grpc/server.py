"""FasterMCP: register tools, resources, prompts and serve them over gRPC."""
from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

import grpc
from grpc import aio as grpc_aio

from mcp_grpc._generated import mcp_pb2, mcp_pb2_grpc
from mcp_grpc.errors import McpError
from mcp_grpc.session import PendingRequests


@dataclass
class RegisteredTool:
    name: str
    description: str
    input_schema: str
    handler: Callable[..., Awaitable[Any]]
    needs_context: bool = False


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
    ) -> mcp_pb2.SamplingResponse:
        """Request LLM completion from the client."""
        if not self._capabilities.sampling:
            raise McpError(400, "Client does not support sampling")
        rid = self._pending.next_id()
        future = self._pending.create(rid)
        sampling_messages = []
        for msg in messages:
            if isinstance(msg, mcp_pb2.SamplingMessage):
                sampling_messages.append(msg)
            else:
                sampling_messages.append(mcp_pb2.SamplingMessage(
                    role=msg.get("role", "user"),
                    content=mcp_pb2.ContentItem(
                        type="text", text=msg.get("content", ""),
                    ),
                ))
        envelope = mcp_pb2.ServerEnvelope(
            request_id=rid,
            sampling=mcp_pb2.SamplingRequest(
                messages=sampling_messages,
                system_prompt=system_prompt or "",
                max_tokens=max_tokens,
            ),
        )
        await self._write_queue.put(envelope)
        return await asyncio.wait_for(future, timeout=30.0)

    async def elicit(
        self,
        message: str,
        schema: str | None = None,
    ) -> mcp_pb2.ElicitationResponse:
        """Request user input from the client."""
        if not self._capabilities.elicitation:
            raise McpError(400, "Client does not support elicitation")
        rid = self._pending.next_id()
        future = self._pending.create(rid)
        envelope = mcp_pb2.ServerEnvelope(
            request_id=rid,
            elicitation=mcp_pb2.ElicitationRequest(
                message=message,
                schema=schema or "",
            ),
        )
        await self._write_queue.put(envelope)
        return await asyncio.wait_for(future, timeout=30.0)

    async def _log(self, level: str, message: str, extra: dict | None) -> None:
        await self._write_queue.put(mcp_pb2.ServerEnvelope(
            request_id=0,
            notification=mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.LOG,
                payload=json.dumps({"level": level, "message": message, "extra": extra}),
            ),
        ))

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
        await self._write_queue.put(mcp_pb2.ServerEnvelope(
            request_id=0,
            notification=mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.PROGRESS,
                payload=json.dumps({"progress": progress, "total": total}),
            ),
        ))


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

        async def _reader():
            _tool_tasks: list[asyncio.Task] = []
            async for envelope in request_iterator:
                rid = envelope.request_id
                msg_type = envelope.WhichOneof("message")

                if msg_type == "initialize":
                    nonlocal client_capabilities
                    client_capabilities = envelope.initialize.capabilities
                    await write_queue.put(mcp_pb2.ServerEnvelope(
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
                    ))

                elif msg_type == "initialized":
                    self._server._session_queues.append(write_queue)

                elif msg_type == "list_tools":
                    tools = [
                        mcp_pb2.ToolDefinition(
                            name=t.name, description=t.description,
                            input_schema=t.input_schema,
                        )
                        for t in self._server._tools.values()
                    ]
                    await write_queue.put(mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        list_tools=mcp_pb2.ListToolsResponse(tools=tools),
                    ))

                elif msg_type == "call_tool":
                    req = envelope.call_tool

                    async def _run_tool(_rid, _req):
                        try:
                            tool = self._server._tools.get(_req.name)
                            ctx = None
                            if tool and tool.needs_context:
                                ctx = Context(
                                    client_capabilities=client_capabilities,
                                    pending=server_pending,
                                    write_queue=write_queue,
                                )
                            result = await self._server.handle_call_tool(
                                _req.name, _req.arguments, context=ctx,
                            )
                            await write_queue.put(mcp_pb2.ServerEnvelope(
                                request_id=_rid, call_tool=result,
                            ))
                        except McpError as e:
                            await write_queue.put(mcp_pb2.ServerEnvelope(
                                request_id=_rid,
                                error=mcp_pb2.ErrorResponse(code=e.code, message=e.message),
                            ))

                    _tool_tasks.append(asyncio.create_task(_run_tool(rid, req)))

                elif msg_type == "list_resources":
                    resources = [
                        mcp_pb2.ResourceDefinition(
                            uri=r.uri, name=r.name,
                            description=r.description, mime_type=r.mime_type,
                        )
                        for r in self._server._resources.values()
                    ]
                    await write_queue.put(mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        list_resources=mcp_pb2.ListResourcesResponse(resources=resources),
                    ))

                elif msg_type == "read_resource":
                    uri = envelope.read_resource.uri
                    res = self._server._resources.get(uri)
                    if not res:
                        await write_queue.put(mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            error=mcp_pb2.ErrorResponse(
                                code=404, message=f"Resource '{uri}' not found",
                            ),
                        ))
                    else:
                        text = await res.handler()
                        await write_queue.put(mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            read_resource=mcp_pb2.ReadResourceResponse(
                                content=[mcp_pb2.ContentItem(type="text", text=text)],
                            ),
                        ))

                elif msg_type == "list_resource_templates":
                    templates = [
                        mcp_pb2.ResourceTemplateDefinition(
                            uri_template=t.uri_template,
                            name=t.name,
                            description=t.description,
                            mime_type=t.mime_type,
                        )
                        for t in self._server._resource_templates.values()
                    ]
                    await write_queue.put(mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        list_resource_templates=mcp_pb2.ListResourceTemplatesResponse(
                            templates=templates,
                        ),
                    ))

                elif msg_type == "list_prompts":
                    prompts = [
                        mcp_pb2.PromptDefinition(
                            name=p.name, description=p.description,
                            arguments=[mcp_pb2.PromptArgument(**a) for a in p.arguments],
                        )
                        for p in self._server._prompts.values()
                    ]
                    await write_queue.put(mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        list_prompts=mcp_pb2.ListPromptsResponse(prompts=prompts),
                    ))

                elif msg_type == "get_prompt":
                    req = envelope.get_prompt
                    prompt = self._server._prompts.get(req.name)
                    if not prompt:
                        await write_queue.put(mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            error=mcp_pb2.ErrorResponse(
                                code=404, message=f"Prompt '{req.name}' not found",
                            ),
                        ))
                    else:
                        text = await prompt.handler(**dict(req.arguments))
                        await write_queue.put(mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            get_prompt=mcp_pb2.GetPromptResponse(
                                messages=[mcp_pb2.PromptMessage(
                                    role="assistant",
                                    content=mcp_pb2.ContentItem(type="text", text=text),
                                )],
                            ),
                        ))

                elif msg_type == "complete":
                    req = envelope.complete
                    comp = self._server._completions.get(req.ref.name)
                    if not comp:
                        await write_queue.put(mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            complete=mcp_pb2.CompleteResponse(values=[], has_more=False, total=0),
                        ))
                    else:
                        values = await comp.handler(req.argument.name, req.argument.value)
                        await write_queue.put(mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            complete=mcp_pb2.CompleteResponse(
                                values=values, has_more=False, total=len(values),
                            ),
                        ))

                elif msg_type == "ping":
                    await write_queue.put(mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        pong=mcp_pb2.PingResponse(),
                    ))

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
                    pass  # cancellation acknowledged but not acted on in sub-project 1

                elif msg_type == "subscribe_res":
                    uri = envelope.subscribe_res.uri
                    for handler in self._server._subscribe_handlers:
                        result = handler(uri)
                        if asyncio.iscoroutine(result):
                            await result

                else:
                    await write_queue.put(mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        error=mcp_pb2.ErrorResponse(
                            code=400, message=f"Unknown message type: {msg_type}",
                        ),
                    ))

            # Wait for any in-flight tool tasks before signalling writer to stop
            if _tool_tasks:
                await asyncio.gather(*_tool_tasks, return_exceptions=True)
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

    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        self._tools: dict[str, RegisteredTool] = {}
        self._resources: dict[str, RegisteredResource] = {}
        self._prompts: dict[str, RegisteredPrompt] = {}
        self._resource_templates: dict[str, RegisteredResourceTemplate] = {}
        self._completions: dict[str, RegisteredCompletion] = {}
        self._session_queues: list[asyncio.Queue] = []
        self._client_notification_handlers: dict[str, list[Callable]] = {}
        self._subscribe_handlers: list[Callable] = []

    def tool(self, *, description: str | None = None) -> Callable[[Callable], Callable]:
        def decorator(fn: Callable) -> Callable:
            desc = description or (fn.__doc__ or "").strip()
            sig = inspect.signature(fn)
            needs_ctx = any(
                p.annotation is Context
                for p in sig.parameters.values()
            )
            self._tools[fn.__name__] = RegisteredTool(
                name=fn.__name__,
                description=desc,
                input_schema=_build_input_schema(fn),
                handler=fn,
                needs_context=needs_ctx,
            )
            return fn

        return decorator

    def resource(
        self, uri: str, *, description: str | None = None, mime_type: str = "text/plain",
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
        self, uri_template: str, *, description: str | None = None, mime_type: str = "text/plain",
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
        self._broadcast(mcp_pb2.ServerNotification(
            type=mcp_pb2.ServerNotification.TOOLS_LIST_CHANGED,
        ))

    def notify_resources_list_changed(self) -> None:
        self._broadcast(mcp_pb2.ServerNotification(
            type=mcp_pb2.ServerNotification.RESOURCES_LIST_CHANGED,
        ))

    def notify_resource_updated(self, uri: str) -> None:
        self._broadcast(mcp_pb2.ServerNotification(
            type=mcp_pb2.ServerNotification.RESOURCE_UPDATED,
            payload=json.dumps({"uri": uri}),
        ))

    def notify_prompts_list_changed(self) -> None:
        self._broadcast(mcp_pb2.ServerNotification(
            type=mcp_pb2.ServerNotification.PROMPTS_LIST_CHANGED,
        ))

    async def handle_call_tool(
        self, name: str, arguments_json: str, context: Context | None = None,
    ) -> mcp_pb2.CallToolResponse:
        tool = self._tools.get(name)
        if not tool:
            raise McpError(code=404, message=f"Tool '{name}' not found")
        args = json.loads(arguments_json) if arguments_json else {}
        if tool.needs_context and context is not None:
            sig = inspect.signature(tool.handler)
            for param_name, param in sig.parameters.items():
                if param.annotation is Context:
                    args[param_name] = context
                    break
        try:
            result = await tool.handler(**args)
            if isinstance(result, str):
                content = [mcp_pb2.ContentItem(type="text", text=result)]
            else:
                content = [mcp_pb2.ContentItem(type="text", text=str(result))]
            return mcp_pb2.CallToolResponse(content=content, is_error=False)
        except Exception as e:
            return mcp_pb2.CallToolResponse(
                content=[mcp_pb2.ContentItem(type="text", text=str(e))],
                is_error=True,
            )

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
