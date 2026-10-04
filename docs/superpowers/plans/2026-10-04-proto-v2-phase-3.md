# Proto v2, Phase 3 Implementation Plan — asking the client for input

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tool on the v2 protocol can ask the user a question (form or URL elicitation) with the same `ctx.elicit()` call it uses on v1, and a v2 client answers through the elicitation handler it already registers.

**Architecture:** v2 has no server-to-client requests. When `ctx.elicit()` has no answer yet, the call ends with an `input_required` event carrying the question and an opaque `request_state`; the client asks its handler and calls again with the answer and the state; the tool runs again from the top and this time `ctx.elicit()` returns. Earlier answers ride in `request_state`, which the server signs, binds to the operation and the caller, and expires.

**Tech Stack:** Python 3.10+ (`grpcio` aio, stdlib `hmac`/`hashlib`), TypeScript (`nice-grpc`, `node:crypto`).

**Spec:** `docs/superpowers/specs/2026-10-01-proto-v2-stateless-design.md`, section "Asking the client for input (multi-round-trip requests)".

## Global Constraints

- `request_state` wire format, identical in both languages: 32 bytes of HMAC-SHA256 followed by the UTF-8 JSON payload `{"v":1,"exp":<unix seconds>,"op":"<hex>","sub":"<hex or empty>","answers":{<key>:{"action":…,"content":…}}}`. The MAC covers exactly the payload bytes.
- `op` is the hex SHA-256 of `"<method>\n<name>\n<arguments text>"`, with method `tools/call`. A retry must therefore send the same arguments text.
- `sub` is the hex SHA-256 of the request's `authorization` metadata value when the server has `auth` configured, otherwise the empty string.
- State lives 600 seconds. State that fails the MAC, has expired, or names another operation or caller is rejected with `-32602` and a message starting `Invalid request_state:`.
- The secret comes from `RapidMCP(state_secret=...)` / `stateSecret`. Without one, the server generates a random secret at start-up and logs one warning the first time it issues state.
- Default answer keys are `elicit-0`, `elicit-1`, … in call order within one run of the tool; `key=` overrides.
- A server never asks for a mode the request's capabilities did not declare: missing form or URL support is `-32021`, with `required_capabilities` `["elicitation.form"]` or `["elicitation.url"]`.
- The client gives up after 10 rounds with a local `McpError` code `508`.
- Sampling and roots stay unavailable on v2.
- Python: project venv; `ruff format` and `ruff check` before each commit. Git: `-c safe.directory=D:/Trabajo/mcp-grpc`; no co-author or tool attribution in commit messages.

## Review Focus

1. **State replayed on a different request** — same tool with different arguments, or a different tool: must be `-32602`, never treated as answers.
2. **State presented by a different caller** on a server with auth — `-32602`.
3. **A client that sends an answer nobody asked for** — ignored, as the standard says; the tool still asks its own question.
4. **A tool that asks two questions** — the first answer must survive to the third call even though the client only sends the latest answer.
5. **A user who declines** — the tool receives `decline` and decides; the protocol does not turn it into an error.

---

### Task 1: Python — the request-state codec

**Files:**
- Create: `python/src/rapidmcp/_v2_state.py`
- Test: `python/tests/test_v2_state.py`

**Interfaces:**
- Produces: `operation_digest(method: str, name: str, arguments_text: str) -> str`; `principal_digest(authorization: str | None) -> str`; `seal(secret: bytes, answers: dict, operation: str, principal: str, *, now: float | None = None, ttl: float = 600) -> bytes`; `unseal(secret: bytes, state: bytes, operation: str, principal: str, *, now: float | None = None) -> dict` (raises `McpError(-32602)`).

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_state.py`:

```python
"""request_state: signed, bound to its request and caller, and short-lived."""

import pytest

from rapidmcp._v2_state import operation_digest, principal_digest, seal, unseal
from rapidmcp.errors import McpError

SECRET = b"shared-secret"
OP = operation_digest("tools/call", "deploy", '{"service": "api"}')
ANSWERS = {"elicit-0": {"action": "accept", "content": '{"confirm": true}'}}

# Produced once by hand with HMAC-SHA256("shared-secret", payload); the TypeScript
# suite carries the same bytes, which is what keeps the two implementations compatible.
VECTOR = bytes.fromhex(
    "c16779a64fde2278de3d869db2efc16ee1cdda58fab040d26f7d9e16552ae276"
    "7b2276223a312c22657870223a343130323434343830302c226f70223a2234616632363130643935373039"
    "623365303133343562616461656538323633363032626465306431623332373337303638633138656561"
    "313936646362613831222c22737562223a22222c22616e7377657273223a7b22656c696369742d30223a"
    "7b22616374696f6e223a22616363657074222c22636f6e74656e74223a227b5c22636f6e6669726d5c22"
    "3a20747275657d227d7d7d"
)


def _rejected(**kwargs) -> str:
    with pytest.raises(McpError) as exc:
        unseal(**kwargs)
    assert exc.value.code == -32602
    return exc.value.message


def test_operation_digest_is_the_documented_hash():
    assert OP == "4af2610d95709b3e01345badaee8263602bde0d1b32737068c18eea196dcba81"


def test_known_vector_opens():
    assert unseal(SECRET, VECTOR, OP, "") == ANSWERS


def test_sealed_state_round_trips():
    state = seal(SECRET, ANSWERS, OP, principal_digest("Bearer abc"))

    assert unseal(SECRET, state, OP, principal_digest("Bearer abc")) == ANSWERS


def test_altered_state_is_rejected():
    state = bytearray(seal(SECRET, ANSWERS, OP, ""))
    state[-5] ^= 1

    assert "altered" in _rejected(secret=SECRET, state=bytes(state), operation=OP, principal="")


def test_state_signed_with_another_secret_is_rejected():
    state = seal(b"other-secret", ANSWERS, OP, "")

    assert "altered" in _rejected(secret=SECRET, state=state, operation=OP, principal="")


def test_expired_state_is_rejected():
    state = seal(SECRET, ANSWERS, OP, "", now=1_000.0)

    assert "expired" in _rejected(secret=SECRET, state=state, operation=OP, principal="", now=1_601.0)
    assert unseal(SECRET, state, OP, "", now=1_599.0) == ANSWERS


def test_state_for_another_request_is_rejected():
    other = operation_digest("tools/call", "deploy", '{"service": "db"}')
    state = seal(SECRET, ANSWERS, OP, "")

    assert "different request" in _rejected(secret=SECRET, state=state, operation=other, principal="")


def test_state_for_another_caller_is_rejected():
    state = seal(SECRET, ANSWERS, OP, principal_digest("Bearer alice"))

    assert "different caller" in _rejected(
        secret=SECRET, state=state, operation=OP, principal=principal_digest("Bearer bob")
    )


@pytest.mark.parametrize("junk", [b"", b"short", b"x" * 32, b"x" * 32 + b"not json"])
def test_garbage_is_rejected(junk):
    _rejected(secret=SECRET, state=junk, operation=OP, principal="")


