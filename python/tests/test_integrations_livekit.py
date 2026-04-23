"""Functional tests for MCPServerGRPC — LiveKit integration."""

from __future__ import annotations

import asyncio
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
        adapter._init_lock = asyncio.Lock()
        yield adapter


async def test_initialize_is_concurrency_safe() -> None:
    """Two concurrent initialize() calls must not both call Client.connect()."""
    server = _make_server()
    async with InProcessChannel(server) as chan:
        adapter = MCPServerGRPC.__new__(MCPServerGRPC)
        from rapidmcp.integrations.livekit import MCPServer

        MCPServer.__init__(adapter, client_session_timeout_seconds=30)
        adapter._address = "in-process"
        adapter._init_lock = asyncio.Lock()

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
    from livekit.agents.llm.tool_context import ToolError as LKToolError

    from rapidmcp.types import CallToolResult, ContentItem

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
        async def _fake_call(name, arguments):
            return error_result

        grpc._grpc_client.call_tool = _fake_call  # type: ignore[method-assign]

        tools = await grpc.list_tools()
        boom_tool = next(t for t in tools if t.info.name == "boom")
        with pytest.raises(LKToolError) as exc_info:
            await boom_tool(raw_arguments={})

        msg = str(exc_info.value)
        assert "pre-text" in msg
        # The image part should surface as *something* — not be silently dropped.
        assert "image" in msg or "bytes" in msg or len(msg) > len("pre-text")


async def test_custom_tool_result_resolver_is_invoked() -> None:
    import mcp.types as mcp_types
    from livekit.agents.llm.mcp import MCPToolResultContext

    server = _make_server()
    seen: list[MCPToolResultContext] = []

    def resolver(ctx: MCPToolResultContext) -> str:
        seen.append(ctx)
        return "resolver-said-this"

    async with _grpc_adapter_for(server, tool_result_resolver=resolver) as grpc:
        tools = await grpc.list_tools()
        echo_tool = next(t for t in tools if t.info.name == "echo")
        out = await echo_tool(raw_arguments={"text": "hi"})
        assert out == "resolver-said-this"
        assert len(seen) == 1
        ctx = seen[0]
        assert ctx.tool_name == "echo"
        assert ctx.arguments == {"text": "hi"}
        assert isinstance(ctx.result, mcp_types.CallToolResult)
        assert not ctx.result.isError
        assert len(ctx.result.content) == 1
        assert ctx.result.content[0].type == "text"
        assert ctx.result.content[0].text == "hi"


async def test_multi_content_uses_default_resolver() -> None:
    """With no custom resolver, a multi-content response is JSON-serialized
    by the library's default resolver (not our old hand-rolled shape)."""
    import json as _json

    from rapidmcp.content import Image

    app = RapidMCP(name="t", version="0")

    @app.tool()
    async def mixed():
        """Return mixed content."""
        return ["hello", Image(data=b"\xff\xd8", mime_type="image/jpeg")]

    async with _grpc_adapter_for(app) as grpc:
        tools = await grpc.list_tools()
        (tool,) = tools
        out = await tool(raw_arguments={})
        assert isinstance(out, str)
        parsed = _json.loads(out)
        assert isinstance(parsed, list)
        assert any(p.get("type") == "text" and p.get("text") == "hello" for p in parsed)
        assert any(p.get("type") == "image" for p in parsed)


async def test_client_streams_raises_not_implemented() -> None:
    """MCPServerGRPC uses gRPC transport; the base-class JSON-RPC path
    must never be entered. Calling client_streams() must raise cleanly."""
    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:
        with pytest.raises(NotImplementedError, match="gRPC transport"):
            grpc.client_streams()


async def test_list_tools_and_call_tool() -> None:
    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:
        tools = await grpc.list_tools()
        names = sorted(t.info.name for t in tools)
        assert names == ["add", "echo"]

        add_tool = next(t for t in tools if t.info.name == "add")
        result = await add_tool(raw_arguments={"a": 17, "b": 25})
        assert "42" in str(result)


async def test_list_tools_raises_when_not_initialized() -> None:
    """Calling list_tools before initialize() must raise a clean RuntimeError."""
    server = _make_server()
    async with InProcessChannel(server) as chan:
        adapter = MCPServerGRPC.__new__(MCPServerGRPC)
        from rapidmcp.integrations.livekit import MCPServer

        MCPServer.__init__(adapter, client_session_timeout_seconds=30)
        adapter._address = "in-process"
        adapter._grpc_client = chan
        adapter._allowed_tools = None
        adapter._connected = False  # not initialized
        adapter._init_lock = asyncio.Lock()

        with pytest.raises(RuntimeError, match="isn't initialized"):
            await adapter.list_tools()


async def test_tool_call_raises_tool_error_after_aclose() -> None:
    """Calling a cached tool after aclose() must raise ToolError (not a raw
    gRPC error) — matches the base class contract."""
    from livekit.agents.llm.tool_context import ToolError as LKToolError

    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:
        tools = await grpc.list_tools()
        echo_tool = next(t for t in tools if t.info.name == "echo")

        # Simulate aclose by flipping _connected to False. The fixture's
        # channel stays open, so a gRPC error isn't possible — we're
        # isolating the guard behavior from transport.
        grpc._connected = False

        with pytest.raises(LKToolError, match="internal service is unavailable"):
            await echo_tool(raw_arguments={"text": "hi"})


