"""Regressions found by the whole-branch review of the v2 protocol."""

import asyncio
import datetime
import json

import pytest
from grpc import aio

from rapidmcp import BoolField, Client, Context, Middleware, RapidMCP
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc
from rapidmcp._v2_errors import abort
from rapidmcp._v2_servicer import _McpV2Servicer
from rapidmcp.errors import INVALID_PARAMS, UNSUPPORTED_PROTOCOL_VERSION, McpError

FORM = pb.ClientCapabilities(elicitation=pb.ElicitationCapability(form=True))
META = pb.RequestMeta(protocol_version="2026-07-28", client_capabilities=FORM)


class Redact(Middleware):
    async def on_tool_call(self, ctx, call_next):
        response = await call_next(ctx)
        for item in response.content:
            item.text = item.text.replace("sk-123", "***")
        return response


async def _terminal(stub, request) -> pb.CallToolEvent:
    events = [event async for event in stub.CallTool(request)]
    return events[-1]


async def _until(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition never became true"
        await asyncio.sleep(0.02)


# ── structured content ───────────────────────────────────────────────────


async def test_structured_content_is_what_middleware_let_through():
    srv = RapidMCP(name="x", version="1", middleware=[Redact()])

    @srv.tool()
    async def secret() -> dict:
        return {"key": "sk-123"}

    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)
        event = await _terminal(stub, pb.CallToolRequest(meta=META, name="secret", arguments="{}"))

    assert json.loads(event.complete.content[0].text) == {"key": "***"}
    assert json.loads(event.complete.structured_content) == {"key": "***"}


async def test_a_result_that_cannot_be_serialised_is_a_tool_error_not_a_protocol_error():
    srv = RapidMCP(name="x", version="1")

    @srv.tool()
    async def when() -> dict:
        return {"at": datetime.datetime(2026, 1, 1)}

    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)
        event = await _terminal(stub, pb.CallToolRequest(meta=META, name="when", arguments="{}"))

    assert event.complete.is_error is True
    assert event.complete.content[0].text.startswith("Error calling tool 'when'")
    assert event.complete.structured_content == ""


# ── input rounds ─────────────────────────────────────────────────────────


def _asking_server() -> RapidMCP:
    srv = RapidMCP(name="x", version="1", state_secret="s3cret-key")

    @srv.tool()
    async def deploy(ctx: Context) -> str:
        answer = await ctx.elicit("Deploy?", fields={"confirm": BoolField()})
        return f"deployed ({answer.action})"

    return srv


async def test_an_answer_sent_before_the_question_was_asked_is_ignored():
    srv = _asking_server()
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)
        first = await _terminal(stub, pb.CallToolRequest(meta=META, name="deploy", arguments="{}"))
        key = next(iter(first.input_required.input_requests))
        answer = {
            key: pb.InputResponse(
                elicit=pb.ElicitResult(action="accept", content='{"confirm": true}')
            )
        }

        unasked = await _terminal(
            stub,
            pb.CallToolRequest(meta=META, name="deploy", arguments="{}", input_responses=answer),
        )
        asked = await _terminal(
            stub,
            pb.CallToolRequest(
                meta=META,
                name="deploy",
                arguments="{}",
                input_responses=answer,
                request_state=first.input_required.request_state,
            ),
        )

    assert unasked.WhichOneof("event") == "input_required"
    assert asked.complete.content[0].text == "deployed (accept)"


# ── handlers that refuse on purpose ──────────────────────────────────────


async def test_a_deliberate_mcp_error_from_a_resource_or_prompt_keeps_its_code():
    srv = RapidMCP(name="x", version="1")

    @srv.resource("res://deny")
    async def deny() -> str:
        raise McpError(INVALID_PARAMS, "you may not read this")

    @srv.prompt()
    async def refuse() -> str:
        raise McpError(INVALID_PARAMS, "no prompt for you")

    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        with pytest.raises(McpError) as read:
            await client.read_resource("res://deny")
        with pytest.raises(McpError) as prompt:
            await client.get_prompt("refuse")

    assert (read.value.code, read.value.message) == (INVALID_PARAMS, "you may not read this")
    assert (prompt.value.code, prompt.value.message) == (INVALID_PARAMS, "no prompt for you")


# ── the client's subscription ────────────────────────────────────────────


async def test_a_rejected_subscription_is_reported_to_the_caller(monkeypatch):
    async def reject(self, request, context):
        await abort(context, McpError(UNSUPPORTED_PROTOCOL_VERSION, "no listening here"))
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(_McpV2Servicer, "Listen", reject)
    srv = RapidMCP(name="x", version="1")

    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        with pytest.raises(McpError) as exc:
            await client.subscribe_resource("res://a")

    assert exc.value.code == UNSUPPORTED_PROTOCOL_VERSION


def _record_listeners(srv: RapidMCP) -> list[str]:
    """Record the order in which the server gains and loses listeners."""
    events: list[str] = []
    add, remove = srv._v2_listeners.add, srv._v2_listeners.remove

    def recording_add(notifications):
        events.append("add")
        return add(notifications)

    def recording_remove(listener):
        events.append("remove")
        return remove(listener)

    srv._v2_listeners.add = recording_add
    srv._v2_listeners.remove = recording_remove
    return events


async def test_handlers_that_do_not_change_the_filter_do_not_reopen_the_stream():
    srv = RapidMCP(name="x", version="1")
    events = _record_listeners(srv)
    async with srv:
        client = Client(f"localhost:{srv.port}", mode="modern")
        client.on_notification("tools_list_changed", lambda payload: None)
        await client.connect()
        client.on_notification("progress", lambda payload: None)
        client.on_notification("log", lambda payload: None)
        client.on_notification("tools_list_changed", lambda payload: None)
        await client.subscribe_resource("res://a")
        await client.subscribe_resource("res://a")  # already subscribed
        await asyncio.sleep(0.2)
        seen = list(events)
        await client.close()

    assert seen == ["add", "add", "remove"]


async def test_the_new_stream_is_open_before_the_old_one_is_closed():
    srv = RapidMCP(name="x", version="1")
    events = _record_listeners(srv)
    async with srv:
        client = Client(f"localhost:{srv.port}", mode="modern")
        client.on_notification("tools_list_changed", lambda payload: None)
        await client.connect()
        await client.subscribe_resource("res://a")
        await _until(lambda: len(events) == 3)
        seen, open_streams = list(events), len(srv._v2_listeners)
        await client.close()

    assert seen == ["add", "add", "remove"]
    assert open_streams == 1


async def test_subscribing_while_closing_fails_cleanly():
    srv = RapidMCP(name="x", version="1")
    async with srv:
        for _ in range(20):
            client = Client(f"localhost:{srv.port}", mode="modern")
            await client.connect()
            results = await asyncio.gather(
                client.subscribe_resource("res://a"), client.close(), return_exceptions=True
            )
            await client.close()

            assert not [
                r for r in results if isinstance(r, Exception) and not isinstance(r, McpError)
            ]


# ── server settings ──────────────────────────────────────────────────────


@pytest.mark.parametrize("ttl", [float("nan"), float("inf")])
def test_cache_ttl_must_be_finite(ttl):
    with pytest.raises(ValueError, match="cache_ttl"):
        RapidMCP(name="x", version="1", cache_ttl=ttl)


@pytest.mark.parametrize("secret", ["", b""])
def test_an_empty_state_secret_is_rejected(secret):
    with pytest.raises(ValueError, match="state_secret"):
        RapidMCP(name="x", version="1", state_secret=secret)