def test_no_authorization_means_an_empty_principal():
    assert principal_digest(None) == ""
    assert principal_digest("") == ""
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_state.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'rapidmcp._v2_state'`.

- [ ] **Step 3: Implement**

`python/src/rapidmcp/_v2_state.py`:

```python
"""request_state — the server's memory of earlier answers, carried by the client.

v2 is stateless, so when a tool asks a second question the first answer has to
come back with the retry. It travels in ``request_state``, which passes through
the client and is therefore attacker-controlled: it is signed, bound to the
operation and the caller, and expires.

Wire format (the same in the TypeScript implementation):
32 bytes of HMAC-SHA256, then the UTF-8 JSON payload the MAC covers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from rapidmcp.errors import INVALID_PARAMS, McpError

STATE_TTL_SECONDS = 600
_MAC_BYTES = 32


def operation_digest(method: str, name: str, arguments_text: str) -> str:
    """Identify the request a state belongs to."""
    return hashlib.sha256(f"{method}\n{name}\n{arguments_text}".encode()).hexdigest()


def principal_digest(authorization: str | None) -> str:
    """Identify the caller a state belongs to; empty when the request carries no credentials."""
    return hashlib.sha256(authorization.encode()).hexdigest() if authorization else ""


def seal(
    secret: bytes,
    answers: dict[str, dict[str, str]],
    operation: str,
    principal: str,
    *,
    now: float | None = None,
    ttl: float = STATE_TTL_SECONDS,
) -> bytes:
    issued = time.time() if now is None else now
    payload = json.dumps(
        {"v": 1, "exp": int(issued + ttl), "op": operation, "sub": principal, "answers": answers},
        separators=(",", ":"),
    ).encode()
    return hmac.new(secret, payload, hashlib.sha256).digest() + payload


def _rejected(reason: str) -> McpError:
    return McpError(INVALID_PARAMS, f"Invalid request_state: {reason}")


def unseal(
    secret: bytes,
    state: bytes,
    operation: str,
    principal: str,
    *,
    now: float | None = None,
) -> dict[str, dict[str, str]]:
    """The answers inside *state*, or ``McpError(-32602)`` if it cannot be trusted."""
    mac, payload = state[:_MAC_BYTES], state[_MAC_BYTES:]
    expected = hmac.new(secret, payload, hashlib.sha256).digest()
    if len(mac) != _MAC_BYTES or not hmac.compare_digest(mac, expected):
        raise _rejected("it was not issued by this server, or has been altered")
    try:
        data = json.loads(payload)
        expires, op, sub, answers = data["exp"], data["op"], data["sub"], data["answers"]
    except (ValueError, KeyError, TypeError):
        raise _rejected("it is malformed") from None
    if expires < (time.time() if now is None else now):
        raise _rejected("it has expired")
    if op != operation:
        raise _rejected("it belongs to a different request")
    if sub != principal:
        raise _rejected("it belongs to a different caller")
    return answers
```

- [ ] **Step 4: Run, lint, commit**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_state.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp/_v2_state.py python/tests/test_v2_state.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): signed, bound, expiring request_state for v2 input rounds"
```

Expected: 13 tests pass.

---

### Task 2: Python server — `ctx.elicit()` on v2

**Files:**
- Modify: `python/src/rapidmcp/context.py` (v1 `elicit` accepts `key` and `url`)
- Modify: `python/src/rapidmcp/_v2_context.py`
- Modify: `python/src/rapidmcp/_v2_servicer.py` (`CallTool`)
- Modify: `python/src/rapidmcp/server.py` (`state_secret`)
- Test: `python/tests/test_v2_input_server.py`

**Interfaces:**
- Consumes: Task 1's `seal`, `unseal`, `operation_digest`, `principal_digest`.
- Produces: `RapidMCP(state_secret: str | bytes | None = None)`, with `server._state_secret: bytes` and `server._state_secret_configured: bool`; `_V2Context(meta, emit, answers=None)`; `_V2Context.elicit(message, schema=None, fields=None, timeout=None, key=None, url=None) -> ElicitationResult`; `_V2Context.input_responses -> dict[str, ElicitationResult]`; `rapidmcp._v2_context._NeedsInput(requests: dict[str, pb.InputRequest])`, an `McpError` subclass; `Context.elicit(..., key=None, url=None)` on v1 (`key` ignored, `url` raises `-32601`).

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_input_server.py`:

```python
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
    assert json.loads(request.form.requested_schema)["properties"] == {"confirm": {"type": "boolean"}}
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

    event = await _call(stub, "deploy", '{"service": "api"}', answers={"surprise": ("accept", "{}")})

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
    assert (request.WhichOneof("mode"), request.url.url) == ("url", "https://pay.example/session/42")
    assert done.complete.content[0].text == "payment accept"
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_input_server.py -q
```

Expected: FAIL — `TypeError: RapidMCP.__init__() got an unexpected keyword argument 'state_secret'`.

- [ ] **Step 3: The server option**

In `python/src/rapidmcp/server.py`, add `import secrets` and the parameter (after `host`):

```python
        host: str | None = None,
        state_secret: str | bytes | None = None,
    ) -> None:
        # Signs the request_state v2 tools hand to clients between input rounds.
        # Replicas behind one load balancer must share it.
        self._state_secret_configured = state_secret is not None
        if state_secret is None:
            self._state_secret = secrets.token_bytes(32)
        elif isinstance(state_secret, str):
            self._state_secret = state_secret.encode()
        else:
            self._state_secret = state_secret
```

- [ ] **Step 4: v1 `elicit` accepts the new arguments**

In `python/src/rapidmcp/context.py`, extend the signature of `Context.elicit` and reject URL mode:

```python
    async def elicit(
        self,
        message: str,
        schema: str | None = None,
        fields: dict | None = None,
        timeout: float | None = _DEFAULT_TIMEOUT,
        key: str | None = None,
        url: str | None = None,
    ) -> ElicitationResult:
```

In its docstring's `Args:` add:

```python
            key:     Name for this question on the v2 protocol; ignored on v1.
            url:     Ask the user to visit a URL instead of filling a form.
                     Needs the v2 protocol.
```

and as the first statement of the body:

```python
        if url is not None:
            raise McpError(METHOD_NOT_FOUND, "URL elicitation needs the v2 protocol")
```

Add `METHOD_NOT_FOUND` to the module's `rapidmcp.errors` import.

- [ ] **Step 5: `elicit` on the v2 context**

In `python/src/rapidmcp/_v2_context.py`, replace the imports and everything from `class _V2Context` to the end of the file's `elicit` stub as follows.

Imports:

```python
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp.context import Context
from rapidmcp.elicitation import ElicitationResult, build_elicitation_schema
from rapidmcp.errors import INTERNAL_ERROR, METHOD_NOT_FOUND, MISSING_CLIENT_CAPABILITY, McpError
```

Above the class:

```python
class _NeedsInput(McpError):
    """Raised by ``ctx.elicit()`` to end the call with ``input_required``.

    An ``McpError`` so the tool manager lets it through instead of turning it
    into tool output; the v2 servicer catches it before it could become an error.
    """

    def __init__(self, requests: dict[str, pb.InputRequest]) -> None:
        super().__init__(INTERNAL_ERROR, "The tool needs input from the client")
        self.requests = requests


def _to_result(answer: dict[str, str]) -> ElicitationResult:
    data: dict = {}
    if answer.get("content"):
        try:
            parsed = json.loads(answer["content"])
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            pass
    return ElicitationResult(action=answer.get("action", ""), data=data)
```

Constructor:

```python
    def __init__(
        self,
        meta: pb.RequestMeta,
        emit: Callable[[pb.CallToolEvent], Awaitable[None]],
        answers: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._meta = meta
        self._emit = emit
        self._answers = answers or {}
        self._elicit_calls = 0
```

Replace the `elicit` stub with:

```python
    @property
    def input_responses(self) -> dict[str, ElicitationResult]:
        """The answers this request carries, by key."""
        return {key: _to_result(answer) for key, answer in self._answers.items()}

    async def elicit(
        self,
        message: str,
        schema: str | None = None,
        fields: dict | None = None,
        timeout: float | None = None,
        key: str | None = None,
        url: str | None = None,
    ) -> ElicitationResult:
        """Ask the user a question.

        The first time, this ends the call: the client asks the user and calls
        the tool again, and the tool **runs again from the top**. On that run
        this returns the answer. So ask before doing anything with side effects.
        ``timeout`` is ignored; the client decides how long the user gets.
        """
        if url is not None and (schema is not None or fields is not None):
            raise ValueError("Provide 'url' or a form ('schema' / 'fields'), not both")
        if fields is not None and schema is not None:
            raise ValueError("Provide either 'schema' or 'fields', not both")

        mode = "url" if url is not None else "form"
        capabilities = self._meta.client_capabilities
        if not capabilities.HasField("elicitation") or not getattr(capabilities.elicitation, mode):
            raise McpError(
                MISSING_CLIENT_CAPABILITY,
                f"Client does not support {mode} elicitation",
                data={"required_capabilities": [f"elicitation.{mode}"]},
            )

        name = key or f"elicit-{self._elicit_calls}"
        self._elicit_calls += 1
        answer = self._answers.get(name)
        if answer is not None:
            return _to_result(answer)

        request = pb.ElicitRequest(message=message)
        if url is not None:
            request.url.CopyFrom(pb.ElicitUrl(url=url))
        else:
            resolved = schema or (build_elicitation_schema(fields) if fields else "")
            request.form.CopyFrom(pb.ElicitForm(requested_schema=resolved))
        raise _NeedsInput({name: pb.InputRequest(elicit=request)})
```

- [ ] **Step 6: `CallTool` handles the rounds**

In `python/src/rapidmcp/_v2_servicer.py`:

Imports — add:

```python
from rapidmcp._v2_context import LOG_LEVELS, _NeedsInput, _V2Context
from rapidmcp._v2_state import operation_digest, principal_digest, seal, unseal
```

(the first replaces the existing `_v2_context` import).

Add two helpers to the class (after `_check_meta`):

```python
    def _principal(self, context) -> str:
        """Who a request_state belongs to: the caller's credentials, when the server checks them."""
        if self._server._auth is None:
            return ""
        return principal_digest(dict(context.invocation_metadata()).get("authorization"))

    def _seal(self, answers: dict, operation: str, principal: str) -> bytes:
        if not self._server._state_secret_configured and not self._warned_about_secret:
            self._warned_about_secret = True
            logger.warning(
                "Issuing request_state signed with a secret generated at start-up. "
                "Set RapidMCP(state_secret=...) so every replica can verify it."
            )
        return seal(self._server._state_secret, answers, operation, principal)
