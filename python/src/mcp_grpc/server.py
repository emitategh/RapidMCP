"""McpServer: register tools, resources, prompts and serve them over gRPC."""
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


@dataclass
class RegisteredTool:
    name: str
    description: str
    input_schema: str
    handler: Callable[..., Awaitable[Any]]


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


def _build_input_schema(fn: Callable) -> str:
    """Build a JSON Schema from function type hints."""
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}

    for param_name, param in sig.parameters.items():
        annotation = param.annotation
        json_type = type_map.get(annotation, "string")
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return json.dumps(schema)


class _McpServicer(mcp_pb2_grpc.McpServicer):
    """Handles bidi Session stream."""

    def __init__(self, server: McpServer) -> None:
        self._server = server

    async def Session(self, request_iterator, context):
        async for envelope in request_iterator:
            rid = envelope.request_id
            msg_type = envelope.WhichOneof("message")

            if msg_type == "initialize":
                yield mcp_pb2.ServerEnvelope(
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

            elif msg_type == "initialized":
                pass

            elif msg_type == "list_tools":
                tools = [
                    mcp_pb2.ToolDefinition(
                        name=t.name,
                        description=t.description,
                        input_schema=t.input_schema,
                    )
                    for t in self._server._tools.values()
                ]
                yield mcp_pb2.ServerEnvelope(
                    request_id=rid,
                    list_tools=mcp_pb2.ListToolsResponse(tools=tools),
                )

            elif msg_type == "call_tool":
                req = envelope.call_tool
                try:
                    result = await self._server.handle_call_tool(req.name, req.arguments)
                    yield mcp_pb2.ServerEnvelope(request_id=rid, call_tool=result)
                except McpError as e:
                    yield mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        error=mcp_pb2.ErrorResponse(code=e.code, message=e.message),
                    )

            elif msg_type == "list_resources":
                resources = [
                    mcp_pb2.ResourceDefinition(
                        uri=r.uri,
                        name=r.name,
                        description=r.description,
                        mime_type=r.mime_type,
                    )
                    for r in self._server._resources.values()
                ]
                yield mcp_pb2.ServerEnvelope(
                    request_id=rid,
                    list_resources=mcp_pb2.ListResourcesResponse(resources=resources),
                )

            elif msg_type == "read_resource":
                uri = envelope.read_resource.uri
                res = self._server._resources.get(uri)
                if not res:
                    yield mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        error=mcp_pb2.ErrorResponse(
                            code=404, message=f"Resource '{uri}' not found"
                        ),
                    )
                else:
                    text = await res.handler()
                    yield mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        read_resource=mcp_pb2.ReadResourceResponse(
                            content=[mcp_pb2.ContentItem(type="text", text=text)],
                        ),
                    )

            elif msg_type == "list_prompts":
                prompts = [
                    mcp_pb2.PromptDefinition(
                        name=p.name,
                        description=p.description,
                        arguments=[mcp_pb2.PromptArgument(**a) for a in p.arguments],
                    )
                    for p in self._server._prompts.values()
                ]
                yield mcp_pb2.ServerEnvelope(
                    request_id=rid,
                    list_prompts=mcp_pb2.ListPromptsResponse(prompts=prompts),
                )

            elif msg_type == "get_prompt":
                req = envelope.get_prompt
                prompt = self._server._prompts.get(req.name)
                if not prompt:
                    yield mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        error=mcp_pb2.ErrorResponse(
                            code=404, message=f"Prompt '{req.name}' not found"
                        ),
                    )
                else:
                    text = await prompt.handler(**dict(req.arguments))
                    yield mcp_pb2.ServerEnvelope(
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

            elif msg_type == "ping":
                yield mcp_pb2.ServerEnvelope(
                    request_id=rid,
                    pong=mcp_pb2.PingResponse(),
                )

            else:
                yield mcp_pb2.ServerEnvelope(
                    request_id=rid,
                    error=mcp_pb2.ErrorResponse(
                        code=400, message=f"Unknown message type: {msg_type}"
                    ),
                )


class McpServer:
    """Register tools, resources, and prompts, then serve over gRPC."""

    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        self._tools: dict[str, RegisteredTool] = {}
        self._resources: dict[str, RegisteredResource] = {}
        self._prompts: dict[str, RegisteredPrompt] = {}

    def tool(self, description: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            self._tools[fn.__name__] = RegisteredTool(
                name=fn.__name__,
                description=description,
                input_schema=_build_input_schema(fn),
                handler=fn,
            )
            return fn

        return decorator

    def resource(self, uri: str, description: str, mime_type: str = "text/plain") -> Callable:
        def decorator(fn: Callable) -> Callable:
            self._resources[uri] = RegisteredResource(
                uri=uri,
                name=fn.__name__,
                description=description,
                mime_type=mime_type,
                handler=fn,
            )
            return fn

        return decorator

    def prompt(self, description: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
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
                description=description,
                arguments=arguments,
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

    async def handle_call_tool(self, name: str, arguments_json: str) -> mcp_pb2.CallToolResponse:
        tool = self._tools.get(name)
        if not tool:
            raise McpError(code=404, message=f"Tool '{name}' not found")
        args = json.loads(arguments_json) if arguments_json else {}
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
            print(f"McpServer '{self.name}' listening on port {self._port}", flush=True)
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
