"""Test helpers: InProcessChannel wires client to server without a socket."""

from __future__ import annotations

import json
from typing import Any

from mcp_grpc._generated import mcp_pb2
from mcp_grpc.client import ListResult, ServerInfo
from mcp_grpc.errors import McpError
from mcp_grpc.server import FasterMCP, _McpServicer
from mcp_grpc.session import PendingRequests


class InProcessChannel:
    """Connect a client directly to a FasterMCP server in-memory (no socket)."""

    def __init__(self, server: FasterMCP) -> None:
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

    async def list_tools(self) -> ListResult:
        resp = await self._roundtrip(mcp_pb2.ClientEnvelope(list_tools=mcp_pb2.ListToolsRequest()))
        return ListResult(items=list(resp.tools), next_cursor=resp.next_cursor or None)

    async def call_tool(self, name: str, arguments: dict | None = None) -> mcp_pb2.CallToolResponse:
        return await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                call_tool=mcp_pb2.CallToolRequest(
                    name=name,
                    arguments=json.dumps(arguments or {}),
                ),
            )
        )

    async def list_resources(self) -> ListResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(list_resources=mcp_pb2.ListResourcesRequest())
        )
        return ListResult(items=list(resp.resources), next_cursor=resp.next_cursor or None)

    async def read_resource(self, uri: str) -> mcp_pb2.ReadResourceResponse:
        return await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                read_resource=mcp_pb2.ReadResourceRequest(uri=uri),
            )
        )

    async def list_prompts(self) -> ListResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(list_prompts=mcp_pb2.ListPromptsRequest())
        )
        return ListResult(items=list(resp.prompts), next_cursor=resp.next_cursor or None)

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> mcp_pb2.GetPromptResponse:
        return await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                get_prompt=mcp_pb2.GetPromptRequest(name=name, arguments=arguments or {}),
            )
        )

    async def list_resource_templates(self) -> ListResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(list_resource_templates=mcp_pb2.ListResourceTemplatesRequest())
        )
        return ListResult(items=list(resp.templates), next_cursor=resp.next_cursor or None)

    async def complete(
        self,
        ref_type: str,
        ref_name: str,
        argument_name: str,
        value: str,
    ) -> mcp_pb2.CompleteResponse:
        return await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                complete=mcp_pb2.CompleteRequest(
                    ref=mcp_pb2.CompletionRef(type=ref_type, name=ref_name),
                    argument=mcp_pb2.CompletionArg(name=argument_name, value=value),
                )
            )
        )

    async def ping(self) -> None:
        await self._roundtrip(mcp_pb2.ClientEnvelope(ping=mcp_pb2.PingRequest()))

    async def cancel(self, target_request_id: int) -> None:
        """Send a cancel notification (fire-and-forget, no response expected)."""
        env = mcp_pb2.ClientEnvelope(
            request_id=0,
            cancel=mcp_pb2.CancelRequest(target_request_id=target_request_id),
        )

        async def _single():
            yield env
            # Send a ping immediately after so the session produces a response
            # we can break on, preventing the loop from hanging.
            ping_env = mcp_pb2.ClientEnvelope(
                request_id=999999,
                ping=mcp_pb2.PingRequest(),
            )
            yield ping_env

        async for resp in self._servicer.Session(_single(), context=None):
            if resp.WhichOneof("message") == "pong":
                break
