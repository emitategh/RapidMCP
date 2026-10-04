"""v2 Listen: an opt-in stream of notifications, acknowledged first."""

import asyncio

import grpc
import pytest
from grpc import aio

from rapidmcp import RapidMCP
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc

META = pb.RequestMeta(protocol_version="2026-07-28", client_capabilities=pb.ClientCapabilities())


@pytest.fixture
async def pair():
    srv = RapidMCP(name="listen", version="1.0")
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        yield srv, mcp_v2_pb2_grpc.McpStub(channel)


def _listen(stub, **wanted):
    return stub.Listen(pb.ListenRequest(meta=META, notifications=pb.NotificationFilter(**wanted)))


async def _next(call) -> pb.ListenEvent:
    return await asyncio.wait_for(call.read(), timeout=3)


async def _acknowledged(call) -> pb.NotificationFilter:
    event = await _next(call)
    assert event.WhichOneof("event") == "acknowledged"
    return event.acknowledged


async def test_the_first_event_acknowledges_the_filter(pair):
    _, stub = pair
    call = _listen(stub, tools_list_changed=True, resource_subscriptions=["res://a"])

    accepted = await _acknowledged(call)

    assert accepted.tools_list_changed is True
    assert accepted.prompts_list_changed is False
    assert list(accepted.resource_subscriptions) == ["res://a"]
    call.cancel()


async def test_a_listener_gets_only_the_kinds_it_asked_for(pair):
    srv, stub = pair
    call = _listen(stub, prompts_list_changed=True)
    await _acknowledged(call)

    srv.notify_tools_list_changed()  # not asked for
    srv.notify_resources_list_changed()  # not asked for
    srv.notify_prompts_list_changed()

    assert (await _next(call)).WhichOneof("event") == "prompts_list_changed"
    call.cancel()


async def test_resource_updates_arrive_only_for_subscribed_uris(pair):
    srv, stub = pair
    call = _listen(stub, resource_subscriptions=["res://mine"])
    await _acknowledged(call)

    srv.notify_resource_updated("res://other")
    srv.notify_resource_updated("res://mine")

    event = await _next(call)
    assert (event.WhichOneof("event"), event.resource_updated.uri) == (
        "resource_updated",
        "res://mine",
    )
    call.cancel()


async def test_each_listener_is_served_independently(pair):
    srv, stub = pair
    tools = _listen(stub, tools_list_changed=True)
    prompts = _listen(stub, prompts_list_changed=True)
    await _acknowledged(tools)
    await _acknowledged(prompts)

    srv.notify_prompts_list_changed()
    srv.notify_tools_list_changed()

    assert (await _next(tools)).WhichOneof("event") == "tools_list_changed"
    assert (await _next(prompts)).WhichOneof("event") == "prompts_list_changed"
    tools.cancel()
    prompts.cancel()


async def test_subscribe_handlers_run_for_each_uri_and_may_fail(pair):
    srv, stub = pair
    seen: list[str] = []

    def explode(uri: str) -> None:
        raise RuntimeError("handler bug")

    async def record(uri: str) -> None:
        seen.append(uri)

    srv.on_resource_subscribe(explode)
    srv.on_resource_subscribe(record)
    call = _listen(stub, resource_subscriptions=["res://a", "res://b"])
    await _acknowledged(call)

    srv.notify_resource_updated("res://b")  # the stream survived the failing handler

    assert (await _next(call)).resource_updated.uri == "res://b"
    assert seen == ["res://a", "res://b"]
    call.cancel()


async def test_a_cancelled_stream_leaves_no_listener_behind(pair):
    srv, stub = pair
    call = _listen(stub, tools_list_changed=True)
    await _acknowledged(call)
    assert len(srv._v2_listeners) == 1

    call.cancel()
    for _ in range(40):
        if len(srv._v2_listeners) == 0:
            break
        await asyncio.sleep(0.05)

    assert len(srv._v2_listeners) == 0
    srv.notify_tools_list_changed()  # publishing to nobody is fine


async def test_listen_without_meta_is_invalid_params(pair):
    _, stub = pair
    call = stub.Listen(pb.ListenRequest())

    with pytest.raises(aio.AioRpcError) as exc:
        await call.read()

    assert dict(exc.value.trailing_metadata())["mcp-error-code"] == "-32602"
    assert exc.value.code() is grpc.StatusCode.INVALID_ARGUMENT
