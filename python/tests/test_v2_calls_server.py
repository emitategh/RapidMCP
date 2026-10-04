"""v2 CallTool / ReadResource / GetPrompt, exercised with a raw stub."""

import asyncio
import json

import grpc
import pytest
from grpc import aio

from rapidmcp import Context, Middleware, RapidMCP
from rapidmcp._generated import mcp_pb2, mcp_v2_pb2_grpc
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp.errors import ToolError


def _meta(**extra) -> pb.RequestMeta:
    return pb.RequestMeta(
        protocol_version="2026-07-28", client_capabilities=pb.ClientCapabilities(), **extra
    )


class _Block(Middleware):
    async def on_tool_call(self, tool_ctx, call_next):
        if tool_ctx.tool_name == "blocked":
            return mcp_pb2.CallToolResponse(
                content=[mcp_pb2.ContentItem(type="text", text="blocked by middleware")],
                is_error=True,
            )
        return await call_next(tool_ctx)


def _server(**kwargs) -> RapidMCP:
    srv = RapidMCP(name="calls", version="1.0", middleware=[_Block()], **kwargs)
    srv.started = asyncio.Event()
    srv.finished = []

    @srv.tool()
    async def echo(text: str) -> str:
        return text

    @srv.tool()
    def add(a: int, b: int) -> dict:
        return {"sum": a + b}

    @srv.tool()
    async def blocked() -> str:
        return "never"

    @srv.tool()
    async def friendly() -> str:
        raise ToolError("order id must start with ORD-")

    @srv.tool()
    async def chatty(ctx: Context) -> str:
        await ctx.debug("starting")
        await ctx.report_progress(1, 2)
        await ctx.warning("halfway", extra={"step": 1})
        await ctx.report_progress(2, 2)
        return "done"

    @srv.tool()
    async def progress_then_fail(ctx: Context) -> str:
        await ctx.report_progress(1, 2)
        raise ToolError("gave up halfway")

    @srv.tool()
    async def wants_sampling(ctx: Context) -> str:
        await ctx.sample(messages=[], max_tokens=1)
        return "never"

    @srv.tool()
    async def slow() -> str:
        srv.started.set()
        await asyncio.sleep(0.6)
        srv.finished.append("slow")
        return "done"

    @srv.resource("res://text", mime_type="text/markdown")
    async def text() -> str:
        return "# hello"

    @srv.resource("res://logo", mime_type="image/png")
    async def logo() -> bytes:
        return b"\x89PNG"

    @srv.resource("res://broken")
    async def broken() -> str:
        raise RuntimeError("password is hunter2")

    @srv.resource_template("res://items/{item_id}")
    async def item(item_id: str) -> str:
        return f"item {item_id}"

    @srv.prompt()
    async def greet(name: str, tone: str = "kind") -> str:
        return f"hi {name}, {tone}"

    @srv.prompt()
    async def broken_prompt() -> str:
        raise RuntimeError("secret")

    return srv


@pytest.fixture
async def pair():
    srv = _server()
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        yield srv, mcp_v2_pb2_grpc.McpStub(channel)


async def _events(call) -> list:
    return [event async for event in call]


def _kinds(events) -> list[str]:
    return [e.WhichOneof("event") for e in events]


async def _failure(call) -> tuple[int, str]:
    with pytest.raises(aio.AioRpcError) as exc:
        await _events(call)
    return int(dict(exc.value.trailing_metadata())["mcp-error-code"]), exc.value.details()


def _call(stub, name, arguments="{}", meta=None, **kwargs):
    return stub.CallTool(
        pb.CallToolRequest(meta=meta or _meta(), name=name, arguments=arguments), **kwargs
    )


# ── tool calls ──────────────────────────────────────────────────────────────


async def test_call_tool_ends_with_one_complete_event(pair):
    _, stub = pair

    events = await _events(_call(stub, "echo", '{"text": "hi"}'))

    assert _kinds(events) == ["complete"]
    result = events[0].complete
    assert [(c.type, c.text) for c in result.content] == [("text", "hi")]
    assert result.is_error is False
    assert result.structured_content == ""
    assert result.meta.server_info.name == "calls"


