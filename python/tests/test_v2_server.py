"""The v2 service: stateless requests answered next to the v1 stream."""

import grpc
import pytest
from grpc import aio

from rapidmcp import Client, RapidMCP
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc

META = pb.RequestMeta(
    protocol_version="2026-07-28",
    client_capabilities=pb.ClientCapabilities(),
    client_info=pb.Implementation(name="test", version="0"),
)


def _server(**kwargs) -> RapidMCP:
    srv = RapidMCP(name="v2-server", version="1.2.3", **kwargs)

    @srv.tool(description="Echo", read_only=True)
    async def echo(text: str) -> str:
        return text

    @srv.tool()
    async def plain() -> str:
        return "x"

    @srv.resource("res://a", description="A")
    async def a() -> str:
        return "a"

    @srv.resource_template("res://items/{item_id}")
    async def item(item_id: str) -> str:
        return item_id

    @srv.prompt(description="Greet")
    async def greet(name: str) -> str:
        return f"hi {name}"

    @srv.completion("greet")
    async def complete_greet(argument_name: str, value: str) -> list[str]:
        return [f"{value}lice", f"{value}da"]

    return srv


@pytest.fixture
async def stub():
    srv = _server()
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        yield mcp_v2_pb2_grpc.McpStub(channel)


async def _mcp_code(call) -> tuple[int, grpc.StatusCode]:
    with pytest.raises(aio.AioRpcError) as exc:
        await call
    trailers = dict(exc.value.trailing_metadata())
    return int(trailers["mcp-error-code"]), exc.value.code()


async def test_discover_reports_identity_versions_and_capabilities(stub):
    result = await stub.Discover(pb.DiscoverRequest(meta=META))

    assert (result.meta.server_info.name, result.meta.server_info.version) == ("v2-server", "1.2.3")
    assert list(result.supported_versions) == ["2026-07-28"]
    assert result.capabilities.tools.list_changed
    assert result.capabilities.HasField("resources")
    assert result.capabilities.HasField("prompts")
    assert (result.cache.ttl_ms, result.cache.scope) == (0, pb.CACHE_SCOPE_PRIVATE)


async def test_discover_omits_capabilities_for_what_is_not_registered():
    srv = RapidMCP(name="empty", version="0.1")
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        result = await mcp_v2_pb2_grpc.McpStub(channel).Discover(pb.DiscoverRequest(meta=META))

    assert not result.capabilities.HasField("tools")
    assert not result.capabilities.HasField("resources")
    assert not result.capabilities.HasField("prompts")


async def test_list_tools_returns_schemas_and_only_the_hints_that_were_set(stub):
    result = await stub.ListTools(pb.ListToolsRequest(meta=META))
    tools = {t.name: t for t in result.tools}

    assert sorted(tools) == ["echo", "plain"]
    assert '"text"' in tools["echo"].input_schema
    assert tools["echo"].annotations.read_only_hint is True
    # echo's author set read_only and nothing else: the other hints stay unset.
    assert not tools["echo"].annotations.HasField("destructive_hint")
    assert not tools["echo"].annotations.HasField("open_world_hint")
    assert not tools["plain"].HasField("annotations")
    assert result.meta.server_info.name == "v2-server"


async def test_list_resources_templates_and_prompts(stub):
    resources = await stub.ListResources(pb.ListResourcesRequest(meta=META))
    templates = await stub.ListResourceTemplates(pb.ListResourceTemplatesRequest(meta=META))
    prompts = await stub.ListPrompts(pb.ListPromptsRequest(meta=META))

    assert [(r.uri, r.description) for r in resources.resources] == [("res://a", "A")]
    assert [t.uri_template for t in templates.templates] == ["res://items/{item_id}"]
    assert [(p.name, [a.name for a in p.arguments]) for p in prompts.prompts] == [
        ("greet", ["name"])
    ]


