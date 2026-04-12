"""Test helpers: InProcessChannel wires client to server without a socket."""

from __future__ import annotations

import json
from typing import Any

from fastermcp._generated import mcp_pb2
from fastermcp.errors import McpError
from fastermcp.server import FasterMCP, _McpServicer
from fastermcp.session import PendingRequests
from fastermcp.types import (
    CallToolResult,
    CompleteResult,
    GetPromptResult,
    ListResult,
    ReadResourceResult,
    ServerInfo,
    _convert_call_tool_result,
    _convert_complete_result,
    _convert_get_prompt_result,
    _convert_prompt,
    _convert_read_resource_result,
    _convert_resource,
    _convert_resource_template,
    _convert_tool,
)


class _AsyncMessageIter:
    """Async iterator over a fixed list of ClientEnvelope messages.

    Raises StopAsyncIteration after the last message, which causes the
    Session generator's read_task to set eof=True and drain the write queue.
    """

    def __init__(self, messages: list[mcp_pb2.ClientEnvelope]) -> None:
        self._iter = iter(messages)

    def __aiter__(self) -> _AsyncMessageIter:
        return self

    async def __anext__(self) -> mcp_pb2.ClientEnvelope:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


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

    async def _roundtrip(self, *envelopes: mcp_pb2.ClientEnvelope) -> Any:
        """Run a short session with the given envelopes; return the first response."""
        rid = self._pending.next_id()
        envelopes[0].request_id = rid

        request_iter = _AsyncMessageIter(list(envelopes))
        responses: list[mcp_pb2.ServerEnvelope] = []
        async for resp in self._servicer.Session(request_iter, None):
            responses.append(resp)

        for resp in responses:
            if resp.request_id == rid:
                msg_type = resp.WhichOneof("message")
                if msg_type == "error":
                    err = resp.error
                    raise McpError(err.code, err.message)
                return getattr(resp, msg_type)
        raise RuntimeError(f"No response with request_id={rid} in session responses")

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
        return ListResult(
            items=[_convert_tool(t) for t in resp.tools],
            next_cursor=resp.next_cursor or None,
        )

    async def call_tool(self, name: str, arguments: dict | None = None) -> CallToolResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                call_tool=mcp_pb2.CallToolRequest(
                    name=name,
                    arguments=json.dumps(arguments or {}),
                ),
            )
        )
        return _convert_call_tool_result(resp)

    async def list_resources(self) -> ListResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(list_resources=mcp_pb2.ListResourcesRequest())
        )
        return ListResult(
            items=[_convert_resource(r) for r in resp.resources],
            next_cursor=resp.next_cursor or None,
        )

    async def read_resource(self, uri: str) -> ReadResourceResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                read_resource=mcp_pb2.ReadResourceRequest(uri=uri),
            )
        )
        return _convert_read_resource_result(resp)

    async def list_prompts(self) -> ListResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(list_prompts=mcp_pb2.ListPromptsRequest())
        )
        return ListResult(
            items=[_convert_prompt(p) for p in resp.prompts],
            next_cursor=resp.next_cursor or None,
        )

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> GetPromptResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                get_prompt=mcp_pb2.GetPromptRequest(name=name, arguments=arguments or {}),
            )
        )
        return _convert_get_prompt_result(resp)

    async def list_resource_templates(self) -> ListResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(list_resource_templates=mcp_pb2.ListResourceTemplatesRequest())
        )
        return ListResult(
            items=[_convert_resource_template(t) for t in resp.templates],
            next_cursor=resp.next_cursor or None,
        )

    async def complete(
        self,
        ref_type: str,
        ref_name: str,
        argument_name: str,
        value: str,
    ) -> CompleteResult:
        resp = await self._roundtrip(
            mcp_pb2.ClientEnvelope(
                complete=mcp_pb2.CompleteRequest(
                    ref=mcp_pb2.CompletionRef(type=ref_type, name=ref_name),
                    argument=mcp_pb2.CompletionArg(name=argument_name, value=value),
                )
            )
        )
        return _convert_complete_result(resp)

    async def ping(self) -> bool:
        await self._roundtrip(mcp_pb2.ClientEnvelope(ping=mcp_pb2.PingRequest()))
        return True

    async def cancel(self, target_request_id: int) -> None:
        """Send a cancel notification then ping to confirm the session is live."""
        cancel_env = mcp_pb2.ClientEnvelope(
            request_id=0,
            cancel=mcp_pb2.CancelRequest(target_request_id=target_request_id),
        )
        ping_env = mcp_pb2.ClientEnvelope(
            request_id=999999,
            ping=mcp_pb2.PingRequest(),
        )
        request_iter = _AsyncMessageIter([cancel_env, ping_env])
        async for _ in self._servicer.Session(request_iter, None):
            pass