async def test_object_result_is_sent_as_structured_content_and_as_text(pair):
    _, stub = pair

    result = (await _events(_call(stub, "add", '{"a": 2, "b": 3}')))[-1].complete

    assert json.loads(result.structured_content) == {"sum": 5}
    assert json.loads(result.content[0].text) == {"sum": 5}


async def test_middleware_wraps_v2_calls(pair):
    _, stub = pair

    result = (await _events(_call(stub, "blocked")))[-1].complete

    assert (result.is_error, result.content[0].text) == (True, "blocked by middleware")


async def test_tool_error_is_a_complete_result_marked_is_error(pair):
    _, stub = pair

    result = (await _events(_call(stub, "friendly")))[-1].complete

    assert (result.is_error, result.content[0].text) == (True, "order id must start with ORD-")


async def test_no_progress_or_log_events_unless_the_request_asked(pair):
    _, stub = pair

    assert _kinds(await _events(_call(stub, "chatty"))) == ["complete"]


async def test_progress_events_carry_the_requests_token(pair):
    _, stub = pair

    events = await _events(_call(stub, "chatty", meta=_meta(progress_token="tok-7")))

    assert _kinds(events) == ["progress", "progress", "complete"]
    assert [(e.progress.token, e.progress.progress, e.progress.total) for e in events[:2]] == [
        ("tok-7", 1, 2),
        ("tok-7", 2, 2),
    ]


async def test_log_events_respect_the_requested_level(pair):
    _, stub = pair

    debug = await _events(_call(stub, "chatty", meta=_meta(log_level="debug")))
    warning = await _events(_call(stub, "chatty", meta=_meta(log_level="warning")))

    assert [(e.log.level, json.loads(e.log.data)) for e in debug if e.HasField("log")] == [
        ("debug", {"message": "starting", "extra": None}),
        ("warning", {"message": "halfway", "extra": {"step": 1}}),
    ]
    assert [e.log.level for e in warning if e.HasField("log")] == ["warning"]


async def test_log_level_set_but_nothing_logged_is_a_plain_result(pair):
    _, stub = pair

    events = await _events(_call(stub, "echo", '{"text": "x"}', meta=_meta(log_level="debug")))

    assert _kinds(events) == ["complete"]


async def test_unknown_log_level_is_invalid_params(pair):
    _, stub = pair

    code, _ = await _failure(_call(stub, "echo", '{"text": "x"}', meta=_meta(log_level="loud")))

    assert code == -32602


async def test_progress_then_failure_still_delivers_the_failure(pair):
    _, stub = pair

    events = await _events(_call(stub, "progress_then_fail", meta=_meta(progress_token="t")))

    assert _kinds(events) == ["progress", "complete"]
    assert (events[-1].complete.is_error, events[-1].complete.content[0].text) == (
        True,
        "gave up halfway",
    )


async def test_unknown_tool_and_bad_arguments_are_invalid_params(pair):
    _, stub = pair

    assert await _failure(_call(stub, "nope")) == (-32602, "Tool 'nope' not found")
    assert await _failure(_call(stub, "echo", "{not json")) == (
        -32602,
        "Invalid arguments for tool 'echo': not valid JSON",
    )
    assert await _failure(_call(stub, "echo", "[1]")) == (
        -32602,
        "Invalid arguments for tool 'echo': expected a JSON object",
    )


async def test_sampling_is_not_available_on_v2(pair):
    _, stub = pair

    code, message = await _failure(_call(stub, "wants_sampling"))

    assert code == -32601
    assert "v2" in message


async def test_cancelling_the_rpc_cancels_the_tool(pair):
    srv, stub = pair
    call = _call(stub, "slow")
    reader = asyncio.create_task(_events(call))
    await asyncio.wait_for(srv.started.wait(), timeout=3)

    call.cancel()
    await asyncio.gather(reader, return_exceptions=True)
    await asyncio.sleep(0.8)

    assert srv.finished == []


