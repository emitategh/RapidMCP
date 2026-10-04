"""A modern client sees cache hints and icons, and propagates trace context."""

import pytest

from rapidmcp import Client, Context, Icon, RapidMCP

ICON = Icon(src="https://example.com/i.png", mime_type="image/png", sizes=("48x48",), theme="dark")


@pytest.fixture
async def server():
    srv = RapidMCP(name="extras", version="1.0", cache_ttl=30, cache_scope="public", icons=[ICON])

    @srv.tool(icons=[ICON])
    async def trace(ctx: Context) -> dict:
        return dict(ctx.trace_context)

    @srv.resource("res://a", icons=[ICON])
    async def a() -> str:
        return "a"

    @srv.resource_template("res://items/{item_id}", icons=[ICON])
    async def item(item_id: str) -> str:
        return item_id

    @srv.prompt(icons=[ICON])
    async def greet(name: str) -> str:
        return f"hi {name}"

    async with srv:
        yield srv


async def test_modern_client_sees_cache_hints(server):
    async with Client(f"localhost:{server.port}", mode="modern") as client:
        results = [
            await client.list_tools(),
            await client.list_resources(),
            await client.list_resource_templates(),
            await client.list_prompts(),
            await client.read_resource("res://a"),
        ]

    assert [(r.ttl_ms, r.cache_scope) for r in results] == [(30_000, "public")] * 5


async def test_legacy_client_sees_no_cache_hints_or_icons(server):
    async with Client(f"localhost:{server.port}") as client:
        tools = await client.list_tools()
        read = await client.read_resource("res://a")

    assert (tools.ttl_ms, tools.cache_scope, read.ttl_ms, read.cache_scope) == (None,) * 4
    assert tools.items[0].icons == []
    assert client.server_info.icons == []


async def test_modern_client_sees_icons(server):
    async with Client(f"localhost:{server.port}", mode="modern") as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        prompts = await client.list_prompts()
        server_icons = client.server_info.icons

    assert tools.items[0].icons == [ICON]
    assert resources.items[0].icons == [ICON]
    assert templates.items[0].icons == [ICON]
    assert prompts.items[0].icons == [ICON]
    assert server_icons == [ICON]


async def test_trace_context_provider_is_called_per_request_and_filtered(server):
    spans = iter(["00-aaaa-01", "00-bbbb-01"])

    def provider() -> dict[str, str]:
        return {"traceparent": next(spans), "baggage": "user=ada", "authorization": "nope"}

    client = Client(f"localhost:{server.port}", mode="modern", trace_context=provider)
    await client.connect()  # discover does not consume a span: the provider is only for calls
    try:
        first = await client.call_tool("trace")
        second = await client.call_tool("trace")
    finally:
        await client.close()

    assert first.structured_content == {"traceparent": "00-aaaa-01", "baggage": "user=ada"}
    assert second.structured_content == {"traceparent": "00-bbbb-01", "baggage": "user=ada"}