```

and `self._warned_about_secret = False` in `__init__`.

In `CallTool`, replace the body of `work` with:

```python
        async def work(emit):
            arguments = _parse_tool_arguments(name, request.arguments)
            operation = operation_digest("tools/call", name, request.arguments)
            principal = self._principal(context)

            # Earlier answers come back inside the state; the latest ones in the request.
            answers: dict[str, dict[str, str]] = {}
            if request.request_state:
                answers = unseal(
                    self._server._state_secret, request.request_state, operation, principal
                )
            for key, response in request.input_responses.items():
                if response.HasField("elicit"):
                    answers[key] = {
                        "action": response.elicit.action,
                        "content": response.elicit.content,
                    }

            ctx = _V2Context(request.meta, emit, answers)
            try:
                result = await self._server._dispatch_tool(name, arguments, ctx)
            except _NeedsInput as need:
                return pb.CallToolEvent(
                    input_required=pb.InputRequired(
                        input_requests=need.requests,
                        request_state=self._seal(answers, operation, principal),
                    )
                )
            structured = ctx._structured_content
            return pb.CallToolEvent(
                complete=pb.CallToolResult(
                    meta=self._result_meta(),
                    content=[
                        pb.ContentItem(
                            type=c.type, text=c.text, data=c.data, mime_type=c.mime_type, uri=c.uri
                        )
                        for c in result.content
                    ],
                    is_error=result.is_error,
                    structured_content=json.dumps(structured) if structured is not None else "",
                )
            )
```

- [ ] **Step 7: Run, lint, commit**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_input_server.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp python/tests/test_v2_input_server.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): ctx.elicit() on v2 through input-required rounds"
```

Expected: 14 tests pass in the new file; the full suite stays green.

---

### Task 3: Python client — answering input rounds

**Files:**
- Modify: `python/src/rapidmcp/errors.py` (`INPUT_LOOP = 508`)
- Modify: `python/src/rapidmcp/types.py` (`ElicitRequestInfo`)
- Modify: `python/src/rapidmcp/__init__.py` (export `ElicitRequestInfo`)
- Modify: `python/src/rapidmcp/_v2_client.py`
- Modify: `python/src/rapidmcp/client.py` (`set_elicitation_handler(handler, *, url=False)`)
- Test: `python/tests/test_v2_input_client.py`

**Interfaces:**
- Produces: `rapidmcp.ElicitRequestInfo(message: str, schema: str, mode: str, url: str)`, the object a v2 round passes to the elicitation handler (a v1 request has the same `.message` and `.schema`); `Client.set_elicitation_handler(handler, *, url: bool = False)`; `_V2Transport(channel, metadata, timeout, elicitation, notifications)` where `elicitation: Callable[[], tuple[handler | None, bool]]`; `rapidmcp.errors.INPUT_LOOP = 508`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_input_client.py`:

```python
"""A v2 client answers input rounds with the elicitation handler it already has."""

import pytest

from rapidmcp import BoolField, Client, Context, RapidMCP, StringField
from rapidmcp._generated import mcp_pb2
from rapidmcp.errors import McpError


@pytest.fixture
async def server():
    srv = RapidMCP(name="input", version="1.0", state_secret="k")

    @srv.tool()
    async def deploy(service: str, ctx: Context) -> str:
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
    async def pay(ctx: Context) -> str:
        answer = await ctx.elicit("Complete the payment", url="https://pay.example/session/42")
        return f"payment {answer.action}"

    @srv.tool()
    async def never_satisfied(ctx: Context) -> str:
        for attempt in range(100):
            await ctx.elicit("Again?", fields={"ok": BoolField()}, key=f"attempt-{attempt}")
        return "unreachable"

    async with srv:
        yield srv


def _reply(action="accept", content="{}"):
    return mcp_pb2.ElicitationResponse(action=action, content=content)


@pytest.mark.parametrize("mode", ["legacy", "modern"])
async def test_one_tool_works_on_both_protocol_versions(server, mode):
    client = Client(f"localhost:{server.port}", mode=mode)
    seen: list[str] = []

    async def handler(request):
        seen.append(request.message)
        return _reply(content='{"confirm": true}')

    client.set_elicitation_handler(handler)
    async with client:
        result = await client.call_tool("deploy", {"service": "api"})

    assert result.content[0].text == "deployed api confirm=True"
    assert seen == ["Deploy to production?"]


