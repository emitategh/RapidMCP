"""Unit tests for middleware core types (no gRPC required)."""
from __future__ import annotations

import asyncio

import pytest

from mcp_grpc._generated import mcp_pb2
from mcp_grpc.middleware import CallNext, Middleware, ToolCallContext
from mcp_grpc import Client, FasterMCP


def _ok(text: str) -> mcp_pb2.CallToolResponse:
    return mcp_pb2.CallToolResponse(
        content=[mcp_pb2.ContentItem(type="text", text=text)],
        is_error=False,
    )


def test_tool_call_context_fields():
    """ToolCallContext stores tool_name, arguments, and ctx."""
    tc = ToolCallContext(tool_name="add", arguments={"a": 1, "b": 2}, ctx=None)
    assert tc.tool_name == "add"
    assert tc.arguments == {"a": 1, "b": 2}
    assert tc.ctx is None


@pytest.mark.asyncio
async def test_base_middleware_passes_through():
    """Default Middleware.on_tool_call forwards to call_next unchanged."""
    mw = Middleware()
    tc = ToolCallContext(tool_name="echo", arguments={"text": "hi"}, ctx=None)
    expected = _ok("hi")

    async def call_next(t: ToolCallContext) -> mcp_pb2.CallToolResponse:
        assert t is tc
        return expected

    result = await mw.on_tool_call(tc, call_next)
    assert result is expected


# ── Integration tests (real gRPC loopback) ──────────────────────────────────


@pytest.mark.asyncio
async def test_middleware_intercepts_tool_call():
    """Registered middleware on_tool_call runs before and after the handler."""
    calls: list = []

    class RecordingMiddleware(Middleware):
        async def on_tool_call(
            self, tool_ctx: ToolCallContext, call_next: CallNext
        ) -> mcp_pb2.CallToolResponse:
            calls.append(("before", tool_ctx.tool_name, dict(tool_ctx.arguments)))
            result = await call_next(tool_ctx)
            calls.append(("after", tool_ctx.tool_name))
            return result

    server = FasterMCP(name="mw-server", version="0.1", middleware=[RecordingMiddleware()])

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("echo", {"text": "hello"})
            assert result.content[0].text == "hello"
            assert not result.is_error

    assert calls == [
        ("before", "echo", {"text": "hello"}),
        ("after", "echo"),
    ]


@pytest.mark.asyncio
async def test_middleware_can_modify_arguments():
    """Middleware can replace the arguments dict before the tool runs."""

    class UppercaseMiddleware(Middleware):
        async def on_tool_call(
            self, tool_ctx: ToolCallContext, call_next: CallNext
        ) -> mcp_pb2.CallToolResponse:
            modified = ToolCallContext(
                tool_name=tool_ctx.tool_name,
                arguments={
                    k: v.upper() if isinstance(v, str) else v
                    for k, v in tool_ctx.arguments.items()
                },
                ctx=tool_ctx.ctx,
            )
            return await call_next(modified)

    server = FasterMCP(name="upper-server", version="0.1", middleware=[UppercaseMiddleware()])

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("echo", {"text": "hello"})
            assert result.content[0].text == "HELLO"


@pytest.mark.asyncio
async def test_middleware_chain_order():
    """Multiple middleware: first-registered runs outermost (first before, last after)."""
    log: list[str] = []

    class MwA(Middleware):
        async def on_tool_call(
            self, tool_ctx: ToolCallContext, call_next: CallNext
        ) -> mcp_pb2.CallToolResponse:
            log.append("A:before")
            result = await call_next(tool_ctx)
            log.append("A:after")
            return result

    class MwB(Middleware):
        async def on_tool_call(
            self, tool_ctx: ToolCallContext, call_next: CallNext
        ) -> mcp_pb2.CallToolResponse:
            log.append("B:before")
            result = await call_next(tool_ctx)
            log.append("B:after")
            return result

    server = FasterMCP(name="chain-server", version="0.1", middleware=[MwA(), MwB()])

    @server.tool(description="Noop")
    async def noop() -> str:
        return "ok"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            await client.call_tool("noop", {})

    assert log == ["A:before", "B:before", "B:after", "A:after"]


@pytest.mark.asyncio
async def test_add_middleware_at_runtime():
    """add_middleware() appends to the chain after construction."""
    called: list[str] = []

    class TraceMiddleware(Middleware):
        async def on_tool_call(
            self, tool_ctx: ToolCallContext, call_next: CallNext
        ) -> mcp_pb2.CallToolResponse:
            called.append(tool_ctx.tool_name)
            return await call_next(tool_ctx)

    server = FasterMCP(name="runtime-mw-server", version="0.1")
    server.add_middleware(TraceMiddleware())

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            await client.call_tool("echo", {"text": "x"})

    assert called == ["echo"]


@pytest.mark.asyncio
async def test_no_middleware_unchanged():
    """Server with no middleware behaves identically to before (regression guard)."""
    server = FasterMCP(name="bare-server", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("echo", {"text": "plain"})
            assert result.content[0].text == "plain"
            assert not result.is_error