async def test_deadline_cancels_the_tool(pair):
    srv, stub = pair

    with pytest.raises(aio.AioRpcError) as exc:
        await _events(_call(stub, "slow", timeout=0.2))
    await asyncio.sleep(0.8)

    assert exc.value.code() is grpc.StatusCode.DEADLINE_EXCEEDED
    assert srv.finished == []


# ── resources ───────────────────────────────────────────────────────────────


def _read(stub, uri, **kwargs):
    return stub.ReadResource(pb.ReadResourceRequest(meta=_meta(), uri=uri), **kwargs)


async def test_read_text_resource(pair):
    _, stub = pair

    events = await _events(_read(stub, "res://text"))

    assert _kinds(events) == ["complete"]
    item = events[0].complete.content[0]
    assert (item.type, item.text, item.mime_type, item.uri) == (
        "text",
        "# hello",
        "text/markdown",
        "res://text",
    )
    assert events[0].complete.cache.ttl_ms == 0


async def test_read_binary_resource_is_typed_by_mime(pair):
    _, stub = pair

    item = (await _events(_read(stub, "res://logo")))[0].complete.content[0]

    assert (item.type, item.data, item.mime_type) == ("image", b"\x89PNG", "image/png")


async def test_read_templated_resource(pair):
    _, stub = pair

    item = (await _events(_read(stub, "res://items/42")))[0].complete.content[0]

    assert item.text == "item 42"


async def test_missing_resource_is_invalid_params(pair):
    _, stub = pair

    assert await _failure(_read(stub, "res://nope")) == (-32602, "Resource 'res://nope' not found")


async def test_failing_resource_handler_does_not_leak_its_exception(pair):
    _, stub = pair

    assert await _failure(_read(stub, "res://broken")) == (
        -32603,
        "Resource handler for 'res://broken' failed",
    )


# ── prompts ─────────────────────────────────────────────────────────────────


def _prompt(stub, name, arguments=None):
    return stub.GetPrompt(pb.GetPromptRequest(meta=_meta(), name=name, arguments=arguments or {}))


async def test_get_prompt_returns_a_user_message(pair):
    _, stub = pair

    events = await _events(_prompt(stub, "greet", {"name": "Ada"}))

    assert _kinds(events) == ["complete"]
    message = events[0].complete.messages[0]
    assert (message.role, message.content.type, message.content.text) == (
        "user",
        "text",
        "hi Ada, kind",
    )


async def test_prompt_argument_problems_are_invalid_params(pair):
    _, stub = pair

    assert await _failure(_prompt(stub, "nope")) == (-32602, "Prompt 'nope' not found")
    assert await _failure(_prompt(stub, "greet")) == (
        -32602,
        "Missing required argument(s) for prompt 'greet': name",
    )
    assert await _failure(_prompt(stub, "greet", {"name": "Ada", "mood": "x"})) == (
        -32602,
        "Unknown argument(s) for prompt 'greet': mood",
    )


async def test_failing_prompt_handler_does_not_leak_its_exception(pair):
    _, stub = pair

    assert await _failure(_prompt(stub, "broken_prompt")) == (
        -32603,
        "Prompt handler 'broken_prompt' failed",
    )


# ── auth ────────────────────────────────────────────────────────────────────


async def test_streaming_calls_need_the_token_when_the_server_has_auth():
    srv = _server(auth=lambda token: token == "s3cret")
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)

        with pytest.raises(aio.AioRpcError) as exc:
            await _events(_call(stub, "echo", '{"text": "x"}'))
        allowed = await _events(
            _call(stub, "echo", '{"text": "x"}', metadata=[("authorization", "Bearer s3cret")])
        )

    assert exc.value.code() is grpc.StatusCode.UNAUTHENTICATED
    assert allowed[-1].complete.content[0].text == "x"