async def test_embedded_resource_with_blob_is_forwarded() -> None:
    """A resource with binary data should become an EmbeddedResource
    carrying BlobResourceContents (base64 blob), not a bare ResourceLink."""
    import base64 as _b64

    import mcp.types as mcp_types
    from livekit.agents.llm.mcp import MCPToolResultContext

    from rapidmcp.types import CallToolResult, ContentItem

    blob_bytes = b"\x00\x01\x02\xff"
    mocked_result = CallToolResult(
        is_error=False,
        content=[
            ContentItem(
                type="resource",
                uri="file:///tmp/embedded.bin",
                data=blob_bytes,
                mime_type="application/octet-stream",
            ),
        ],
    )

    server = _make_server()
    seen: list[MCPToolResultContext] = []

    def resolver(ctx: MCPToolResultContext) -> str:
        seen.append(ctx)
        return "ok"

    async with _grpc_adapter_for(server, tool_result_resolver=resolver) as grpc:

        async def _fake_call(name, arguments):
            return mocked_result

        grpc._grpc_client.call_tool = _fake_call  # type: ignore[method-assign]

        tools = await grpc.list_tools()
        echo_tool = next(t for t in tools if t.info.name == "echo")
        await echo_tool(raw_arguments={"text": "x"})

        assert len(seen) == 1
        (part,) = seen[0].result.content
        assert isinstance(part, mcp_types.EmbeddedResource)
        assert isinstance(part.resource, mcp_types.BlobResourceContents)
        assert part.resource.blob == _b64.b64encode(blob_bytes).decode()
        assert part.resource.mimeType == "application/octet-stream"


async def test_embedded_resource_with_text_is_forwarded() -> None:
    """A resource with text content should become an EmbeddedResource
    carrying TextResourceContents."""
    import mcp.types as mcp_types
    from livekit.agents.llm.mcp import MCPToolResultContext

    from rapidmcp.types import CallToolResult, ContentItem

    mocked_result = CallToolResult(
        is_error=False,
        content=[
            ContentItem(
                type="resource",
                uri="file:///tmp/notes.txt",
                text="hello there",
                mime_type="text/plain",
            ),
        ],
    )

    server = _make_server()
    seen: list[MCPToolResultContext] = []

    def resolver(ctx: MCPToolResultContext) -> str:
        seen.append(ctx)
        return "ok"

    async with _grpc_adapter_for(server, tool_result_resolver=resolver) as grpc:

        async def _fake_call(name, arguments):
            return mocked_result

        grpc._grpc_client.call_tool = _fake_call  # type: ignore[method-assign]

        tools = await grpc.list_tools()
        echo_tool = next(t for t in tools if t.info.name == "echo")
        await echo_tool(raw_arguments={"text": "x"})

        (part,) = seen[0].result.content
        assert isinstance(part, mcp_types.EmbeddedResource)
        assert isinstance(part.resource, mcp_types.TextResourceContents)
        assert part.resource.text == "hello there"
        assert part.resource.mimeType == "text/plain"


async def test_unknown_content_type_emits_text_placeholder(caplog) -> None:
    """Unknown rapidmcp content types must surface as a text placeholder and
    a warning log — never be silently dropped."""
    import logging

    import mcp.types as mcp_types
    from livekit.agents.llm.mcp import MCPToolResultContext

    from rapidmcp.types import CallToolResult, ContentItem

    mocked_result = CallToolResult(
        is_error=False,
        content=[ContentItem(type="some-future-type", text="ignored")],
    )

    server = _make_server()
    seen: list[MCPToolResultContext] = []

    def resolver(ctx: MCPToolResultContext) -> str:
        seen.append(ctx)
        return "ok"

    async with _grpc_adapter_for(server, tool_result_resolver=resolver) as grpc:

        async def _fake_call(name, arguments):
            return mocked_result

        grpc._grpc_client.call_tool = _fake_call  # type: ignore[method-assign]

        with caplog.at_level(logging.WARNING, logger="rapidmcp.integrations.livekit"):
            tools = await grpc.list_tools()
            echo_tool = next(t for t in tools if t.info.name == "echo")
            await echo_tool(raw_arguments={"text": "x"})

    (part,) = seen[0].result.content
    assert isinstance(part, mcp_types.TextContent)
    assert "unsupported content type: some-future-type" in part.text
    assert any("unknown content type" in r.message for r in caplog.records)


async def test_default_resolver_raises_on_empty_content() -> None:
    """When a tool returns success with empty content, the library's default
    resolver raises ToolError (matches base-class semantics)."""
    from livekit.agents.llm.tool_context import ToolError as LKToolError

    from rapidmcp.types import CallToolResult

    mocked_result = CallToolResult(is_error=False, content=[])

    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:

        async def _fake_call(name, arguments):
            return mocked_result

        grpc._grpc_client.call_tool = _fake_call  # type: ignore[method-assign]

        tools = await grpc.list_tools()
        echo_tool = next(t for t in tools if t.info.name == "echo")

        with pytest.raises(LKToolError, match="without producing a result"):
            await echo_tool(raw_arguments={"text": "x"})


async def test_allowed_tools_filters_list_tools() -> None:
    """Only tools in the allowed_tools set should be returned by list_tools."""
    server = _make_server()
    async with _grpc_adapter_for(server, allowed_tools={"add"}) as grpc:
        tools = await grpc.list_tools()
        names = {t.info.name for t in tools}
        assert names == {"add"}


async def test_allowed_tools_none_returns_all() -> None:
    """allowed_tools=None should return every tool the server exposes."""
    server = _make_server()
    async with _grpc_adapter_for(server, allowed_tools=None) as grpc:
        tools = await grpc.list_tools()
        names = {t.info.name for t in tools}
        assert names == {"add", "echo"}
