"""ctx.elicit() on v2: the call ends with input_required and is retried with the answer."""

import json

import grpc
import pytest
from grpc import aio

from rapidmcp import BoolField, Context, RapidMCP, StringField
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc
from rapidmcp._v2_state import operation_digest, seal

FORM = pb.ClientCapabilities(elicitation=pb.ElicitationCapability(form=True))
FORM_AND_URL = pb.ClientCapabilities(elicitation=pb.ElicitationCapability(form=True, url=True))


def _meta(capabilities=FORM) -> pb.RequestMeta:
    return pb.RequestMeta(protocol_version="2026-07-28", client_capabilities=capabilities)


def _server(**kwargs) -> RapidMCP:
    srv = RapidMCP(name="input", version="1.0", state_secret="s3cret-key", **kwargs)
    srv.runs = []

    @srv.tool()
    async def deploy(service: str, ctx: Context) -> str:
        srv.runs.append(service)
        answer = await ctx.elicit("Deploy to production?", fields={"confirm": BoolField()})
        if not answer.accepted:
            return f"not deployed ({answer.action})"
        return f"deployed {service} confirm={answer.data.get('confirm')}"

    @srv.tool()
    async def two_questions(ctx: Context) -> str:
        first = await ctx.elicit("Name?", fields={"name": StringField()})
        second = await ctx.elicit("Colour?", fields={"colour": StringField()})
        return f"{first.data['name']} likes {second.data['colour']}"

    @srv.tool()
    async def named(ctx: Context) -> str:
        answer = await ctx.elicit("Sure?", fields={"ok": BoolField()}, key="confirmation")
        return f"{answer.action} / seen={sorted(ctx.input_responses)}"

    @srv.tool()
    async def pay(ctx: Context) -> str:
        answer = await ctx.elicit("Complete the payment", url="https://pay.example/session/42")
        return f"payment {answer.action}"

    return srv


@pytest.fixture
async def pair():
    srv = _server()
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        yield srv, mcp_v2_pb2_grpc.McpStub(channel)


async def _call(stub, name, arguments="{}", *, answers=None, state=b"", meta=None, **kwargs):
    """One round. Returns the terminal event."""
    request = pb.CallToolRequest(
        meta=meta or _meta(),
        name=name,
        arguments=arguments,
        input_responses={
            key: pb.InputResponse(elicit=pb.ElicitResult(action=action, content=content))
            for key, (action, content) in (answers or {}).items()
        },
        request_state=state,
    )
    events = [event async for event in stub.CallTool(request, **kwargs)]
    return events[-1]


async def _code(coro) -> tuple[int, str]:
    with pytest.raises(aio.AioRpcError) as exc:
        await coro
    return int(dict(exc.value.trailing_metadata())["mcp-error-code"]), exc.value.details()


async def test_first_round_asks_the_question(pair):
    _, stub = pair

    event = await _call(stub, "deploy", '{"service": "api"}')

    assert event.WhichOneof("event") == "input_required"
    assert list(event.input_required.input_requests) == ["elicit-0"]
    request = event.input_required.input_requests["elicit-0"].elicit
    assert request.message == "Deploy to production?"
    assert request.WhichOneof("mode") == "form"
    assert json.loads(request.form.requested_schema)["properties"] == {
        "confirm": {"type": "boolean"}
    }
    assert event.input_required.request_state


async def test_retry_with_the_answer_completes_the_call(pair):
    _, stub = pair
    first = await _call(stub, "deploy", '{"service": "api"}')

    second = await _call(
        stub,
        "deploy",
        '{"service": "api"}',
        answers={"elicit-0": ("accept", '{"confirm": true}')},
        state=first.input_required.request_state,
    )

    assert second.complete.content[0].text == "deployed api confirm=True"


async def test_a_declined_question_reaches_the_tool_as_a_decline(pair):
    _, stub = pair
    first = await _call(stub, "deploy", '{"service": "api"}')

    second = await _call(
        stub,
        "deploy",
        '{"service": "api"}',
        answers={"elicit-0": ("decline", "")},
        state=first.input_required.request_state,
    )

    assert (second.complete.is_error, second.complete.content[0].text) == (
        False,
        "not deployed (decline)",
    )


async def test_the_tool_runs_again_from_the_top_each_round(pair):
    srv, stub = pair
    first = await _call(stub, "deploy", '{"service": "api"}')
    await _call(
        stub,
        "deploy",
        '{"service": "api"}',
        answers={"elicit-0": ("accept", "{}")},
        state=first.input_required.request_state,
    )

    assert srv.runs == ["api", "api"]


async def test_the_first_answer_survives_to_the_third_call(pair):
    _, stub = pair
    one = await _call(stub, "two_questions")
    two = await _call(
        stub,
        "two_questions",
        answers={"elicit-0": ("accept", '{"name": "Ada"}')},
        state=one.input_required.request_state,
    )
    three = await _call(
        stub,
        "two_questions",
        answers={"elicit-1": ("accept", '{"colour": "green"}')},  # only the latest answer
        state=two.input_required.request_state,
    )

    assert list(one.input_required.input_requests) == ["elicit-0"]
    assert list(two.input_required.input_requests) == ["elicit-1"]
    assert three.complete.content[0].text == "Ada likes green"


