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


async def test_initialize_is_concurrency_safe() -> None:
    """Two concurrent initialize() calls must not both call Client.connect()."""
    import asyncio

    server = _make_server()
    async with InProcessChannel(server) as chan:
        adapter = MCPServerGRPC.__new__(MCPServerGRPC)
        from rapidmcp.integrations.livekit import MCPServer
        MCPServer.__init__(adapter, client_session_timeout_seconds=30)
        adapter._address = "in-process"

        # Count connect() calls via a wrapper.
        real = chan
        calls = 0
        orig_connect = real.connect if hasattr(real, "connect") else None

        async def counting_connect():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)  # widen the race window
            if orig_connect is not None:
                return await orig_connect()

        real.connect = counting_connect  # type: ignore[method-assign]

        adapter._grpc_client = real
        adapter._allowed_tools = None
        adapter._connected = False

        await asyncio.gather(adapter.initialize(), adapter.initialize())
        assert calls == 1, f"expected one connect() call, got {calls}"
        assert adapter.initialized is True


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


async def test_tool_error_message_includes_non_text_parts() -> None:
    """When a tool's error payload includes non-text content, the ToolError
    message must still convey that something was there (not be silently dropped)."""
    from rapidmcp.types import CallToolResult, ContentItem
    from livekit.agents.llm.mcp import MCPTool
    from livekit.agents.llm.tool_context import ToolError as LKToolError

    # Build a fake error result that contains an image part (non-text)
    error_result = CallToolResult(
        is_error=True,
        content=[
            ContentItem(type="text", text="pre-text"),
            ContentItem(type="image", data=b"\x00\x01", mime_type="image/png"),
        ],
    )

    app = RapidMCP(name="t", version="0")

    @app.tool()
    async def boom() -> str:
        """A tool that always errors."""
        raise RuntimeError("won't matter — we mock the result")

    async with _grpc_adapter_for(app) as grpc:
        # Patch the underlying gRPC client to return our crafted error result
        real_call = grpc._grpc_client.call_tool

        async def _fake_call(name, arguments):
            return error_result

        grpc._grpc_client.call_tool = _fake_call  # type: ignore[method-assign]

        tools = await grpc.list_tools()
        # Reset grpc_client patching for call_tool but keep list from cache
        grpc._grpc_client.call_tool = _fake_call  # type: ignore[method-assign]

        boom_tool = next(t for t in tools if t.info.name == "boom")
        with pytest.raises(LKToolError) as exc_info:
            await boom_tool(raw_arguments={})

        msg = str(exc_info.value)
        assert "pre-text" in msg
        # The image part should surface as *something* — not be silently dropped.
        assert "image" in msg or "bytes" in msg or len(msg) > len("pre-text")


async def test_list_tools_and_call_tool() -> None:
    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:
        tools = await grpc.list_tools()
        names = sorted(t.info.name for t in tools)
        assert names == ["add", "echo"]

        add_tool = next(t for t in tools if t.info.name == "add")
        result = await add_tool(raw_arguments={"a": 17, "b": 25})
        assert "42" in str(result)
