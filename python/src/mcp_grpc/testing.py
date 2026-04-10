"""Test helpers: InProcessChannel wires client to server without a socket."""
from __future__ import annotations

import json
from typing import Any

from mcp_grpc._generated import mcp_pb2
from mcp_grpc.client import ServerInfo
from mcp_grpc.errors import McpError
from mcp_grpc.server import McpServer, _McpServicer
from mcp_grpc.session import PendingRequests


class InProcessChannel:
    """Connect a client directly to a McpServer in-memory (no socket)."""

    def __init__(self, server: McpServer) -> None:
        self._server = server
        self._servicer = _McpServicer(server)
        self._client = _InProcessClient(self._servicer)

    async def __aenter__(self) -> _InProcessClient:
        await self._client._initialize()
        return self._client

    async def __aexit__(self, *exc) -> None:
        pass


class _InProcessClient:
    """Lightweight client that calls the servicer directly."""

    def __init__(self, servicer: _McpServicer) -> None:
        self._servicer = servicer
        self._pending = PendingRequests()
        self.server_info: ServerInfo | None = None

    async def _roundtrip(self, envelope: mcp_pb2.ClientEnvelope) -> Any:
        rid = self._pending.next_id()
        envelope.request_id = rid

        async def _single_request():
            yield envelope

        response = None
        async for resp in self._servicer.Session(_single_request(), context=None):
            if resp.request_id == rid:
                msg_type = resp.WhichOneof("message")
                if msg_type == "error":
                    err = resp.error
                    raise McpError(err.code, err.message)
                response = getattr(resp, msg_type)
                break
        return response

    async def _initialize(self) -> None:
        env = mcp_pb2.ClientEnvelope(
            initialize=mcp_pb2.InitializeRequest(
                client_name="test-client",
                client_version="0.1.0",
                capabilities=mcp_pb2.ClientCapabilities(),
            ),
        )
        resp = await self._roundtrip(env)
        self.server_info = ServerInfo(
            server_name=resp.server_name,
            server_version=resp.server_version,
            capabilities=resp.capabilities,
        )

    async def list_tools(self) -> list[mcp_pb2.ToolDefinition]:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(list_tools=mcp_pb2.ListToolsRequest())
        )
        return list(resp.tools)

    async def call_tool(
        self, name: str, arguments: dict | None = None
    ) -> mcp_pb2.CallToolResponse:
        return await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                call_tool=mcp_pb2.CallToolRequest(
                    name=name,
                    arguments=json.dumps(arguments or {}),
                ),
            )
        )

    async def list_resources(self) -> list[mcp_pb2.ResourceDefinition]:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(list_resources=mcp_pb2.ListResourcesRequest())
        )
        return list(resp.resources)

    async def read_resource(self, uri: str) -> mcp_pb2.ReadResourceResponse:
        return await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                read_resource=mcp_pb2.ReadResourceRequest(uri=uri),
            )
        )

    async def list_prompts(self) -> list[mcp_pb2.PromptDefinition]:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(list_prompts=mcp_pb2.ListPromptsRequest())
        )
        return list(resp.prompts)

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> mcp_pb2.GetPromptResponse:
        return await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                get_prompt=mcp_pb2.GetPromptRequest(name=name, arguments=arguments or {}),
            )
        )

    async def list_resource_templates(self) -> list[mcp_pb2.ResourceTemplateDefinition]:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                list_resource_templates=mcp_pb2.ListResourceTemplatesRequest()
            )
        )
        return list(resp.templates)

    async def ping(self) -> None:
        await self._roundtrip(mcp_pb2.ClientEnvelope(ping=mcp_pb2.PingRequest()))