async def test_an_answer_nobody_asked_for_is_ignored(pair):
    _, stub = pair

    event = await _call(
        stub, "deploy", '{"service": "api"}', answers={"surprise": ("accept", "{}")}
    )

    assert event.WhichOneof("event") == "input_required"
    assert list(event.input_required.input_requests) == ["elicit-0"]


async def test_explicit_key_and_input_responses(pair):
    _, stub = pair
    first = await _call(stub, "named")
    second = await _call(
        stub,
        "named",
        answers={"confirmation": ("accept", '{"ok": true}')},
        state=first.input_required.request_state,
    )

    assert list(first.input_required.input_requests) == ["confirmation"]
    assert second.complete.content[0].text == "accept / seen=['confirmation']"


async def test_altered_state_is_invalid_params(pair):
    _, stub = pair
    first = await _call(stub, "deploy", '{"service": "api"}')
    state = bytearray(first.input_required.request_state)
    state[-3] ^= 1

    code, message = await _code(_call(stub, "deploy", '{"service": "api"}', state=bytes(state)))

    assert code == -32602
    assert message.startswith("Invalid request_state:")


async def test_state_replayed_on_other_arguments_or_another_tool_is_rejected(pair):
    _, stub = pair
    first = await _call(stub, "deploy", '{"service": "api"}')
    state = first.input_required.request_state

    other_arguments = await _code(_call(stub, "deploy", '{"service": "db"}', state=state))
    other_tool = await _code(_call(stub, "two_questions", state=state))

    assert other_arguments[0] == other_tool[0] == -32602
    assert "different request" in other_arguments[1]
    assert "different request" in other_tool[1]


async def test_expired_state_is_rejected(pair):
    _, stub = pair
    arguments = '{"service": "api"}'
    stale = seal(
        b"s3cret-key", {}, operation_digest("tools/call", "deploy", arguments), "", now=1_000.0
    )

    code, message = await _code(_call(stub, "deploy", arguments, state=stale))

    assert (code, "expired" in message) == (-32602, True)


async def test_state_from_another_caller_is_rejected():
    srv = _server(auth=lambda token: token in ("alice", "bob"))
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)
        alice = [("authorization", "Bearer alice")]
        bob = [("authorization", "Bearer bob")]
        first = await _call(stub, "deploy", '{"service": "api"}', metadata=alice)
        state = first.input_required.request_state

        code, message = await _code(
            _call(stub, "deploy", '{"service": "api"}', state=state, metadata=bob)
        )
        same_caller = await _call(
            stub,
            "deploy",
            '{"service": "api"}',
            answers={"elicit-0": ("accept", "{}")},
            state=state,
            metadata=alice,
        )

    assert (code, "different caller" in message) == (-32602, True)
    assert same_caller.WhichOneof("event") == "complete"


async def test_any_replica_with_the_same_secret_can_finish_the_call():
    a, b = _server(), _server()
    async with a, b:
        async with aio.insecure_channel(f"localhost:{a.port}") as channel:
            first = await _call(mcp_v2_pb2_grpc.McpStub(channel), "deploy", '{"service": "api"}')
        async with aio.insecure_channel(f"localhost:{b.port}") as channel:
            second = await _call(
                mcp_v2_pb2_grpc.McpStub(channel),
                "deploy",
                '{"service": "api"}',
                answers={"elicit-0": ("accept", '{"confirm": true}')},
                state=first.input_required.request_state,
            )

    assert second.complete.content[0].text == "deployed api confirm=True"
    assert (a.runs, b.runs) == (["api"], ["api"])


async def test_client_without_elicitation_gets_missing_capability(pair):
    _, stub = pair

    with pytest.raises(aio.AioRpcError) as exc:
        await _call(stub, "deploy", '{"service": "api"}', meta=_meta(pb.ClientCapabilities()))

    trailers = dict(exc.value.trailing_metadata())
    data = pb.ErrorData.FromString(trailers["mcp-error-data-bin"])
    assert trailers["mcp-error-code"] == "-32021"
    assert exc.value.code() is grpc.StatusCode.FAILED_PRECONDITION
    assert list(data.required_capabilities) == ["elicitation.form"]


async def test_url_mode_needs_the_url_capability(pair):
    _, stub = pair

    with pytest.raises(aio.AioRpcError) as exc:
        await _call(stub, "pay")  # form only
    asked = await _call(stub, "pay", meta=_meta(FORM_AND_URL))
    done = await _call(
        stub,
        "pay",
        meta=_meta(FORM_AND_URL),
        answers={"elicit-0": ("accept", "")},
        state=asked.input_required.request_state,
    )

    data = pb.ErrorData.FromString(dict(exc.value.trailing_metadata())["mcp-error-data-bin"])
    assert list(data.required_capabilities) == ["elicitation.url"]
    request = asked.input_required.input_requests["elicit-0"].elicit
    assert (request.WhichOneof("mode"), request.url.url) == (
        "url",
        "https://pay.example/session/42",
    )
    assert done.complete.content[0].text == "payment accept"
