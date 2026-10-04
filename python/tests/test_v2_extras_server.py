"""Cache hints, icons and trace context on the v2 service."""

import json

import pytest
from grpc import aio

from rapidmcp import Client, Context, Icon, RapidMCP
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc

META = pb.RequestMeta(protocol_version="2026-07-28", client_capabilities=pb.ClientCapabilities())
ICON = Icon(src="https://example.com/i.png", mime_type="image/png", sizes=("48x48",), theme="dark")
WIRE_ICON = pb.Icon(
    src="https://example.com/i.png", mime_type="image/png", sizes=["48x48"], theme="dark"
)


def _server(**kwargs) -> RapidMCP:
    srv = RapidMCP(name="extras", version="1.0", **kwargs)

    @srv.tool(icons=[ICON])
    async def echo(text: str) -> str:
        return text

    @srv.tool()
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

    return srv


async def _everything(srv: RapidMCP) -> dict:
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)
        read = [
            e async for e in stub.ReadResource(pb.ReadResourceRequest(meta=META, uri="res://a"))
        ]
        return {
            "discover": await stub.Discover(pb.DiscoverRequest(meta=META)),
            "tools": await stub.ListTools(pb.ListToolsRequest(meta=META)),
            "resources": await stub.ListResources(pb.ListResourcesRequest(meta=META)),
            "templates": await stub.ListResourceTemplates(
                pb.ListResourceTemplatesRequest(meta=META)
            ),
            "prompts": await stub.ListPrompts(pb.ListPromptsRequest(meta=META)),
            "read": read[-1].complete,
        }


async def test_cache_hints_default_to_immediately_stale_and_private():
    results = await _everything(_server())

    assert {name: (r.cache.ttl_ms, r.cache.scope) for name, r in results.items()} == dict.fromkeys(
        results, (0, pb.CACHE_SCOPE_PRIVATE)
    )


async def test_configured_cache_hints_are_stamped_on_every_cacheable_result():
    results = await _everything(_server(cache_ttl=60, cache_scope="public"))

    assert {name: (r.cache.ttl_ms, r.cache.scope) for name, r in results.items()} == dict.fromkeys(
        results, (60_000, pb.CACHE_SCOPE_PUBLIC)
    )


def test_bad_cache_settings_are_rejected_at_construction():
    with pytest.raises(ValueError, match="cache_ttl"):
        RapidMCP(name="x", version="1", cache_ttl=-1)
    with pytest.raises(ValueError, match="cache_scope"):
        RapidMCP(name="x", version="1", cache_scope="everyone")


async def test_icons_are_listed_with_their_items():
    results = await _everything(_server(icons=[ICON]))

    echo = next(t for t in results["tools"].tools if t.name == "echo")
    plain = next(t for t in results["tools"].tools if t.name == "trace")
    assert list(echo.icons) == [WIRE_ICON]
    assert list(plain.icons) == []
    assert list(results["resources"].resources[0].icons) == [WIRE_ICON]
    assert list(results["templates"].templates[0].icons) == [WIRE_ICON]
    assert list(results["prompts"].prompts[0].icons) == [WIRE_ICON]


async def test_server_icons_travel_with_discover_only():
    results = await _everything(_server(icons=[ICON]))

    assert list(results["discover"].meta.server_info.icons) == [WIRE_ICON]
    assert list(results["tools"].meta.server_info.icons) == []


@pytest.mark.parametrize(
    "src", ["javascript:alert(1)", "http://example.com/i.png", "file:///i.png", ""]
)
def test_icon_sources_other_than_https_or_data_are_rejected(src):
    srv = RapidMCP(name="x", version="1")

    with pytest.raises(ValueError, match="https: or data:"):

        @srv.tool(icons=[Icon(src=src)])
        async def bad() -> str:
            return "x"

    assert srv.list_registered_tools() == []


def test_data_uri_icons_are_accepted():
    srv = RapidMCP(name="x", version="1", icons=[Icon(src="DATA:image/png;base64,AAAA")])

    assert srv.icons[0].src == "DATA:image/png;base64,AAAA"


async def test_trace_context_reaches_the_tool_and_nothing_else_does():
    srv = _server()
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)
        metadata = [
            ("traceparent", "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01"),
            ("tracestate", "vendor=1"),
            ("baggage", "user=ada"),
            ("x-other", "ignored"),
        ]
        events = [
            e
            async for e in stub.CallTool(
                pb.CallToolRequest(meta=META, name="trace", arguments="{}"), metadata=metadata
            )
        ]

    assert json.loads(events[-1].complete.structured_content) == {
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
        "tracestate": "vendor=1",
        "baggage": "user=ada",
    }


async def test_trace_context_is_empty_on_v1():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}") as client:
        result = await client.call_tool("trace")

    assert json.loads(result.content[0].text) == {}