async def test_complete_returns_the_handler_values(stub):
    result = await stub.Complete(
        pb.CompleteRequest(
            meta=META,
            ref=pb.CompletionRef(type="ref/prompt", name="greet"),
            argument=pb.CompletionArg(name="name", value="A"),
        )
    )

    assert (list(result.values), result.total) == (["Alice", "Ada"], 2)


async def test_lists_paginate_and_tolerate_a_garbage_cursor():
    srv = _server(page_size=1)
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)
        first = await stub.ListTools(pb.ListToolsRequest(meta=META))
        second = await stub.ListTools(pb.ListToolsRequest(meta=META, cursor=first.next_cursor))
        garbage = await stub.ListTools(pb.ListToolsRequest(meta=META, cursor="not-a-cursor"))

    assert [t.name for t in first.tools] == ["echo"]
    assert [t.name for t in second.tools] == ["plain"]
    assert second.next_cursor == ""
    assert [t.name for t in garbage.tools] == ["echo"]


async def test_request_without_meta_is_invalid_params(stub):
    assert await _mcp_code(stub.ListTools(pb.ListToolsRequest())) == (
        -32602,
        grpc.StatusCode.INVALID_ARGUMENT,
    )


async def test_request_without_client_capabilities_is_invalid_params(stub):
    meta = pb.RequestMeta(protocol_version="2026-07-28")

    assert (await _mcp_code(stub.Discover(pb.DiscoverRequest(meta=meta))))[0] == -32602


async def test_unsupported_version_lists_the_supported_ones(stub):
    meta = pb.RequestMeta(
        protocol_version="1900-01-01", client_capabilities=pb.ClientCapabilities()
    )

    with pytest.raises(aio.AioRpcError) as exc:
        await stub.Discover(pb.DiscoverRequest(meta=meta))

    trailers = dict(exc.value.trailing_metadata())
    data = pb.ErrorData.FromString(trailers["mcp-error-data-bin"])
    assert trailers["mcp-error-code"] == "-32022"
    assert exc.value.code() is grpc.StatusCode.FAILED_PRECONDITION
    assert (list(data.supported_versions), data.requested_version) == (
        ["2026-07-28"],
        "1900-01-01",
    )


async def test_failing_completion_handler_is_an_internal_error():
    srv = RapidMCP(name="boom", version="0.1")

    @srv.completion("x")
    async def boom(argument_name: str, value: str) -> list[str]:
        raise RuntimeError("exploded")

    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        call = mcp_v2_pb2_grpc.McpStub(channel).Complete(
            pb.CompleteRequest(meta=META, ref=pb.CompletionRef(name="x"))
        )
        assert await _mcp_code(call) == (-32603, grpc.StatusCode.INTERNAL)


async def test_v2_calls_need_the_token_when_the_server_has_auth():
    srv = _server(auth=lambda token: token == "s3cret")
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)

        with pytest.raises(aio.AioRpcError) as exc:
            await stub.Discover(pb.DiscoverRequest(meta=META))
        allowed = await stub.Discover(
            pb.DiscoverRequest(meta=META), metadata=[("authorization", "Bearer s3cret")]
        )

    assert exc.value.code() is grpc.StatusCode.UNAUTHENTICATED
    assert allowed.meta.server_info.name == "v2-server"


async def test_v1_clients_still_work_on_the_same_port():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}") as client:
        result = await client.call_tool("echo", {"text": "still v1"})

    assert result.content[0].text == "still v1"


async def test_an_explicit_false_hint_is_sent_as_false():
    srv = RapidMCP(name="hints", version="0.1")

    @srv.tool(destructive=False, title="Safe")
    async def safe() -> str:
        return "x"

    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        result = await mcp_v2_pb2_grpc.McpStub(channel).ListTools(pb.ListToolsRequest(meta=META))

    annotations = result.tools[0].annotations
    assert annotations.HasField("destructive_hint")
    assert annotations.destructive_hint is False
    assert not annotations.HasField("read_only_hint")
