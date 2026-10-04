"""Client(mode=...) chooses which protocol version it speaks."""

import asyncio

import pytest
from grpc import aio

from rapidmcp import Client, RapidMCP
from rapidmcp._generated import mcp_pb2_grpc
from rapidmcp._servicer import _McpServicer
from rapidmcp.errors import McpError


def _server(**kwargs) -> RapidMCP:
    srv = RapidMCP(name="dual", version="9.9", **kwargs)

    @srv.tool(description="Echo", read_only=True)
    async def echo(text: str) -> str:
        return text

    @srv.tool()
    async def plain() -> str:
        return "x"

    @srv.resource("res://a")
    async def a() -> str:
        return "a"

    @srv.resource_template("res://items/{item_id}")
    async def item(item_id: str) -> str:
        return item_id

    @srv.prompt()
    async def greet(name: str) -> str:
        return f"hi {name}"

    @srv.completion("greet")
    async def complete_greet(argument_name: str, value: str) -> list[str]:
        return [f"{value}lice"]

    return srv


async def test_modern_client_discovers_and_lists_over_v2():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        prompts = await client.list_prompts()
        completion = await client.complete("ref/prompt", "greet", "name", "A")

        assert client.protocol == "v2"
        assert (client.server_info.server_name, client.server_info.server_version) == (
            "dual",
            "9.9",
        )
        assert client.server_info.capabilities.tools
        assert sorted(t.name for t in tools.items) == ["echo", "plain"]
        assert tools.items[0].input_schema["properties"] == {"text": {"type": "string"}}
        assert [r.uri for r in resources.items] == ["res://a"]
        assert [t.uri_template for t in templates.items] == ["res://items/{item_id}"]
        assert [p.name for p in prompts.items] == ["greet"]
        assert completion.values == ["Alice"]
        assert await client.ping()


async def test_modern_client_applies_mcp_defaults_to_unset_annotation_hints():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        tools = {t.name: t for t in (await client.list_tools()).items}

    # No annotations at all: MCP's defaults.
    assert tools["plain"].annotations.destructive_hint is True
    assert tools["plain"].annotations.open_world_hint is True
    assert tools["plain"].annotations.read_only_hint is False
    # Annotated with read_only only: that hint as given, the rest defaulted.
    assert tools["echo"].annotations.read_only_hint is True
    assert tools["echo"].annotations.destructive_hint is True


async def test_modern_client_follows_pagination_cursors():
    srv = _server(page_size=1)
    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        first = await client.list_tools()
        second = await client.list_tools(cursor=first.next_cursor)

    assert [t.name for t in first.items] == ["echo"]
    assert [t.name for t in second.items] == ["plain"]
    assert second.next_cursor is None


async def test_modern_client_says_which_operations_v2_lacks():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        with pytest.raises(McpError) as exc:
            await client.subscribe_resource("res://a")

    assert exc.value.code == -32601
    assert "legacy" in exc.value.message


async def test_modern_client_sends_its_token():
    srv = _server(auth=lambda token: token == "s3cret")
    async with srv:
        async with Client(f"localhost:{srv.port}", token="s3cret", mode="modern") as client:
            assert [t.name for t in (await client.list_prompts()).items] == ["greet"]

        with pytest.raises(aio.AioRpcError):
            async with Client(f"localhost:{srv.port}", token="nope", mode="modern"):
                pass


async def test_auto_picks_v2_when_the_server_offers_it():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}", mode="auto") as client:
        assert client.protocol == "v2"


async def test_auto_falls_back_to_v1_against_a_server_without_v2():
    srv = _server()
    grpc_server = aio.server()
    mcp_pb2_grpc.add_McpServicer_to_server(_McpServicer(srv), grpc_server)  # v1 only
    port = grpc_server.add_insecure_port("127.0.0.1:0")
    await grpc_server.start()
    try:
        async with Client(f"127.0.0.1:{port}", mode="auto") as client:
            result = await client.call_tool("echo", {"text": "old server"})
            assert client.protocol == "v1"
            assert result.content[0].text == "old server"
    finally:
        await grpc_server.stop(0)


async def test_auto_fails_promptly_when_nothing_is_listening():
    client = Client("127.0.0.1:1", mode="auto", request_timeout=2)

    with pytest.raises(McpError) as exc:
        await asyncio.wait_for(client.connect(), timeout=10)

    assert exc.value.code in (408, 503)  # refused at once, or no answer within the deadline
    assert not client.is_connected


async def test_legacy_stays_the_default():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}") as client:
        assert client.protocol == "v1"


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="mode"):
        Client("localhost:1", mode="newest")
