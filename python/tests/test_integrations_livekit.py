"""Functional tests for MCPServerGRPC — LiveKit integration."""
from __future__ import annotations

import contextlib
from typing import Any

import pytest

pytest.importorskip("livekit.agents.llm.mcp")

from rapidmcp import RapidMCP
from rapidmcp.integrations.livekit import MCPServerGRPC
from rapidmcp.testing import InProcessChannel


def _make_server() -> RapidMCP:
    app = RapidMCP(name="test", version="0.0.1")

    @app.tool()
    async def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @app.tool()
    async def echo(text: str) -> str:
        """Echo text back."""
        return text

    return app


@contextlib.asynccontextmanager
async def _grpc_adapter_for(server: RapidMCP, **kwargs: Any):
    """Yield an MCPServerGRPC wired to an in-process RapidMCP server."""
    async with InProcessChannel(server) as chan:
        adapter = MCPServerGRPC.__new__(MCPServerGRPC)
        from rapidmcp.integrations.livekit import MCPServer
        MCPServer.__init__(
            adapter,
            client_session_timeout_seconds=kwargs.pop("timeout", 30),
            tool_result_resolver=kwargs.pop("tool_result_resolver", None),
        )
        adapter._address = "in-process"
        adapter._grpc_client = chan
        adapter._allowed_tools = kwargs.pop("allowed_tools", None)
        adapter._connected = True
        try:
            yield adapter
        finally:
            adapter._connected = False


async def test_list_tools_reuses_cache_until_invalidated() -> None:
    """list_tools should not re-hit the server when the cache is clean."""
    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:
        first = await grpc.list_tools()

        # Swap the underlying client for a sentinel — if the adapter hits it,
        # we'll know the cache wasn't used.
        class _Explode:
            async def list_tools(self):
                raise AssertionError("cache was bypassed — list_tools called a second time")
        grpc._grpc_client = _Explode()

        second = await grpc.list_tools()
        assert second is first  # same list object returned from cache

        # After invalidate, a fresh call must re-query — which now blows up.
        grpc.invalidate_cache()
        with pytest.raises(AssertionError, match="cache was bypassed"):
            await grpc.list_tools()


async def test_list_tools_and_call_tool() -> None:
    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:
        tools = await grpc.list_tools()
        names = sorted(t.info.name for t in tools)
        assert names == ["add", "echo"]

        add_tool = next(t for t in tools if t.info.name == "add")
        result = await add_tool(raw_arguments={"a": 17, "b": 25})
        assert "42" in str(result)