async def test_handler_gets_the_form_schema_on_v2(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    requests = []

    async def handler(request):
        requests.append(request)
        return _reply()

    client.set_elicitation_handler(handler)
    async with client:
        await client.call_tool("deploy", {"service": "api"})

    assert (requests[0].mode, requests[0].url) == ("form", "")
    assert '"confirm"' in requests[0].schema


async def test_two_questions_are_asked_in_order(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    asked: list[str] = []

    async def handler(request):
        asked.append(request.message)
        content = '{"name": "Ada"}' if request.message == "Name?" else '{"colour": "green"}'
        return _reply(content=content)

    client.set_elicitation_handler(handler)
    async with client:
        result = await client.call_tool("two_questions")

    assert asked == ["Name?", "Colour?"]
    assert result.content[0].text == "Ada likes green"


async def test_declining_reaches_the_tool(server):
    client = Client(f"localhost:{server.port}", mode="modern")

    async def handler(request):
        return _reply(action="decline", content="")

    client.set_elicitation_handler(handler)
    async with client:
        result = await client.call_tool("deploy", {"service": "api"})

    assert result.content[0].text == "not deployed (decline)"


async def test_url_mode_reaches_a_handler_that_declared_it(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    requests = []

    async def handler(request):
        requests.append(request)
        return _reply(content="")

    client.set_elicitation_handler(handler, url=True)
    async with client:
        result = await client.call_tool("pay")

    assert (requests[0].mode, requests[0].url) == ("url", "https://pay.example/session/42")
    assert result.content[0].text == "payment accept"


async def test_url_mode_without_declaring_it_is_a_missing_capability(server):
    client = Client(f"localhost:{server.port}", mode="modern")

    async def handler(request):
        return _reply()

    client.set_elicitation_handler(handler)
    async with client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("pay")

    assert exc.value.code == -32021
    assert exc.value.data == {"required_capabilities": ["elicitation.url"]}


async def test_no_handler_is_a_missing_capability(server):
    async with Client(f"localhost:{server.port}", mode="modern") as client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("deploy", {"service": "api"})

    assert exc.value.code == -32021


async def test_client_gives_up_on_a_server_that_never_stops_asking(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    rounds = 0

    async def handler(request):
        nonlocal rounds
        rounds += 1
        return _reply()

    client.set_elicitation_handler(handler)
    async with client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("never_satisfied")

    assert exc.value.code == 508
    assert rounds == 10
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_input_client.py -q
```

Expected: the `modern` tests fail with `McpError: The server asked for input, which this client cannot give yet` (and `TypeError` for `url=True`); the `legacy` parametrization passes.

- [ ] **Step 3: Supporting types**

`python/src/rapidmcp/errors.py`, with the other local codes:

```python
INPUT_LOOP = 508
```

`python/src/rapidmcp/types.py`, before the `ListResult` section:

```python
@dataclass
class ElicitRequestInfo:
    """What a v2 server asks the user; passed to the elicitation handler.

    Has the same ``message`` and ``schema`` attributes as the v1 request, so a
    handler written for v1 works unchanged.
    """

    message: str
    schema: str = ""  # JSON Schema text, form mode only
    mode: str = "form"  # "form" or "url"
    url: str = ""  # URL mode only
```

`python/src/rapidmcp/__init__.py`: add `ElicitRequestInfo` to the `rapidmcp.types` import list and to `__all__`.

- [ ] **Step 4: The transport runs the rounds**

In `python/src/rapidmcp/_v2_client.py`:

Imports: add `INPUT_LOOP` and `MISSING_CLIENT_CAPABILITY` to the `rapidmcp.errors` import, `ElicitRequestInfo` to the `rapidmcp.types` import, and drop `METHOD_NOT_FOUND` if nothing else uses it. Add below `PROTOCOL_VERSION`:

```python
MAX_INPUT_ROUNDS = 10
```

Constructor — the fourth parameter changes meaning:

```python
    def __init__(
        self,
        channel: aio.Channel,
        metadata: list[tuple[str, str]],
        timeout: float,
        elicitation: Callable[[], tuple[Callable | None, bool]],
        notifications: NotificationRegistry,
    ) -> None:
        self._stub = mcp_v2_pb2_grpc.McpStub(channel)
        self._metadata = metadata
        self._timeout = timeout
        # () -> (handler or None, whether it also handles URL mode)
        self._elicitation = elicitation
        self._notifications = notifications
        self._progress_tokens = itertools.count(1)
```

In `_meta`, replace the capability lines:

```python
        capabilities = pb.ClientCapabilities()
        handler, handles_url = self._elicitation()
        if handler is not None:
            capabilities.elicitation.CopyFrom(
                pb.ElicitationCapability(form=True, url=handles_url)
            )
```

In `_stream`, replace the `complete` and `input_required` branches so the caller learns which terminal event arrived:

```python
                elif kind == "complete":
                    return kind, event.complete
                elif kind == "input_required":
                    return kind, event.input_required
```

Replace `call_tool`, `read_resource` and `get_prompt` with:

```python
    async def _answer(self, request: pb.InputRequest) -> pb.InputResponse:
        handler, _ = self._elicitation()
        if request.WhichOneof("request") != "elicit" or handler is None:
            raise McpError(
                MISSING_CLIENT_CAPABILITY, "The server asked for input this client cannot give"
            )
        asked = request.elicit
        mode = asked.WhichOneof("mode") or "form"
        reply = await handler(
            ElicitRequestInfo(
                message=asked.message,
                schema=asked.form.requested_schema if mode == "form" else "",
                mode=mode,
                url=asked.url.url if mode == "url" else "",
            )
        )
        return pb.InputResponse(
            elicit=pb.ElicitResult(action=reply.action, content=getattr(reply, "content", "") or "")
        )

    async def _run(self, method, build_request, timeout: float | None = None):
        """Call until the server has what it needs, answering its questions in between."""
        responses: dict[str, pb.InputResponse] = {}
        state = b""
        for round_number in range(MAX_INPUT_ROUNDS + 1):
            kind, message = await self._stream(method, build_request(responses, state), timeout)
            if kind == "complete":
                return message
            if round_number == MAX_INPUT_ROUNDS:
                break
            responses = {
                key: await self._answer(asked) for key, asked in message.input_requests.items()
            }
            state = message.request_state
        raise McpError(
            INPUT_LOOP, f"The server asked for input more than {MAX_INPUT_ROUNDS} times"
        )

    async def call_tool(
        self, name: str, arguments: dict | None, timeout: float | None
    ) -> CallToolResult:
        text = json.dumps(arguments or {})  # the same text every round: the state is bound to it

        def build(responses, state):
            return pb.CallToolRequest(
                meta=self._meta(events=True),
                name=name,
                arguments=text,
                input_responses=responses,
                request_state=state,
            )

        return _convert_call_tool_result_v2(await self._run(self._stub.CallTool, build, timeout))

    async def read_resource(self, uri: str) -> ReadResourceResult:
        def build(responses, state):
            return pb.ReadResourceRequest(
                meta=self._meta(), uri=uri, input_responses=responses, request_state=state
            )

        return _convert_read_resource_result(await self._run(self._stub.ReadResource, build))

    async def get_prompt(self, name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        def build(responses, state):
            return pb.GetPromptRequest(
                meta=self._meta(),
                name=name,
                arguments=arguments or {},
                input_responses=responses,
                request_state=state,
            )

        return _convert_get_prompt_result(await self._run(self._stub.GetPrompt, build))
```

- [ ] **Step 5: The client declares URL support**

In `python/src/rapidmcp/client.py`:

```python
    def set_elicitation_handler(self, handler, *, url: bool = False) -> None:
        """Answer the server's questions.

        *handler* receives the request (``.message``, ``.schema``; on v2 also
        ``.mode`` and ``.url``) and returns an object with ``.action`` and
        ``.content``. Pass ``url=True`` if it can also send the user to a URL.
        """
        self._elicitation_handler = handler
        self._elicitation_url = url
```

Add `self._elicitation_url = False` next to `self._elicitation_handler = None` in `__init__`, and in `_connect_v2` replace the `supports_elicitation=...` argument with:

```python
            elicitation=lambda: (self._elicitation_handler, self._elicitation_url),
```

- [ ] **Step 6: Run, lint, commit**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_input_client.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp python/tests/test_v2_input_client.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): v2 clients answer input rounds with their elicitation handler"
```

Expected: 9 tests pass; the full suite stays green.

---

### Task 4: TypeScript — the request-state codec

**Files:**
- Create: `typescript/src/v2/state.ts`
- Test: `typescript/tests/v2-state.test.ts`

**Interfaces:**
- Produces: `operationDigest(method: string, name: string, argumentsText: string): string`; `principalDigest(authorization: string | undefined): string`; `seal(secret: Uint8Array, answers: Answers, operation: string, principal: string, opts?: { now?: number; ttl?: number }): Uint8Array`; `unseal(secret: Uint8Array, state: Uint8Array, operation: string, principal: string, opts?: { now?: number }): Answers`; `type Answers = Record<string, { action: string; content: string }>`. `now` is in seconds.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-state.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { McpError } from "../src/errors.js";
import { operationDigest, principalDigest, seal, unseal } from "../src/v2/state.js";

const SECRET = new TextEncoder().encode("shared-secret");
const OP = operationDigest("tools/call", "deploy", '{"service": "api"}');
const ANSWERS = { "elicit-0": { action: "accept", content: '{"confirm": true}' } };

// The same bytes as python/tests/test_v2_state.py: this is what keeps the two
// implementations able to read each other's state.
const VECTOR = Buffer.from(
  "c16779a64fde2278de3d869db2efc16ee1cdda58fab040d26f7d9e16552ae276" +
    "7b2276223a312c22657870223a343130323434343830302c226f70223a2234616632363130643935373039" +
    "623365303133343562616461656538323633363032626465306431623332373337303638633138656561" +
    "313936646362613831222c22737562223a22222c22616e7377657273223a7b22656c696369742d30223a" +
    "7b22616374696f6e223a22616363657074222c22636f6e74656e74223a227b5c22636f6e6669726d5c22" +
    "3a20747275657d227d7d7d",
  "hex",
);

function rejected(fn: () => unknown): string {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(-32602);
    return (err as McpError).message;
  }
  throw new Error("expected the state to be rejected");
}

describe("request_state", () => {
  it("computes the documented operation digest", () => {
    expect(OP).toBe("4af2610d95709b3e01345badaee8263602bde0d1b32737068c18eea196dcba81");
  });

  it("opens the vector shared with the Python implementation", () => {
    expect(unseal(SECRET, VECTOR, OP, "")).toEqual(ANSWERS);
  });

  it("round-trips sealed state", () => {
    const who = principalDigest("Bearer abc");

    expect(unseal(SECRET, seal(SECRET, ANSWERS, OP, who), OP, who)).toEqual(ANSWERS);
  });

  it("rejects altered state", () => {
    const state = seal(SECRET, ANSWERS, OP, "");
    state[state.length - 5] ^= 1;

    expect(rejected(() => unseal(SECRET, state, OP, ""))).toContain("altered");
  });

  it("rejects state signed with another secret", () => {
    const state = seal(new TextEncoder().encode("other-secret"), ANSWERS, OP, "");

    expect(rejected(() => unseal(SECRET, state, OP, ""))).toContain("altered");
  });

  it("rejects expired state", () => {
    const state = seal(SECRET, ANSWERS, OP, "", { now: 1000 });

    expect(rejected(() => unseal(SECRET, state, OP, "", { now: 1601 }))).toContain("expired");
    expect(unseal(SECRET, state, OP, "", { now: 1599 })).toEqual(ANSWERS);
  });

  it("rejects state that belongs to another request", () => {
    const other = operationDigest("tools/call", "deploy", '{"service": "db"}');

    expect(rejected(() => unseal(SECRET, seal(SECRET, ANSWERS, OP, ""), other, ""))).toContain(
      "different request",
    );
  });

  it("rejects state that belongs to another caller", () => {
    const state = seal(SECRET, ANSWERS, OP, principalDigest("Bearer alice"));

    expect(rejected(() => unseal(SECRET, state, OP, principalDigest("Bearer bob")))).toContain(
      "different caller",
    );
  });

  it.each([
    ["empty", new Uint8Array()],
    ["short", new TextEncoder().encode("short")],
    ["mac only", new Uint8Array(32).fill(120)],
  ])("rejects garbage (%s)", (_label, junk) => {
    rejected(() => unseal(SECRET, junk, OP, ""));
  });

  it("uses an empty principal when there are no credentials", () => {
    expect(principalDigest(undefined)).toBe("");
    expect(principalDigest("")).toBe("");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-state.test.ts
```

Expected: FAIL — `Failed to resolve import "../src/v2/state.js"`.

- [ ] **Step 3: Implement**

`typescript/src/v2/state.ts`:

```typescript
/**
 * request_state — the server's memory of earlier answers, carried by the client.
 *
 * v2 is stateless, so when a tool asks a second question the first answer has
 * to come back with the retry. It travels in `request_state`, which passes
 * through the client and is therefore attacker-controlled: it is signed, bound
 * to the operation and the caller, and expires.
 *
 * Wire format (the same in the Python implementation):
 * 32 bytes of HMAC-SHA256, then the UTF-8 JSON payload the MAC covers.
 */
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { ErrorCode, McpError } from "../errors.js";

export type Answers = Record<string, { action: string; content: string }>;

export const STATE_TTL_SECONDS = 600;
const MAC_BYTES = 32;

/** Identify the request a state belongs to. */
export function operationDigest(method: string, name: string, argumentsText: string): string {
  return createHash("sha256").update(`${method}\n${name}\n${argumentsText}`).digest("hex");
}

/** Identify the caller a state belongs to; empty when the request carries no credentials. */
export function principalDigest(authorization: string | undefined): string {
  return authorization ? createHash("sha256").update(authorization).digest("hex") : "";
}

const nowSeconds = () => Date.now() / 1000;

export function seal(
  secret: Uint8Array,
  answers: Answers,
  operation: string,
  principal: string,
  opts: { now?: number; ttl?: number } = {},
): Uint8Array {
  const issued = opts.now ?? nowSeconds();
  const payload = Buffer.from(
    JSON.stringify({
      v: 1,
      exp: Math.floor(issued + (opts.ttl ?? STATE_TTL_SECONDS)),
      op: operation,
      sub: principal,
      answers,
    }),
  );
  const mac = createHmac("sha256", secret).update(payload).digest();
  return new Uint8Array(Buffer.concat([mac, payload]));
}

function rejected(reason: string): McpError {
  return new McpError(ErrorCode.InvalidParams, `Invalid request_state: ${reason}`);
}

/** The answers inside *state*, or McpError(-32602) if it cannot be trusted. */
export function unseal(
  secret: Uint8Array,
  state: Uint8Array,
  operation: string,
  principal: string,
  opts: { now?: number } = {},
): Answers {
  const bytes = Buffer.from(state);
  const mac = bytes.subarray(0, MAC_BYTES);
  const payload = bytes.subarray(MAC_BYTES);
  const expected = createHmac("sha256", secret).update(payload).digest();
  if (mac.length !== MAC_BYTES || !timingSafeEqual(mac, expected)) {
    throw rejected("it was not issued by this server, or has been altered");
  }
  let data: { exp?: unknown; op?: unknown; sub?: unknown; answers?: unknown };
  try {
    data = JSON.parse(payload.toString("utf8"));
  } catch {
    throw rejected("it is malformed");
  }
  if (typeof data?.exp !== "number" || typeof data.answers !== "object" || data.answers === null) {
    throw rejected("it is malformed");
  }
  if (data.exp < (opts.now ?? nowSeconds())) throw rejected("it has expired");
  if (data.op !== operation) throw rejected("it belongs to a different request");
  if (data.sub !== principal) throw rejected("it belongs to a different caller");
  return data.answers as Answers;
}
```

- [ ] **Step 4: Run and commit**

```powershell
cd typescript; npx vitest run tests/v2-state.test.ts; npx tsc -p tsconfig.build.json --noEmit
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src/v2/state.ts typescript/tests/v2-state.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): signed, bound, expiring request_state for v2 input rounds"
```

Expected: 12 tests pass.

---

### Task 5: TypeScript server — `ctx.elicit()` on v2

**Files:**
- Modify: `typescript/src/context.ts` (v1 `elicit` accepts `key` and `url`)
- Modify: `typescript/src/v2/context.ts`
- Modify: `typescript/src/v2/servicer.ts`
- Modify: `typescript/src/server.ts` (`stateSecret`, pass `auth` presence)
- Test: `typescript/tests/v2-input-server.test.ts`

**Interfaces:**
- Consumes: Task 4's `seal`, `unseal`, `operationDigest`, `principalDigest`, `Answers`.
- Produces: `RapidMCPOptions.stateSecret?: string | Uint8Array`; `McpV2ServicerOptions.stateSecret: Uint8Array`, `.stateSecretConfigured: boolean`, `.authEnabled: boolean`; `class NeedsInput extends McpError` with `requests`; `V2Context` constructor `(meta, emit, signal, answers?)`; `V2Context.elicit(message, schema, opts?: { key?: string; url?: string }) => Promise<{ action: string; content: string }>`; `V2Context.inputResponses`; `ReplyOptions.key?` and `.url?` on the v1 context.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-input-server.test.ts`:

```typescript
import { describe, it, expect, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status } from "nice-grpc-common";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import {
  ErrorData,
  McpDefinition,
  type CallToolEvent,
  type McpClient,
  type RequestMeta,
} from "../generated/mcp_v2.js";
import { operationDigest, seal } from "../src/v2/state.js";

const FORM = { elicitation: { form: true, url: false }, extensions: {} };
const FORM_AND_URL = { elicitation: { form: true, url: true }, extensions: {} };
const NONE = { elicitation: undefined, extensions: {} };

const meta = (clientCapabilities: RequestMeta["clientCapabilities"] = FORM): RequestMeta => ({
  protocolVersion: "2026-07-28",
  clientCapabilities,
  clientInfo: undefined,
});

type Answer = [action: string, content: string];

describe("ctx.elicit() on v2", () => {
  const servers: RapidMCP[] = [];
  const channels: Channel[] = [];

  async function start(opts: Partial<RapidMCPOptions> = {}) {
    const runs: string[] = [];
    const server = new RapidMCP({ name: "input", stateSecret: "s3cret-key", ...opts });
    server.addTool({
      name: "deploy",
      execute: async (args: any, ctx: any) => {
        runs.push(args.service);
        const answer = await ctx.elicit("Deploy to production?", {
          type: "object",
          properties: { confirm: { type: "boolean" } },
        });
        if (answer.action !== "accept") return `not deployed (${answer.action})`;
        return `deployed ${args.service} confirm=${JSON.parse(answer.content || "{}").confirm}`;
      },
    });
    server.addTool({
      name: "two_questions",
      execute: async (_a: unknown, ctx: any) => {
        const first = await ctx.elicit("Name?", { type: "object" });
        const second = await ctx.elicit("Colour?", { type: "object" });
        return `${JSON.parse(first.content).name} likes ${JSON.parse(second.content).colour}`;
      },
    });
    server.addTool({
      name: "named",
      execute: async (_a: unknown, ctx: any) => {
        const answer = await ctx.elicit("Sure?", { type: "object" }, { key: "confirmation" });
        return `${answer.action} / seen=${Object.keys(ctx.inputResponses).sort().join(",")}`;
      },
    });
    server.addTool({
      name: "pay",
      execute: async (_a: unknown, ctx: any) => {
        const answer = await ctx.elicit("Complete the payment", {}, { url: "https://pay.example/session/42" });
        return `payment ${answer.action}`;
      },
    });
    const port = await server.listen();
    const channel = createChannel(`127.0.0.1:${port}`);
    servers.push(server);
    channels.push(channel);
    return { runs, v2: createClientFactory().create(McpDefinition, channel) as McpClient };
  }

  afterEach(async () => {
    for (const channel of channels.splice(0)) channel.close();
    for (const server of servers.splice(0)) await server.close();
  });

  /** One round; returns the terminal event. */
  async function call(
    v2: McpClient,
    name: string,
    args = "{}",
    extra: { answers?: Record<string, Answer>; state?: Uint8Array; meta?: RequestMeta; options?: object } = {},
  ): Promise<NonNullable<CallToolEvent["event"]>> {
    const inputResponses = Object.fromEntries(
      Object.entries(extra.answers ?? {}).map(([key, [action, content]]) => [
        key,
        { response: { $case: "elicit" as const, elicit: { action, content } } },
      ]),
    );
    let last: CallToolEvent["event"];
    for await (const event of v2.callTool(
      { meta: extra.meta ?? meta(), name, arguments: args, inputResponses, requestState: extra.state ?? new Uint8Array() },
      extra.options ?? {},
    )) {
      last = event.event;
    }
    return last!;
  }

  async function failure(run: (onTrailer: (t: Metadata) => void) => Promise<unknown>) {
    let trailer = new Metadata();
    const err = await run((t) => (trailer = t)).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ClientError);
    return {
      code: Number(trailer.get("mcp-error-code")),
      message: (err as ClientError).details,
      status: (err as ClientError).code,
      trailer,
    };
  }

  const state = (event: NonNullable<CallToolEvent["event"]>) =>
    event.$case === "inputRequired" ? event.inputRequired.requestState : new Uint8Array();

  it("asks the question on the first round", async () => {
    const { v2 } = await start();

    const event = await call(v2, "deploy", '{"service":"api"}');

    expect(event.$case).toBe("inputRequired");
    const required = (event as any).inputRequired;
    expect(Object.keys(required.inputRequests)).toEqual(["elicit-0"]);
    const request = required.inputRequests["elicit-0"].request.elicit;
    expect(request.message).toBe("Deploy to production?");
    expect(request.mode.$case).toBe("form");
    expect(JSON.parse(request.mode.form.requestedSchema).properties).toEqual({ confirm: { type: "boolean" } });
    expect(required.requestState.length).toBeGreaterThan(32);
  });

  it("completes the call when retried with the answer", async () => {
    const { v2 } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');

    const second = await call(v2, "deploy", '{"service":"api"}', {
      answers: { "elicit-0": ["accept", '{"confirm": true}'] },
      state: state(first),
    });

    expect((second as any).complete.content[0].text).toBe("deployed api confirm=true");
  });

  it("hands a declined question to the tool as a decline", async () => {
    const { v2 } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');

    const second = await call(v2, "deploy", '{"service":"api"}', {
      answers: { "elicit-0": ["decline", ""] },
      state: state(first),
    });

    expect([(second as any).complete.isError, (second as any).complete.content[0].text]).toEqual([
      false,
      "not deployed (decline)",
    ]);
  });

  it("runs the tool again from the top each round", async () => {
    const { v2, runs } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');
    await call(v2, "deploy", '{"service":"api"}', { answers: { "elicit-0": ["accept", "{}"] }, state: state(first) });

    expect(runs).toEqual(["api", "api"]);
  });

  it("keeps the first answer until the third call", async () => {
    const { v2 } = await start();
    const one = await call(v2, "two_questions");
    const two = await call(v2, "two_questions", "{}", {
      answers: { "elicit-0": ["accept", '{"name": "Ada"}'] },
      state: state(one),
    });
    const three = await call(v2, "two_questions", "{}", {
      answers: { "elicit-1": ["accept", '{"colour": "green"}'] }, // only the latest answer
      state: state(two),
    });

    expect(Object.keys((one as any).inputRequired.inputRequests)).toEqual(["elicit-0"]);
    expect(Object.keys((two as any).inputRequired.inputRequests)).toEqual(["elicit-1"]);
    expect((three as any).complete.content[0].text).toBe("Ada likes green");
  });

  it("ignores an answer nobody asked for", async () => {
    const { v2 } = await start();

    const event = await call(v2, "deploy", '{"service":"api"}', { answers: { surprise: ["accept", "{}"] } });

    expect(event.$case).toBe("inputRequired");
    expect(Object.keys((event as any).inputRequired.inputRequests)).toEqual(["elicit-0"]);
  });

  it("supports an explicit key and exposes the answers", async () => {
    const { v2 } = await start();
    const first = await call(v2, "named");
    const second = await call(v2, "named", "{}", {
      answers: { confirmation: ["accept", '{"ok": true}'] },
      state: state(first),
    });

    expect(Object.keys((first as any).inputRequired.inputRequests)).toEqual(["confirmation"]);
    expect((second as any).complete.content[0].text).toBe("accept / seen=confirmation");
  });

  it("rejects altered state", async () => {
    const { v2 } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');
    const tampered = new Uint8Array(state(first));
    tampered[tampered.length - 3] ^= 1;

    const { code, message } = await failure((onTrailer) =>
      call(v2, "deploy", '{"service":"api"}', { state: tampered, options: { onTrailer } }),
    );

    expect(code).toBe(-32602);
    expect(message.startsWith("Invalid request_state:")).toBe(true);
  });

  it("rejects state replayed on other arguments or another tool", async () => {
    const { v2 } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');

    const otherArguments = await failure((onTrailer) =>
      call(v2, "deploy", '{"service":"db"}', { state: state(first), options: { onTrailer } }),
    );
    const otherTool = await failure((onTrailer) =>
      call(v2, "two_questions", "{}", { state: state(first), options: { onTrailer } }),
    );

    expect([otherArguments.code, otherTool.code]).toEqual([-32602, -32602]);
    expect(otherArguments.message).toContain("different request");
    expect(otherTool.message).toContain("different request");
  });

  it("rejects expired state", async () => {
    const { v2 } = await start();
    const args = '{"service":"api"}';
    const stale = seal(
      new TextEncoder().encode("s3cret-key"),
      {},
      operationDigest("tools/call", "deploy", args),
      "",
      { now: 1000 },
    );

    const { code, message } = await failure((onTrailer) =>
      call(v2, "deploy", args, { state: stale, options: { onTrailer } }),
    );

    expect([code, message.includes("expired")]).toEqual([-32602, true]);
  });

  it("rejects state presented by another caller", async () => {
    const { v2 } = await start({ auth: (token) => token === "alice" || token === "bob" });
    const alice = Metadata({ authorization: "Bearer alice" });
    const bob = Metadata({ authorization: "Bearer bob" });
    const first = await call(v2, "deploy", '{"service":"api"}', { options: { metadata: alice } });

    const { code, message } = await failure((onTrailer) =>
      call(v2, "deploy", '{"service":"api"}', { state: state(first), options: { metadata: bob, onTrailer } }),
    );
    const sameCaller = await call(v2, "deploy", '{"service":"api"}', {
      answers: { "elicit-0": ["accept", "{}"] },
      state: state(first),
      options: { metadata: alice },
    });

    expect([code, message.includes("different caller")]).toEqual([-32602, true]);
    expect(sameCaller.$case).toBe("complete");
  });

  it("lets any replica with the same secret finish the call", async () => {
    const a = await start();
    const b = await start();
    const first = await call(a.v2, "deploy", '{"service":"api"}');

    const second = await call(b.v2, "deploy", '{"service":"api"}', {
      answers: { "elicit-0": ["accept", '{"confirm": true}'] },
      state: state(first),
    });

    expect((second as any).complete.content[0].text).toBe("deployed api confirm=true");
    expect([a.runs, b.runs]).toEqual([["api"], ["api"]]);
  });

  it("reports a missing capability when the client cannot be asked", async () => {
    const { v2 } = await start();

    const { code, status, trailer } = await failure((onTrailer) =>
      call(v2, "deploy", '{"service":"api"}', { meta: meta(NONE), options: { onTrailer } }),
    );

    expect([code, status]).toEqual([-32021, Status.FAILED_PRECONDITION]);
    expect(ErrorData.decode(trailer.get("mcp-error-data-bin")!).requiredCapabilities).toEqual([
      "elicitation.form",
    ]);
  });

  it("needs the url capability for url mode", async () => {
    const { v2 } = await start();

    const denied = await failure((onTrailer) => call(v2, "pay", "{}", { options: { onTrailer } }));
    const asked = await call(v2, "pay", "{}", { meta: meta(FORM_AND_URL) });
    const done = await call(v2, "pay", "{}", {
      meta: meta(FORM_AND_URL),
      answers: { "elicit-0": ["accept", ""] },
      state: state(asked),
    });

    expect(ErrorData.decode(denied.trailer.get("mcp-error-data-bin")!).requiredCapabilities).toEqual([
      "elicitation.url",
    ]);
    const request = (asked as any).inputRequired.inputRequests["elicit-0"].request.elicit;
    expect([request.mode.$case, request.mode.url.url]).toEqual(["url", "https://pay.example/session/42"]);
    expect((done as any).complete.content[0].text).toBe("payment accept");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-input-server.test.ts
```

Expected: every test fails — `ctx.elicit() is not available on the v2 protocol yet` surfaces as `-32601` / an error result.

- [ ] **Step 3: v1 `elicit` accepts the new options**

In `typescript/src/context.ts`, extend `ReplyOptions`:

```typescript
export interface ReplyOptions {
  /** Milliseconds to wait for the client's reply (default 30 000). */
  timeout?: number;
  /** Name for this question on the v2 protocol; ignored on v1. */
  key?: string;
  /** Ask the user to visit a URL instead of filling a form. Needs the v2 protocol. */
  url?: string;
}
```

and add as the first statement of `elicit`:

```typescript
    if (opts.url !== undefined) {
      throw new McpError(ErrorCode.MethodNotFound, "URL elicitation needs the v2 protocol");
    }
```

- [ ] **Step 4: `elicit` on the v2 context**

In `typescript/src/v2/context.ts`:

Add to the imports:

```typescript
import type { CallToolEvent, DeepPartial, InputRequest, RequestMeta } from "../../generated/mcp_v2.js";
import type { Answers } from "./state.js";
```

(the first replaces the existing generated import). Above the class:

```typescript
/**
 * Thrown by ctx.elicit() to end the call with input_required. An McpError so
 * the tool manager lets it through instead of turning it into tool output; the
 * v2 servicer catches it before it could become an error.
 */
export class NeedsInput extends McpError {
  constructor(public readonly requests: Record<string, InputRequest>) {
    super(ErrorCode.InternalError, "The tool needs input from the client");
    this.name = "NeedsInput";
  }
}
```

Replace the constructor with:

```typescript
  private _elicitCalls = 0;

  constructor(
    private readonly _meta: RequestMeta,
    private readonly _emit: (event: DeepPartial<CallToolEvent>) => void,
    /** Aborted when the client cancels the call or its deadline passes. */
    public readonly signal: AbortSignal,
    private readonly _answers: Answers = {},
  ) {
    this.log = {
      debug: (message) => this._log("debug", message),
      info: (message) => this._log("info", message),
      warning: (message) => this._log("warning", message),
      error: (message) => this._log("error", message),
    };
  }

  /** The answers this request carries, by key. */
  get inputResponses(): Answers {
    return { ...this._answers };
  }
```

Replace the `elicit` stub with:

```typescript
  /**
   * Ask the user a question.
   *
   * The first time, this ends the call: the client asks the user and calls the
   * tool again, and the tool **runs again from the top**. On that run this
   * returns the answer. So ask before doing anything with side effects.
   */
  async elicit(
    message: string,
    schema: Record<string, unknown> = {},
    opts: { key?: string; url?: string } = {},
  ): Promise<{ action: string; content: string }> {
    const mode = opts.url !== undefined ? "url" : "form";
    const capability = this._meta.clientCapabilities?.elicitation;
    if (!capability || !capability[mode]) {
      throw new McpError(
        ErrorCode.MissingClientCapability,
        `Client does not support ${mode} elicitation`,
        { requiredCapabilities: [`elicitation.${mode}`] },
      );
    }

    const key = opts.key ?? `elicit-${this._elicitCalls}`;
    this._elicitCalls += 1;
    const answer = this._answers[key];
    if (answer !== undefined) return answer;

    throw new NeedsInput({
      [key]: {
        request: {
          $case: "elicit",
          elicit: {
            message,
            mode:
              opts.url !== undefined
                ? { $case: "url", url: { url: opts.url } }
                : { $case: "form", form: { requestedSchema: JSON.stringify(schema) } },
          },
        },
      },
    });
  }
```

- [ ] **Step 5: `callTool` handles the rounds**

In `typescript/src/v2/servicer.ts`:

Imports:

```typescript
import { LOG_LEVELS, NeedsInput, V2Context } from "./context.js";
import { operationDigest, principalDigest, seal, unseal, type Answers } from "./state.js";
```

(the first replaces the existing `./context.js` import). Add `type InputRequest` to the generated type import.

Options — three new fields:

```typescript
  /** Signs request_state; replicas behind one load balancer must share it. */
  stateSecret: Uint8Array;
  /** False when the secret was generated at start-up rather than configured. */
  stateSecretConfigured: boolean;
  /** Whether the server checks credentials; if so, request_state is bound to them. */
  authEnabled: boolean;
```

Add to the class:

```typescript
  private _warnedAboutSecret = false;

  private _principal(context: CallContext): string {
    if (!this._opts.authEnabled) return "";
    return principalDigest(context.metadata.get("authorization"));
  }

  private _seal(answers: Answers, operation: string, principal: string): Uint8Array {
    if (!this._opts.stateSecretConfigured && !this._warnedAboutSecret) {
      this._warnedAboutSecret = true;
      console.warn(
        "[rapidmcp] Issuing request_state signed with a secret generated at start-up. " +
          "Set stateSecret so every replica can verify it.",
      );
    }
    return seal(this._opts.stateSecret, answers, operation, principal);
  }
```

Replace `callTool` with:

```typescript
  async *callTool(
    request: CallToolRequest,
    context: CallContext,
  ): AsyncGenerator<DeepPartial<CallToolEvent>> {
    this._checkMeta(request.meta, context);
    const name = request.name;
    const operation = operationDigest("tools/call", name, request.arguments);
    const principal = this._principal(context);

    // Earlier answers come back inside the state; the latest ones in the request.
    let answers: Answers = {};
    if (request.requestState.length > 0) {
      try {
        answers = unseal(this._opts.stateSecret, request.requestState, operation, principal);
      } catch (err) {
        throw this._failure(err, `Tool call '${name}' failed`, context);
      }
    }
    for (const [key, response] of Object.entries(request.inputResponses)) {
      if (response.response?.$case === "elicit") {
        const { action, content } = response.response.elicit;
        answers[key] = { action, content };
      }
    }

    // Events the tool emits and the "stop reading" marker share one queue, so
    // everything emitted before the tool settles is delivered before its result.
    const DONE = Symbol("done");
    const queue = new AsyncQueue<DeepPartial<CallToolEvent> | typeof DONE>();
    const ctx = new V2Context(request.meta!, (event) => queue.enqueue(event), context.signal, answers);
    const stop = () => queue.enqueue(DONE);
    context.signal.addEventListener("abort", stop, { once: true });

    const outcome = this._runTool(name, request.arguments, ctx).then(
      (result) => ({ result }),
      (error: unknown) => ({ error }),
    );
    void outcome.then(stop);

    try {
      for (;;) {
        const item = await queue.dequeue();
        if (item === DONE) break;
        yield item;
      }
    } finally {
      context.signal.removeEventListener("abort", stop);
    }
    // Cancelled, or past its deadline: nobody is waiting for a result.
    if (context.signal.aborted) return;

    const settled = await outcome;
    if ("error" in settled) {
      if (settled.error instanceof NeedsInput) {
        yield {
          event: {
            $case: "inputRequired",
            inputRequired: {
              inputRequests: settled.error.requests as Record<string, DeepPartial<InputRequest>>,
              requestState: this._seal(answers, operation, principal),
            },
          },
        };
        return;
      }
      throw this._failure(settled.error, `Tool call '${name}' failed`, context);
    }
    const { content, isError, structuredContent } = settled.result;
    yield {
      event: {
        $case: "complete",
        complete: {
          meta: this._resultMeta(),
          content,
          isError,
          structuredContent: structuredContent === undefined ? "" : JSON.stringify(structuredContent),
        },
      },
    };
  }
```

In `typescript/src/server.ts`: add to `RapidMCPOptions`

```typescript
  /** Signs the request_state v2 tools hand to clients between input rounds. Replicas must share it. */
  stateSecret?: string | Uint8Array;
```

a field and its initialisation in the constructor:

```typescript
  private _stateSecret: Uint8Array;
  private _stateSecretConfigured: boolean;
```

```typescript
    this._stateSecretConfigured = opts.stateSecret !== undefined;
    this._stateSecret =
      opts.stateSecret === undefined
        ? randomBytes(32)
        : typeof opts.stateSecret === "string"
          ? new TextEncoder().encode(opts.stateSecret)
          : opts.stateSecret;
```

with `import { randomBytes } from "node:crypto";`, and pass to the v2 servicer:

```typescript
      stateSecret: this._stateSecret,
      stateSecretConfigured: this._stateSecretConfigured,
      authEnabled: this._auth !== undefined,
```

- [ ] **Step 6: Run and commit**

```powershell
cd typescript; npx tsc -p tsconfig.build.json --noEmit; npx vitest run tests/v2-input-server.test.ts; npx vitest run
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src typescript/tests/v2-input-server.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): ctx.elicit() on v2 through input-required rounds"
```

Expected: 14 tests pass in the new file; the full suite stays green.

---

### Task 6: TypeScript client — answering input rounds

**Files:**
- Modify: `typescript/src/errors.ts` (`ErrorCode.InputLoop = 508`)
- Modify: `typescript/src/v2/client-transport.ts`
- Modify: `typescript/src/client.ts` (`setElicitationHandler(handler, opts?)`)
- Modify: `CHANGELOG.md`
- Test: `typescript/tests/v2-input-client.test.ts`

**Interfaces:**
- Produces: `export interface ElicitRequestInfo { message: string; schema: string; mode: "form" | "url"; url: string }`; `Client.setElicitationHandler(handler, opts?: { url?: boolean })`; the transport's fourth constructor argument becomes `() => { handler: ElicitationHandler | null; url: boolean }`; `ErrorCode.InputLoop = 508`.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-input-client.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";

describe("a v2 client answers input rounds with its elicitation handler", () => {
  let server: RapidMCP;
  let port: number;
  let client: Client;

  beforeEach(async () => {
    server = new RapidMCP({ name: "input", stateSecret: "k" });
    server.addTool({
      name: "deploy",
      execute: async (args: any, ctx: any) => {
        const answer = await ctx.elicit("Deploy to production?", {
          type: "object",
          properties: { confirm: { type: "boolean" } },
        });
        if (answer.action !== "accept") return `not deployed (${answer.action})`;
        return `deployed ${args.service} confirm=${JSON.parse(answer.content || "{}").confirm}`;
      },
    });
    server.addTool({
      name: "two_questions",
      execute: async (_a: unknown, ctx: any) => {
        const first = await ctx.elicit("Name?", { type: "object" });
        const second = await ctx.elicit("Colour?", { type: "object" });
        return `${JSON.parse(first.content).name} likes ${JSON.parse(second.content).colour}`;
      },
    });
    server.addTool({
      name: "pay",
      execute: async (_a: unknown, ctx: any) => {
        const answer = await ctx.elicit("Complete the payment", {}, { url: "https://pay.example/session/42" });
        return `payment ${answer.action}`;
      },
    });
    server.addTool({
      name: "never_satisfied",
      execute: async (_a: unknown, ctx: any) => {
        for (let attempt = 0; attempt < 100; attempt++) {
          await ctx.elicit("Again?", {}, { key: `attempt-${attempt}` });
        }
        return "unreachable";
      },
    });
    port = await server.listen();
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  const failure = (p: Promise<unknown>) =>
    p.then(
      () => null,
      (e: unknown) => e as McpError,
    );

  it.each(["legacy", "modern"] as const)("runs one tool on the %s protocol", async (mode) => {
    client = new Client(`127.0.0.1:${port}`, { mode });
    const seen: string[] = [];
    client.setElicitationHandler(async (request) => {
      seen.push(request.message);
      return { action: "accept", content: '{"confirm": true}' };
    });
    await client.connect();

    const result = await client.callTool("deploy", { service: "api" });

    expect(result.content[0].text).toBe("deployed api confirm=true");
    expect(seen).toEqual(["Deploy to production?"]);
  });

  it("gives the handler the form schema on v2", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const requests: any[] = [];
    client.setElicitationHandler(async (request) => {
      requests.push(request);
      return { action: "accept", content: "{}" };
    });
    await client.connect();

    await client.callTool("deploy", { service: "api" });

    expect([requests[0].mode, requests[0].url]).toEqual(["form", ""]);
    expect(JSON.parse(requests[0].schema).properties).toEqual({ confirm: { type: "boolean" } });
  });

  it("asks two questions in order", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const asked: string[] = [];
    client.setElicitationHandler(async (request) => {
      asked.push(request.message);
      return {
        action: "accept",
        content: request.message === "Name?" ? '{"name": "Ada"}' : '{"colour": "green"}',
      };
    });
    await client.connect();

    const result = await client.callTool("two_questions");

    expect(asked).toEqual(["Name?", "Colour?"]);
    expect(result.content[0].text).toBe("Ada likes green");
  });

  it("passes a decline to the tool", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    client.setElicitationHandler(async () => ({ action: "decline", content: "" }));
    await client.connect();

    const result = await client.callTool("deploy", { service: "api" });

    expect(result.content[0].text).toBe("not deployed (decline)");
  });

  it("delivers url mode to a handler that declared it", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const requests: any[] = [];
    client.setElicitationHandler(
      async (request) => {
        requests.push(request);
        return { action: "accept", content: "" };
      },
      { url: true },
    );
    await client.connect();

    const result = await client.callTool("pay");

    expect([requests[0].mode, requests[0].url]).toEqual(["url", "https://pay.example/session/42"]);
    expect(result.content[0].text).toBe("payment accept");
  });

  it("reports a missing capability for url mode it did not declare", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    client.setElicitationHandler(async () => ({ action: "accept", content: "" }));
    await client.connect();

    const err = await failure(client.callTool("pay"));

    expect(err?.code).toBe(-32021);
    expect(err?.data).toEqual({ requiredCapabilities: ["elicitation.url"] });
  });

  it("reports a missing capability when it has no handler", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    expect((await failure(client.callTool("deploy", { service: "api" })))?.code).toBe(-32021);
  });

  it("gives up on a server that never stops asking", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    let rounds = 0;
    client.setElicitationHandler(async () => {
      rounds += 1;
      return { action: "accept", content: "{}" };
    });
    await client.connect();

    const err = await failure(client.callTool("never_satisfied"));

    expect(err?.code).toBe(508);
    expect(rounds).toBe(10);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-input-client.test.ts
```

Expected: the `modern` tests fail with `The server asked for input, which this client cannot give yet`; the `legacy` case passes.

- [ ] **Step 3: Implement**

`typescript/src/errors.ts`: add `InputLoop: 508,` to `ErrorCode`.

In `typescript/src/v2/client-transport.ts`:

Add below `PROTOCOL_VERSION`:

```typescript
export const MAX_INPUT_ROUNDS = 10;

/** What a v2 server asks the user; passed to the elicitation handler. */
export interface ElicitRequestInfo {
  message: string;
  /** JSON Schema text, form mode only. */
  schema: string;
  mode: "form" | "url";
  /** URL mode only. */
  url: string;
}

export type ElicitationHandler = (
  request: ElicitRequestInfo,
) => Promise<{ action: string; content?: string }>;

type Terminal<R> =
  | { kind: "complete"; message: R }
  | { kind: "inputRequired"; message: InputRequired };
```

Add `type InputRequest`, `type InputRequired` and `type InputResponse` to the generated import.

Constructor — the fourth parameter changes:

```typescript
    private readonly _elicitation: () => { handler: ElicitationHandler | null; url: boolean },
```

In `_meta`, replace the `elicitation:` line:

```typescript
        elicitation: this._elicitationCapability(),
```

and add:

```typescript
  private _elicitationCapability() {
    const { handler, url } = this._elicitation();
    return handler ? { form: true, url } : undefined;
  }
```

`_stream` now returns which terminal event arrived. Change its signature's return type to `Promise<Terminal<R>>` and its two terminal branches to:

```typescript
        } else if (event.$case === "complete") {
          return { kind: "complete", message: event.complete as R };
        } else if (event.$case === "inputRequired") {
          return { kind: "inputRequired", message: event.inputRequired as InputRequired };
        }
```

Replace `callTool`, `readResource` and `getPrompt` with:

```typescript
  private async _answer(request: InputRequest): Promise<InputResponse> {
    const { handler } = this._elicitation();
    if (request.request?.$case !== "elicit" || handler === null) {
      throw new McpError(
        ErrorCode.MissingClientCapability,
        "The server asked for input this client cannot give",
      );
    }
    const asked = request.request.elicit;
    const mode = asked.mode?.$case === "url" ? "url" : "form";
    const reply = await handler({
      message: asked.message,
      schema: asked.mode?.$case === "form" ? asked.mode.form.requestedSchema : "",
      mode,
      url: asked.mode?.$case === "url" ? asked.mode.url.url : "",
    });
    return {
      response: { $case: "elicit", elicit: { action: reply.action, content: reply.content ?? "" } },
    };
  }

  /** Call until the server has what it needs, answering its questions in between. */
  private async _run<R>(
    open: (
      round: { inputResponses: Record<string, InputResponse>; requestState: Uint8Array },
      options: CallOptions,
    ) => AsyncIterable<{ event?: { $case: string } | undefined }>,
    opts: { signal?: AbortSignal; timeout?: number } = {},
  ): Promise<R> {
    let round = { inputResponses: {} as Record<string, InputResponse>, requestState: new Uint8Array() };
    for (let rounds = 0; rounds <= MAX_INPUT_ROUNDS; rounds++) {
      const current = round;
      const outcome = await this._stream<R>((o) => open(current, o), opts);
      if (outcome.kind === "complete") return outcome.message;
      if (rounds === MAX_INPUT_ROUNDS) break;
      const inputResponses: Record<string, InputResponse> = {};
      for (const [key, request] of Object.entries(outcome.message.inputRequests)) {
        inputResponses[key] = await this._answer(request);
      }
      round = { inputResponses, requestState: outcome.message.requestState };
    }
    throw new McpError(
      ErrorCode.InputLoop,
      `The server asked for input more than ${MAX_INPUT_ROUNDS} times`,
    );
  }

  async callTool(
    name: string,
    args: Record<string, unknown>,
    opts: { signal?: AbortSignal; timeout?: number } = {},
  ): Promise<CallToolResult> {
    const text = JSON.stringify(args); // the same text every round: the state is bound to it
    const wire = await this._run<WireCallToolResult>(
      (round, o) =>
        this._client.callTool({ meta: this._meta(true), name, arguments: text, ...round }, o),
      opts,
    );
    return {
      ...convertCallToolResult(wire),
      structuredContent: wire.structuredContent ? JSON.parse(wire.structuredContent) : undefined,
    };
  }

  async readResource(uri: string): Promise<ReadResourceResult> {
    const wire = await this._run<WireReadResourceResult>((round, o) =>
      this._client.readResource({ meta: this._meta(), uri, ...round }, o),
    );
    return convertReadResourceResult(wire);
  }

  async getPrompt(name: string, args: Record<string, string>): Promise<GetPromptResult> {
    const wire = await this._run<WireGetPromptResult>((round, o) =>
      this._client.getPrompt({ meta: this._meta(), name, arguments: args, ...round }, o),
    );
    return convertGetPromptResult(
      wire as unknown as Parameters<typeof convertGetPromptResult>[0],
    );
  }
```

In `typescript/src/client.ts`:

Import the types and widen the handler:

```typescript
import {
  V2Transport,
  isV2Missing,
  type ElicitRequestInfo,
} from "./v2/client-transport.js";
```

(replacing the existing import of `V2Transport` and `isV2Missing`). Change the handler field and setter:

```typescript
  private _elicitationHandler:
    | ((req: ElicitRequestInfo) => Promise<{ action: string; content?: string }>)
    | null = null;
  private _elicitationUrl = false;
```

```typescript
  /**
   * Register a handler for the server's questions. It receives the message and,
   * for a form, the JSON Schema text; on v2 also the mode and, for url mode, the
   * url. Pass `{ url: true }` if it can also send the user to a URL.
   */
  setElicitationHandler(
    handler: (req: ElicitRequestInfo) => Promise<{ action: string; content?: string }>,
    opts: { url?: boolean } = {},
  ): void {
    this._elicitationHandler = handler;
    this._elicitationUrl = opts.url ?? false;
  }
```

In the v1 read loop's `elicitation` case, call the handler with the wider shape and send a complete reply:

```typescript
                const result = await this._elicitationHandler!({
                  message: msg.elicitation.message,
                  schema: msg.elicitation.schema,
                  mode: "form",
                  url: "",
                });
                this._sendQueue.enqueue({
                  requestId: rid,
                  message: {
                    $case: "elicitationReply",
                    elicitationReply: { action: result.action, content: result.content ?? "" },
                  },
                });
```

and in `_doConnect` replace the transport's fourth argument with:

```typescript
        () => ({ handler: this._elicitationHandler, url: this._elicitationUrl }),
```

Export the type from `typescript/src/index.ts`:

```typescript
export { type ElicitRequestInfo } from "./v2/client-transport.js";
```

- [ ] **Step 4: Run, changelog, commit**

```powershell
cd typescript; npx tsc -p tsconfig.build.json --noEmit; npx vitest run tests/v2-input-client.test.ts; npx vitest run
```

Expected: type check clean; 9 tests pass; the full suite stays green.

In `CHANGELOG.md`, in both "Protocol v2 (experimental)" bullets, replace the final sentence `Not on v2 yet: asking the user for input, and subscriptions` with:

```markdown
`ctx.elicit()` works on v2 too, in form and URL mode: the call ends with "input required", the client asks its elicitation handler and calls again, and the tool runs again from the top — so ask before acting. Set `state_secret` / `stateSecret` when running more than one replica. Not on v2 yet: subscriptions
```

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src typescript/tests/v2-input-client.test.ts CHANGELOG.md
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): v2 clients answer input rounds with their elicitation handler"
```
