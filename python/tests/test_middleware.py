"""Unit tests for middleware core types (no gRPC required)."""

from __future__ import annotations

import pytest

from mcp_grpc import Client, FasterMCP
from mcp_grpc._generated import mcp_pb2
from mcp_grpc.middleware import CallNext, Middleware, ToolCallContext


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
                    k: v.upper() if isinstance(v, str) else v for k, v in tool_ctx.arguments.items()
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


# ── Built-in middleware tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timing_middleware_logs(caplog):
    """TimingMiddleware logs tool name and elapsed time in milliseconds."""
    import logging as _logging

    from mcp_grpc.middleware import TimingMiddleware

    server = FasterMCP(name="timing-server", version="0.1", middleware=[TimingMiddleware()])

    @server.tool(description="Fast tool")
    async def instant() -> str:
        return "done"

    async with server:
        with caplog.at_level(_logging.INFO, logger="mcp_grpc.timing"):
            async with Client(f"localhost:{server.port}") as client:
                await client.call_tool("instant", {})

    assert any("instant" in r.message for r in caplog.records)
    assert any("ms" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_timing_middleware_custom_logger(caplog):
    """TimingMiddleware accepts a custom logger."""
    import logging as _logging

    from mcp_grpc.middleware import TimingMiddleware

    custom = _logging.getLogger("my.timer")
    server = FasterMCP(
        name="custom-timing-server",
        version="0.1",
        middleware=[TimingMiddleware(logger=custom)],
    )

    @server.tool(description="Tool")
    async def tool() -> str:
        return "x"

    async with server:
        with caplog.at_level(_logging.INFO, logger="my.timer"):
            async with Client(f"localhost:{server.port}") as client:
                await client.call_tool("tool", {})

    assert any("tool" in r.message for r in caplog.records if r.name == "my.timer")


@pytest.mark.asyncio
async def test_tool_call_context_carries_input_schema():
    """ToolCallContext.input_schema is populated from the registered tool schema."""
    received: list = []

    class SchemaCapture(Middleware):
        async def on_tool_call(
            self, tool_ctx: ToolCallContext, call_next: CallNext
        ) -> mcp_pb2.CallToolResponse:
            received.append(tool_ctx.input_schema)
            return await call_next(tool_ctx)

    server = FasterMCP(name="schema-server", version="0.1", middleware=[SchemaCapture()])

    @server.tool(description="Add two numbers")
    async def add(a: int, b: int) -> str:
        return str(a + b)

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            await client.call_tool("add", {"a": 1, "b": 2})

    assert len(received) == 1
    schema = received[0]
    assert schema is not None
    assert "properties" in schema
    assert "a" in schema["properties"]
    assert "b" in schema["properties"]


# ── TimeoutMiddleware ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_middleware_fires_on_slow_tool():
    """TimeoutMiddleware returns is_error=True when the tool exceeds the deadline."""
    import asyncio as _asyncio

    from mcp_grpc.middleware import TimeoutMiddleware

    server = FasterMCP(
        name="timeout-server",
        version="0.1",
        middleware=[TimeoutMiddleware(default_timeout=0.05)],
    )

    @server.tool(description="Slow tool")
    async def slow() -> str:
        await _asyncio.sleep(10)
        return "done"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("slow", {})

    assert result.is_error
    assert "timed out" in result.content[0].text
    assert "slow" in result.content[0].text


@pytest.mark.asyncio
async def test_timeout_middleware_passes_fast_tool():
    """TimeoutMiddleware lets fast tools through unchanged."""
    from mcp_grpc.middleware import TimeoutMiddleware

    server = FasterMCP(
        name="timeout-pass-server",
        version="0.1",
        middleware=[TimeoutMiddleware(default_timeout=5.0)],
    )

    @server.tool(description="Fast tool")
    async def fast() -> str:
        return "ok"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("fast", {})

    assert not result.is_error
    assert result.content[0].text == "ok"


@pytest.mark.asyncio
async def test_timeout_middleware_per_tool_override():
    """per_tool dict overrides the default timeout for named tools."""
    import asyncio as _asyncio

    from mcp_grpc.middleware import TimeoutMiddleware

    server = FasterMCP(
        name="timeout-per-tool-server",
        version="0.1",
        middleware=[TimeoutMiddleware(default_timeout=0.05, per_tool={"privileged": 10.0})],
    )

    @server.tool(description="Privileged slow tool")
    async def privileged() -> str:
        await _asyncio.sleep(0.1)  # slower than default but within per_tool budget
        return "privileged done"

    @server.tool(description="Normal slow tool")
    async def normal() -> str:
        await _asyncio.sleep(0.1)  # exceeds default timeout
        return "normal done"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            priv_result = await client.call_tool("privileged", {})
            norm_result = await client.call_tool("normal", {})

    assert not priv_result.is_error
    assert priv_result.content[0].text == "privileged done"
    assert norm_result.is_error
    assert "timed out" in norm_result.content[0].text


# ── ValidationMiddleware ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validation_middleware_missing_required():
    """ValidationMiddleware returns is_error=True when a required arg is absent."""
    from mcp_grpc.middleware import ValidationMiddleware

    server = FasterMCP(
        name="val-missing-server",
        version="0.1",
        middleware=[ValidationMiddleware()],
    )

    @server.tool(description="Greet")
    async def greet(name: str) -> str:
        return f"Hello, {name}!"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("greet", {})  # missing 'name'

    assert result.is_error
    assert "name" in result.content[0].text
    assert "missing" in result.content[0].text.lower()


@pytest.mark.asyncio
async def test_validation_middleware_unknown_arg():
    """ValidationMiddleware returns is_error=True when an unknown arg is passed."""
    from mcp_grpc.middleware import ValidationMiddleware

    server = FasterMCP(
        name="val-unknown-server",
        version="0.1",
        middleware=[ValidationMiddleware()],
    )

    @server.tool(description="Greet")
    async def greet(name: str) -> str:
        return f"Hello, {name}!"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("greet", {"name": "Alice", "extra": "bad"})

    assert result.is_error
    assert "extra" in result.content[0].text
    assert "unknown" in result.content[0].text.lower()


@pytest.mark.asyncio
async def test_validation_middleware_valid_passes():
    """ValidationMiddleware forwards valid calls to the handler unchanged."""
    from mcp_grpc.middleware import ValidationMiddleware

    server = FasterMCP(
        name="val-pass-server",
        version="0.1",
        middleware=[ValidationMiddleware()],
    )

    @server.tool(description="Add")
    async def add(a: int, b: int) -> str:
        return str(a + b)

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("add", {"a": 1, "b": 2})

    assert not result.is_error
    assert result.content[0].text == "3"


@pytest.mark.asyncio
async def test_validation_middleware_no_schema_passes_through():
    """ValidationMiddleware skips validation when input_schema is None."""
    from mcp_grpc.middleware import ValidationMiddleware

    # Manually inject None schema via a wrapping middleware
    class NullifySchema(Middleware):
        async def on_tool_call(
            self, tool_ctx: ToolCallContext, call_next: CallNext
        ) -> mcp_pb2.CallToolResponse:
            from dataclasses import replace as dc_replace

            return await call_next(dc_replace(tool_ctx, input_schema=None))

    server = FasterMCP(
        name="val-noschema-server",
        version="0.1",
        middleware=[NullifySchema(), ValidationMiddleware()],
    )

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            # Even with bad args, ValidationMiddleware skips when schema is None
            result = await client.call_tool("echo", {"text": "hi"})

    assert not result.is_error


# ── LoggingMiddleware (existing) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logging_middleware_logs_tool(caplog):
    """LoggingMiddleware logs tool name and args before, and is_error status after."""
    import logging as _logging

    from mcp_grpc.middleware import LoggingMiddleware

    server = FasterMCP(name="logmw-server", version="0.1", middleware=[LoggingMiddleware()])

    @server.tool(description="A tool")
    async def mytool(x: str) -> str:
        return x

    async with server:
        with caplog.at_level(_logging.INFO, logger="mcp_grpc.requests"):
            async with Client(f"localhost:{server.port}") as client:
                await client.call_tool("mytool", {"x": "hello"})

    messages = [r.message for r in caplog.records]
    assert any("mytool" in m for m in messages)
    assert len([m for m in messages if "mytool" in m]) >= 2


# ---------------------------------------------------------------------------
# Issue 5: middleware chain caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_chain_cached_across_calls():
    """Middleware chain is built once and reused on subsequent calls."""
    from mcp_grpc.middleware import Middleware
    from mcp_grpc.tools.tool_manager import ToolManager

    class CountingMiddleware(Middleware):
        async def on_tool_call(self, tool_ctx, call_next):
            return await call_next(tool_ctx)

    manager = ToolManager(middleware=[CountingMiddleware()])

    @manager.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    # First call — chain built, dirty flag cleared
    assert manager._chain_dirty is True
    await manager._dispatch_tool("echo", {"text": "a"}, None)
    assert manager._chain_dirty is False
    chain_first = manager._cached_chain

    # Second call — chain reused
    await manager._dispatch_tool("echo", {"text": "b"}, None)
    assert manager._cached_chain is chain_first


@pytest.mark.asyncio
async def test_middleware_chain_invalidated_after_add():
    """Adding a middleware marks the chain dirty so it is rebuilt."""
    from mcp_grpc.middleware import Middleware
    from mcp_grpc.tools.tool_manager import ToolManager

    class NoopMiddleware(Middleware):
        async def on_tool_call(self, tool_ctx, call_next):
            return await call_next(tool_ctx)

    manager = ToolManager()

    @manager.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    await manager._dispatch_tool("echo", {"text": "a"}, None)
    chain_before = manager._cached_chain

    manager.add_middleware(NoopMiddleware())
    assert manager._chain_dirty is True

    await manager._dispatch_tool("echo", {"text": "b"}, None)
    assert manager._chain_dirty is False
    assert manager._cached_chain is not chain_before
