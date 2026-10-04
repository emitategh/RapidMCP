# Proto v2, Phase 2 Implementation Plan — calls, reads and prompts

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tool calls, resource reads and prompt retrieval work on the v2 protocol as server-streaming RPCs, with progress and log messages gated per request, structured tool results, gRPC deadlines and cancellation.

**Architecture:** Three server-streaming RPCs are added to `mcp.v2.Mcp`. A call's stream carries zero or more `progress` / `log` events and exactly one terminal event; the `oneof` case is the standard's `resultType`. Each language gets a v2 `Context` that emits onto the call's own stream and only when the request opted in. Tools, resources, prompts and middleware are the ones already registered; v1 is untouched.

**Tech Stack:** Python 3.10+ (`grpcio` aio, `pytest`, `ruff`); TypeScript (`nice-grpc`, `ts-proto`, `vitest`).

**Spec:** `docs/superpowers/specs/2026-10-01-proto-v2-stateless-design.md` (sections "Results, and where `resultType` went", "Progress and logging", "Cancellation and timeouts", "Errors", and the 2026-10-04 addendum on structured results).

## Global Constraints

- `proto/mcp.proto` and the v1 generated stubs are not changed. Generate only `mcp_v2.proto`.
- A stream ends with exactly one terminal event (`complete`, or `input_required` from phase 3). Progress and log events come before it, never after.
- `Progress` is emitted only when the request's `meta.progress_token` is set; the token is echoed unchanged.
- `LogMessage` is emitted only when `meta.log_level` is set, and only at or above that level. Levels, lowest first: `debug`, `info`, `notice`, `warning`, `error`, `critical`, `alert`, `emergency`. An unknown level in a request is `-32602`.
- The server sends no terminal event for a cancelled call; cancelling the RPC cancels the handler (Python `CancelledError`, TypeScript `ctx.signal`).
- Error text for server-side failures is the fixed message already used on v1: `Tool call '<name>' failed`, `Resource handler for '<uri>' failed`, `Prompt handler '<name>' failed`. Exception text is logged, never sent.
- On v2, `ctx.sample()` and `ctx.list_roots()` raise `-32601`. `ctx.elicit()` raises `-32601` until phase 3.
- A tool returning a JSON object produces both `structured_content` (JSON text) and one text content item with the same JSON.
- Python: use the project venv; run `ruff format src tests` and `ruff check src tests` before each commit.
- Git: `-c safe.directory=D:/Trabajo/mcp-grpc` on every git command. No co-author or tool attribution in commit messages.

## Review Focus

1. **A tool that emits progress and then fails** — the client must still get the error (with its MCP code), not a hang or a half-finished stream treated as success.
2. **A deadline that expires mid-tool** — the client gets `408`, and the tool is cancelled on the server rather than running on.
3. **A request with `log_level` set but a tool that logs nothing** — a normal result; no stray events.
4. **A prompt called without a required argument** — `-32602` naming the argument, not `-32603` from a `TypeError`.
5. **A notification handler on the client that throws** — the call still returns its result.

---

### Task 1: Proto messages and RPCs for calls, reads and prompts

**Files:**
- Modify: `proto/mcp_v2.proto`
- Regenerate: `python/src/rapidmcp/_generated/mcp_v2_pb2*.py[i]`, `typescript/generated/mcp_v2.ts`
- Modify: `python/tests/test_v2_proto.py`, `typescript/tests/v2-proto.test.ts`

**Interfaces:**
- Produces: RPCs `CallTool`, `ReadResource`, `GetPrompt` (server-streaming) and messages `ContentItem`, `Progress`, `LogMessage`, `CallToolRequest`, `CallToolResult`, `CallToolEvent`, `ReadResourceRequest`, `ReadResourceResult`, `ReadResourceEvent`, `GetPromptRequest`, `PromptMessage`, `GetPromptResult`, `GetPromptEvent`, and the phase 3 types `ElicitForm`, `ElicitUrl`, `ElicitRequest`, `ElicitResult`, `InputRequest`, `InputResponse`, `InputRequired`.

- [ ] **Step 1: Update the stub tests (they fail first)**

In `python/tests/test_v2_proto.py`, replace the method list in `test_v2_service_has_the_phase_1_methods` and rename the test:

```python
def test_v2_service_has_the_expected_methods():
    from rapidmcp._generated import mcp_v2_pb2

    service = mcp_v2_pb2.DESCRIPTOR.services_by_name["Mcp"]

    assert service.full_name == "mcp.v2.Mcp"
    assert sorted(m.name for m in service.methods) == [
        "CallTool",
        "Complete",
        "Discover",
        "GetPrompt",
        "ListPrompts",
        "ListResourceTemplates",
        "ListResources",
        "ListTools",
        "ReadResource",
    ]
    streaming = {m.name for m in service.methods if m.server_streaming}
    assert streaming == {"CallTool", "ReadResource", "GetPrompt"}
```

In `typescript/tests/v2-proto.test.ts`, replace the first test:

```typescript
  it("describe the service", () => {
    expect(McpDefinition.fullName).toBe("mcp.v2.Mcp");
    expect(Object.keys(McpDefinition.methods).sort()).toEqual([
      "callTool",
      "complete",
      "discover",
      "getPrompt",
      "listPrompts",
      "listResourceTemplates",
      "listResources",
      "listTools",
      "readResource",
    ]);
    expect(McpDefinition.methods.callTool.responseStream).toBe(true);
    expect(McpDefinition.methods.readResource.responseStream).toBe(true);
    expect(McpDefinition.methods.getPrompt.responseStream).toBe(true);
  });
```

Run both; expected: FAIL on the method list.

- [ ] **Step 2: Extend the proto**

In `proto/mcp_v2.proto`, add three lines to `service Mcp` after `rpc Complete(...)`:

```proto
  rpc CallTool(CallToolRequest) returns (stream CallToolEvent);
  rpc ReadResource(ReadResourceRequest) returns (stream ReadResourceEvent);
  rpc GetPrompt(GetPromptRequest) returns (stream GetPromptEvent);
```

and append to the end of the file:

```proto

// ── Content ───────────────────────────────────────────────────────────────

message ContentItem {
  string type      = 1;  // "text" | "image" | "audio" | "resource"
  string text      = 2;
  bytes  data      = 3;
  string mime_type = 4;
  string uri       = 5;
}

// ── Events a call can emit before its result ──────────────────────────────

// Sent only when the request set meta.progress_token.
message Progress {
  string token          = 1;  // echoed from the request
  double progress       = 2;
  optional double total = 3;
  string message        = 4;
}

// Sent only when the request set meta.log_level, at or above that level.
message LogMessage {
  string level  = 1;
  string logger = 2;
  string data   = 3;  // JSON text
}

// ── Asking the client for input (multi round-trip requests) ───────────────

message ElicitForm { string requested_schema = 1; }  // JSON Schema as JSON text
message ElicitUrl  { string url = 1; }

message ElicitRequest {
  string message = 1;
  oneof mode {
    ElicitForm form = 2;
    ElicitUrl  url  = 3;
  }
}

message ElicitResult {
  string action  = 1;  // "accept" | "decline" | "cancel"
  string content = 2;  // JSON object text; only for an accepted form
}

message InputRequest {
  reserved 2, 3;  // sampling, roots
  oneof request { ElicitRequest elicit = 1; }
}

message InputResponse {
  reserved 2, 3;  // sampling, roots
  oneof response { ElicitResult elicit = 1; }
}

message InputRequired {
  map<string, InputRequest> input_requests = 1;  // keys chosen by the server
  bytes request_state = 2;                        // opaque to the client
}

// ── Tool calls ────────────────────────────────────────────────────────────

message CallToolRequest {
  RequestMeta meta = 1;
  string name      = 2;
  string arguments = 3;  // JSON object text
  map<string, InputResponse> input_responses = 4;  // retry only
  bytes request_state = 5;                          // retry only, echoed unchanged
}

message CallToolResult {
  ResultMeta meta              = 1;
  repeated ContentItem content = 2;
  bool is_error                = 3;
  string structured_content    = 4;  // JSON text, empty = none
}

// The oneof case is the standard's resultType: `complete` or `input_required`.
message CallToolEvent {
  oneof event {
    Progress       progress       = 1;
    LogMessage     log            = 2;
    CallToolResult complete       = 3;
    InputRequired  input_required = 4;
  }
}

// ── Resource reads ────────────────────────────────────────────────────────

message ReadResourceRequest {
  RequestMeta meta = 1;
  string uri       = 2;
  map<string, InputResponse> input_responses = 3;
  bytes request_state = 4;
}

message ReadResourceResult {
  ResultMeta meta              = 1;
  repeated ContentItem content = 2;
  CacheHint cache              = 3;
}

message ReadResourceEvent {
  oneof event {
    Progress           progress       = 1;
    LogMessage         log            = 2;
    ReadResourceResult complete       = 3;
    InputRequired      input_required = 4;
  }
}

// ── Prompts ───────────────────────────────────────────────────────────────

message GetPromptRequest {
  RequestMeta meta = 1;
  string name      = 2;
  map<string, string> arguments = 3;
  map<string, InputResponse> input_responses = 4;
  bytes request_state = 5;
}

message PromptMessage {
  string role         = 1;
  ContentItem content = 2;
}

message GetPromptResult {
  ResultMeta meta                 = 1;
  repeated PromptMessage messages = 2;
}

message GetPromptEvent {
  oneof event {
    Progress        progress       = 1;
    LogMessage      log            = 2;
    GetPromptResult complete       = 3;
    InputRequired   input_required = 4;
  }
}
```

- [ ] **Step 3: Regenerate v2 only, verify, commit**

```powershell
cd python; .\.venv\Scripts\python.exe generate.py mcp_v2.proto
cd ..\typescript; npm run generate -- --path ../proto/mcp_v2.proto
```

`git status --short` must show only the proto, the three Python v2 stubs, `typescript/generated/mcp_v2.ts` and the two test files. The TypeScript server no longer type-checks until Task 4 adds the new methods; that is expected. Run the two stub tests (PASS) and the Python suite (PASS), then:

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add proto python/src/rapidmcp/_generated python/tests/test_v2_proto.py typescript/generated/mcp_v2.ts typescript/tests/v2-proto.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(proto): v2 streaming RPCs for tool calls, resource reads and prompts"
```

---

### Task 2: Python server — streaming calls

**Files:**
- Modify: `python/src/rapidmcp/_utils.py` (add `_parse_tool_arguments`, `_resource_content_fields`)
- Modify: `python/src/rapidmcp/resources/manager.py` (add `resolve`)
- Modify: `python/src/rapidmcp/context.py` (class attribute `_structured_content`)
- Modify: `python/src/rapidmcp/tools/tool_manager.py` (record structured results)
- Create: `python/src/rapidmcp/_v2_context.py`
- Modify: `python/src/rapidmcp/_v2_servicer.py`
- Modify: `python/src/rapidmcp/auth.py` (server-streaming wrapper)
- Test: `python/tests/test_v2_calls_server.py`

**Interfaces:**
- Consumes: `abort`, `McpError`, `_invoke`, `_McpV2Servicer._check_meta`, `server._dispatch_tool(name, args, ctx)`.
- Produces: `rapidmcp._utils._parse_tool_arguments(name: str, text: str) -> dict`; `rapidmcp._utils._resource_content_fields(raw, mime_type: str) -> dict`; `ResourceManager.resolve(uri) -> tuple[resource, dict[str, str]] | None`; `rapidmcp._v2_context._V2Context(meta, emit)` and `LOG_LEVELS`; `Context._structured_content`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_calls_server.py`:

```python
"""v2 CallTool / ReadResource / GetPrompt, exercised with a raw stub."""

import asyncio
import json

import grpc
import pytest
from grpc import aio

from rapidmcp import Context, Middleware, RapidMCP
from rapidmcp._generated import mcp_pb2
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc
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
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_calls_server.py -q
```

Expected: every test fails with gRPC `UNIMPLEMENTED`.

- [ ] **Step 3: Shared helpers**

Append to `python/src/rapidmcp/_utils.py` (and add `from rapidmcp.errors import INVALID_PARAMS, McpError` to its imports):

```python
def _parse_tool_arguments(name: str, text: str) -> dict:
    """Decode a call's JSON arguments. Anything but a JSON object is INVALID_PARAMS."""
    try:
        arguments = json.loads(text) if text else {}
    except ValueError:
        raise McpError(
            INVALID_PARAMS, f"Invalid arguments for tool '{name}': not valid JSON"
        ) from None
    if not isinstance(arguments, dict):
        raise McpError(
            INVALID_PARAMS, f"Invalid arguments for tool '{name}': expected a JSON object"
        )
    return arguments


def _resource_content_fields(raw: Any, mime_type: str) -> dict[str, Any]:
    """Content fields for a resource handler's return value.

    Bytes become image / audio / resource content according to the mime type;
    anything else is text.
    """
    if isinstance(raw, bytes):
        if mime_type.startswith("image/"):
            kind = "image"
        elif mime_type.startswith("audio/"):
            kind = "audio"
        else:
            kind = "resource"
        return {"type": kind, "data": raw, "mime_type": mime_type}
    return {"type": "text", "text": str(raw), "mime_type": mime_type}
```

Add to `ResourceManager` in `python/src/rapidmcp/resources/manager.py` (and import `match_uri_template` from `rapidmcp.resources.uri_template`):

```python
    def resolve(
        self, uri: str
    ) -> tuple[RegisteredResource | RegisteredResourceTemplate, dict[str, str]] | None:
        """The resource or template that serves *uri*, with its template parameters."""
        resource = self._resources.get(uri)
        if resource:
            return resource, {}
        for template in self._resource_templates.values():
            params = match_uri_template(uri, template.uri_template)
            if params is not None:
                return template, params
        return None
```

- [ ] **Step 4: Structured results**

In `python/src/rapidmcp/context.py`, add as the first line of the `Context` class body (after the docstring):

```python
    # Set by the tool manager when a tool returns a JSON object; read by the v2 servicer.
    _structured_content: dict | None = None
```

In `python/src/rapidmcp/tools/tool_manager.py`, in `_call_tool_with_dict`, between `result = await _invoke(tool.handler, **args)` and `content = _to_content_items(result)`:

```python
            if isinstance(result, dict) and isinstance(ctx, Context):
                ctx._structured_content = result
```

- [ ] **Step 5: The v2 context**

`python/src/rapidmcp/_v2_context.py`:

```python
"""Context for a v2 tool call: everything goes onto the call's own stream."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp.context import Context
from rapidmcp.errors import METHOD_NOT_FOUND, McpError

# RFC 5424 severities, lowest first.
LOG_LEVELS = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")


class _V2Context(Context):
    """Same surface as the v1 ``Context``, stateless underneath.

    Progress and log messages are emitted only when the request asked for them
    in its ``meta``. Requests from the server to the client do not exist on v2.
    """

    def __init__(
        self, meta: pb.RequestMeta, emit: Callable[[pb.CallToolEvent], Awaitable[None]]
    ) -> None:
        self._meta = meta
        self._emit = emit

    async def report_progress(
        self, progress: float, total: float | None = None, message: str = ""
    ) -> None:
        if not self._meta.HasField("progress_token"):
            return
        event = pb.Progress(token=self._meta.progress_token, progress=progress, message=message)
        if total is not None:
            event.total = total
        await self._emit(pb.CallToolEvent(progress=event))

    async def _log(self, level: str, message: str, extra: dict | None) -> None:
        if not self._meta.HasField("log_level"):
            return
        if LOG_LEVELS.index(level) < LOG_LEVELS.index(self._meta.log_level):
            return
        data = json.dumps({"message": message, "extra": extra})
        await self._emit(pb.CallToolEvent(log=pb.LogMessage(level=level, data=data)))

    async def sample(self, *args, **kwargs):
        raise McpError(
            METHOD_NOT_FOUND,
            "Sampling is not available on the v2 protocol; call your LLM provider directly",
        )

    async def list_roots(self, *args, **kwargs):
        raise McpError(
            METHOD_NOT_FOUND,
            "Roots are not available on the v2 protocol; take paths as tool arguments",
        )

    async def elicit(self, *args, **kwargs):
        raise McpError(METHOD_NOT_FOUND, "ctx.elicit() is not available on the v2 protocol yet")
```

- [ ] **Step 6: The streaming RPCs**

In `python/src/rapidmcp/_v2_servicer.py`:

Imports — add `import asyncio`, and:

```python
from rapidmcp._utils import _invoke, _paginate, _parse_tool_arguments, _resource_content_fields
from rapidmcp._v2_context import LOG_LEVELS, _V2Context
```

(the first replaces the existing `_utils` import).

At the end of `_check_meta`, add:

```python
        if meta.HasField("log_level") and meta.log_level not in LOG_LEVELS:
            await abort(context, McpError(INVALID_PARAMS, f"Unknown log level '{meta.log_level}'"))
```

Append to the class:

```python
    # ── streaming RPCs ───────────────────────────────────────────────────

    @staticmethod
    async def _stream(work):
        """Yield whatever *work* emits while it runs, then its final event.

        *work* is ``async (emit) -> terminal event``. When the RPC is cancelled
        (the client went away, or its deadline passed) the work is cancelled too.
        """
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.ensure_future(work(queue.put))
        getter: asyncio.Future | None = None
        try:
            while not task.done():
                getter = asyncio.ensure_future(queue.get())
                await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter.done():
                    yield getter.result()
                else:
                    getter.cancel()
            while not queue.empty():
                yield queue.get_nowait()
            yield task.result()
        finally:
            if getter is not None and not getter.done():
                getter.cancel()
            if not task.done():
                task.cancel()

    async def CallTool(self, request, context):
        await self._check_meta(request, context)
        name = request.name

        async def work(emit):
            arguments = _parse_tool_arguments(name, request.arguments)
            ctx = _V2Context(request.meta, emit)
            result = await self._server._dispatch_tool(name, arguments, ctx)
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

        try:
            # aclosing: if this RPC is cancelled while suspended at the yield below,
            # the inner generator is closed at once and its tool task cancelled.
            async with aclosing(self._stream(work)) as events:
                async for event in events:
                    yield event
        except McpError as error:
            await abort(context, error)
        except Exception:
            logger.exception("Tool call '%s' failed outside the handler", name)
            await abort(context, McpError(INTERNAL_ERROR, f"Tool call '{name}' failed"))

    async def ReadResource(self, request, context):
        await self._check_meta(request, context)
        uri = request.uri
        found = self._server._resource_manager.resolve(uri)
        if found is None:
            await abort(context, McpError(INVALID_PARAMS, f"Resource '{uri}' not found"))
        resource, params = found
        try:
            raw = await _invoke(resource.handler, **params)
        except Exception:
            logger.exception("Resource handler for '%s' raised", uri)
            await abort(context, McpError(INTERNAL_ERROR, f"Resource handler for '{uri}' failed"))
        yield pb.ReadResourceEvent(
            complete=pb.ReadResourceResult(
                meta=self._result_meta(),
                content=[
                    pb.ContentItem(uri=uri, **_resource_content_fields(raw, resource.mime_type))
                ],
                cache=self._no_cache(),
            )
        )

    async def GetPrompt(self, request, context):
        await self._check_meta(request, context)
        name = request.name
        prompt = self._server._prompts.get(name)
        if prompt is None:
            await abort(context, McpError(INVALID_PARAMS, f"Prompt '{name}' not found"))
        arguments = dict(request.arguments)
        declared = [a["name"] for a in prompt.arguments]
        missing = [a["name"] for a in prompt.arguments if a["required"] and a["name"] not in arguments]
        if missing:
            await abort(
                context,
                McpError(
                    INVALID_PARAMS,
                    f"Missing required argument(s) for prompt '{name}': {', '.join(missing)}",
                ),
            )
        unknown = [key for key in arguments if key not in declared]
        if unknown:
            await abort(
                context,
                McpError(
                    INVALID_PARAMS,
                    f"Unknown argument(s) for prompt '{name}': {', '.join(unknown)}",
                ),
            )
        try:
            text = await _invoke(prompt.handler, **arguments)
        except Exception:
            logger.exception("Prompt handler '%s' raised", name)
            await abort(context, McpError(INTERNAL_ERROR, f"Prompt handler '{name}' failed"))
        yield pb.GetPromptEvent(
            complete=pb.GetPromptResult(
                meta=self._result_meta(),
                messages=[
                    pb.PromptMessage(role="user", content=pb.ContentItem(type="text", text=text))
                ],
            )
        )
```

Add `import json` and `from contextlib import aclosing` to the module's imports.

- [ ] **Step 7: Auth for server-streaming methods**

In `python/src/rapidmcp/auth.py`, replace the `unary_stream` branch (the note about it and the wrapper) with:

```python
        if handler.unary_stream is not None:
            original = handler.unary_stream

            async def auth_unary_stream(request, context):
                if not await self._check_token(context):
                    await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
                    return
                async for msg in original(request, context):
                    yield msg

            return handler._replace(unary_stream=auth_unary_stream)
```

- [ ] **Step 8: Run the tests to verify they pass**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_calls_server.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
```

Expected: 24 tests pass in the new file; the full suite stays green.

- [ ] **Step 9: Commit**

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp python/tests/test_v2_calls_server.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): v2 tool calls, resource reads and prompts as streaming RPCs"
```

---

### Task 3: Python client — calls on v2

**Files:**
- Modify: `python/src/rapidmcp/session.py` (`NotificationRegistry.has`)
- Modify: `python/src/rapidmcp/types.py` (`CallToolResult.structured_content`)
- Modify: `python/src/rapidmcp/_v2_client.py`
- Modify: `python/src/rapidmcp/client.py`
- Modify: `python/tests/test_v2_client.py` (one test now names a different operation)
- Test: `python/tests/test_v2_calls_client.py`

**Interfaces:**
- Consumes: the Task 1 stubs; `error_from_rpc`.
- Produces: `_V2Transport(channel, metadata, timeout, supports_elicitation, notifications)`; `_V2Transport.call_tool(name, arguments, timeout) -> CallToolResult`, `.read_resource(uri) -> ReadResourceResult`, `.get_prompt(name, arguments) -> GetPromptResult`; `CallToolResult.structured_content: Any | None`; `NotificationRegistry.has(notification_type) -> bool`.
- Notification payloads on v2 (JSON text, as on v1): `progress` → `{"progress", "total", "message", "token"}`; `log` → `{"level", "message", "extra"}`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_calls_client.py`:

```python
"""Client(mode="modern"): tool calls, resource reads and prompts over v2."""

import asyncio
import json

import pytest

from rapidmcp import Client, Context, RapidMCP
from rapidmcp.errors import McpError, ToolError


@pytest.fixture
async def server():
    srv = RapidMCP(name="calls", version="1.0")
    srv.finished = []

    @srv.tool()
    async def echo(text: str) -> str:
        return text

    @srv.tool()
    async def add(a: int, b: int) -> dict:
        return {"sum": a + b}

    @srv.tool()
    async def friendly() -> str:
        raise ToolError("order id must start with ORD-")

    @srv.tool()
    async def chatty(ctx: Context) -> str:
        await ctx.info("working", extra={"n": 1})
        await ctx.report_progress(1, 2)
        return "done"

    @srv.tool()
    async def slow() -> str:
        await asyncio.sleep(0.6)
        srv.finished.append("slow")
        return "done"

    @srv.resource("res://text")
    async def text() -> str:
        return "hello"

    @srv.resource("res://logo", mime_type="image/png")
    async def logo() -> bytes:
        return b"\x89PNG"

    @srv.prompt()
    async def greet(name: str) -> str:
        return f"hi {name}"

    async with srv:
        yield srv


def _modern(server, **kwargs) -> Client:
    return Client(f"localhost:{server.port}", mode="modern", **kwargs)


async def test_call_tool(server):
    async with _modern(server) as client:
        result = await client.call_tool("echo", {"text": "hi"})

    assert (result.is_error, result.content[0].text, result.structured_content) == (False, "hi", None)


async def test_structured_content_is_parsed(server):
    async with _modern(server) as client:
        result = await client.call_tool("add", {"a": 2, "b": 3})

    assert result.structured_content == {"sum": 5}
    assert json.loads(result.content[0].text) == {"sum": 5}


async def test_tool_error_comes_back_as_an_error_result(server):
    async with _modern(server) as client:
        result = await client.call_tool("friendly")

    assert (result.is_error, result.content[0].text) == (True, "order id must start with ORD-")


async def test_unknown_tool_raises_invalid_params(server):
    async with _modern(server) as client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("nope")

    assert exc.value.code == -32602


async def test_progress_and_log_handlers_receive_the_calls_events(server):
    client = _modern(server)
    progress: list[dict] = []
    logs: list[dict] = []
    client.on_notification("progress", lambda payload: progress.append(json.loads(payload)))
    client.on_notification("log", lambda payload: logs.append(json.loads(payload)))

    async with client:
        result = await client.call_tool("chatty")

    assert result.content[0].text == "done"
    assert [(p["progress"], p["total"]) for p in progress] == [(1, 2)]
    assert progress[0]["token"]
    assert logs == [{"level": "info", "message": "working", "extra": {"n": 1}}]


async def test_a_throwing_notification_handler_does_not_break_the_call(server):
    client = _modern(server)

    def explode(payload):
        raise RuntimeError("handler bug")

    client.on_notification("progress", explode)
    async with client:
        result = await client.call_tool("chatty")

    assert result.content[0].text == "done"


async def test_timeout_raises_408_and_cancels_the_tool(server):
    async with _modern(server) as client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("slow", timeout=0.2)
        await asyncio.sleep(0.8)

    assert exc.value.code == 408
    assert server.finished == []


async def test_cancelling_the_awaiting_task_cancels_the_tool(server):
    async with _modern(server) as client:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client.call_tool("slow"), timeout=0.2)
        await asyncio.sleep(0.8)

    assert server.finished == []


async def test_read_resource(server):
    async with _modern(server) as client:
        text = await client.read_resource("res://text")
        logo = await client.read_resource("res://logo")
        with pytest.raises(McpError) as exc:
            await client.read_resource("res://nope")

    assert (text.content[0].type, text.content[0].text) == ("text", "hello")
    assert (logo.content[0].type, logo.content[0].data) == ("image", b"\x89PNG")
    assert exc.value.code == -32602


async def test_get_prompt(server):
    async with _modern(server) as client:
        result = await client.get_prompt("greet", {"name": "Ada"})
        with pytest.raises(McpError) as exc:
            await client.get_prompt("greet")

    assert [(m.role, m.content.text) for m in result.messages] == [("user", "hi Ada")]
    assert exc.value.code == -32602
```

In `python/tests/test_v2_client.py`, replace `test_modern_client_says_which_operations_v2_lacks` with:

```python
async def test_modern_client_says_which_operations_v2_lacks():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        with pytest.raises(McpError) as exc:
            await client.subscribe_resource("res://a")

    assert exc.value.code == -32601
    assert "legacy" in exc.value.message
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_calls_client.py -q
```

Expected: FAIL — `McpError: call_tool is not available on the v2 protocol yet` (and the same for `read_resource`, `get_prompt`).

- [ ] **Step 3: Small supporting changes**

`python/src/rapidmcp/session.py`, in `NotificationRegistry`:

```python
    def has(self, notification_type: str) -> bool:
        """True when at least one handler is registered for *notification_type*."""
        return bool(self._handlers.get(notification_type))
```

`python/src/rapidmcp/types.py` — `CallToolResult` gains a field and a v2 converter is appended:

```python
@dataclass
class CallToolResult:
    """Parsed result from a ``call_tool`` request."""

    content: list[ContentItem]
    is_error: bool = False
    # The tool's result as a JSON value, when the server sent one (v2 only).
    structured_content: Any | None = None
```

```python
def _convert_call_tool_result_v2(p) -> CallToolResult:
    return CallToolResult(
        content=[_convert_content_item(c) for c in p.content],
        is_error=p.is_error,
        structured_content=json.loads(p.structured_content) if p.structured_content else None,
    )
```

- [ ] **Step 4: The transport**

In `python/src/rapidmcp/_v2_client.py`:

Imports — add `import itertools`, `import json`, `import logging`, and extend the others:

```python
from rapidmcp.errors import INTERNAL_ERROR, METHOD_NOT_FOUND, McpError
from rapidmcp.session import NotificationRegistry
from rapidmcp.types import (
    CallToolResult,
    CompleteResult,
    GetPromptResult,
    ListResult,
    ReadResourceResult,
    ServerInfo,
    _convert_call_tool_result_v2,
    _convert_complete_result,
    _convert_get_prompt_result,
    _convert_prompt,
    _convert_read_resource_result,
    _convert_resource,
    _convert_resource_template,
    _convert_tool_v2,
)

logger = logging.getLogger("rapidmcp.client")
```

Constructor — one more parameter and two attributes:

```python
    def __init__(
        self,
        channel: aio.Channel,
        metadata: list[tuple[str, str]],
        timeout: float,
        supports_elicitation: Callable[[], bool],
        notifications: NotificationRegistry,
    ) -> None:
        self._stub = mcp_v2_pb2_grpc.McpStub(channel)
        self._metadata = metadata
        self._timeout = timeout
        self._supports_elicitation = supports_elicitation
        self._notifications = notifications
        self._progress_tokens = itertools.count(1)
```

`_meta` takes a flag and opts in to events only when a handler exists:

```python
    def _meta(self, *, events: bool = False) -> pb.RequestMeta:
        capabilities = pb.ClientCapabilities()
        if self._supports_elicitation():
            capabilities.elicitation.CopyFrom(pb.ElicitationCapability(form=True))
        meta = pb.RequestMeta(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=capabilities,
            client_info=pb.Implementation(name="rapidmcp-python", version=__version__),
        )
        if events and self._notifications.has("progress"):
            meta.progress_token = f"p{next(self._progress_tokens)}"
        if events and self._notifications.has("log"):
            meta.log_level = "debug"
        return meta
```

Append the streaming machinery and the three operations:

```python
    async def _notify(self, kind: str, payload: dict) -> None:
        try:
            await self._notifications.dispatch(kind, json.dumps(payload))
        except Exception:
            logger.exception("Notification handler for '%s' raised", kind)

    async def _stream(self, method, request, timeout: float | None = None):
        """Run a streaming RPC to its terminal event, feeding progress and log handlers."""
        call = method(request, metadata=self._metadata, timeout=timeout or self._timeout)
        try:
            async for event in call:
                kind = event.WhichOneof("event")
                if kind == "progress":
                    p = event.progress
                    await self._notify(
                        "progress",
                        {
                            "progress": p.progress,
                            "total": p.total if p.HasField("total") else None,
                            "message": p.message,
                            "token": p.token,
                        },
                    )
                elif kind == "log":
                    data = json.loads(event.log.data) if event.log.data else {}
                    await self._notify(
                        "log",
                        {
                            "level": event.log.level,
                            "message": data.get("message"),
                            "extra": data.get("extra"),
                        },
                    )
                elif kind == "complete":
                    return event.complete
                elif kind == "input_required":
                    raise McpError(
                        METHOD_NOT_FOUND, "The server asked for input, which this client cannot give yet"
                    )
            raise McpError(INTERNAL_ERROR, "The server ended the call without a result")
        except aio.AioRpcError as exc:
            error = error_from_rpc(exc.code(), exc.details(), exc.trailing_metadata())
            if error is None:
                raise
            raise error from None
        finally:
            # A no-op once the call has finished; stops the server when we leave early
            # (the awaiting task was cancelled, or a handler error escaped).
            call.cancel()

    async def call_tool(
        self, name: str, arguments: dict | None, timeout: float | None
    ) -> CallToolResult:
        request = pb.CallToolRequest(
            meta=self._meta(events=True), name=name, arguments=json.dumps(arguments or {})
        )
        return _convert_call_tool_result_v2(await self._stream(self._stub.CallTool, request, timeout))

    async def read_resource(self, uri: str) -> ReadResourceResult:
        request = pb.ReadResourceRequest(meta=self._meta(), uri=uri)
        return _convert_read_resource_result(await self._stream(self._stub.ReadResource, request))

    async def get_prompt(self, name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        request = pb.GetPromptRequest(meta=self._meta(), name=name, arguments=arguments or {})
        return _convert_get_prompt_result(await self._stream(self._stub.GetPrompt, request))
```

- [ ] **Step 5: Route the client**

In `python/src/rapidmcp/client.py`:

In `_connect_v2`, pass the registry:

```python
        transport = _V2Transport(
            self._channel,
            self._metadata,
            self._request_timeout,
            supports_elicitation=lambda: self._elicitation_handler is not None,
            notifications=self._notifications,
        )
```

Replace the `self._v1_only("call_tool")`, `self._v1_only("read_resource")` and `self._v1_only("get_prompt")` lines with, respectively:

```python
        if self._v2 is not None:
            return await self._v2.call_tool(name, arguments, timeout)
```

```python
        if self._v2 is not None:
            return await self._v2.read_resource(uri)
```

```python
        if self._v2 is not None:
            return await self._v2.get_prompt(name, arguments)
```

- [ ] **Step 6: Run the tests to verify they pass**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_calls_client.py tests/test_v2_client.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
```

Expected: 10 new tests pass, `test_v2_client.py` stays green, the full suite stays green.

- [ ] **Step 7: Commit**

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp python/tests/test_v2_calls_client.py python/tests/test_v2_client.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): modern clients call tools, read resources and get prompts over v2"
```

---

### Task 4: TypeScript server — streaming calls

**Files:**
- Modify: `typescript/src/_utils.ts` (export `isContentResult`, add `parseToolArguments`)
- Modify: `typescript/src/servicer.ts` (use `parseToolArguments`)
- Modify: `typescript/src/middleware.ts` (`CallToolResult.structuredContent`)
- Modify: `typescript/src/tools/tool-manager.ts` (record structured results)
- Create: `typescript/src/v2/context.ts`
- Modify: `typescript/src/v2/servicer.ts`
- Modify: `typescript/src/server.ts` (pass middleware to the v2 servicer)
- Test: `typescript/tests/v2-calls-server.test.ts`

**Interfaces:**
- Consumes: `toServerError`, `McpError`, `ErrorCode`, `Middleware.buildChain`, `ToolManager.getTool` / `callTool`, `ResourceManager.readResource`, `PromptManager.listPrompts` / `getPrompt`, `AsyncQueue`.
- Produces: `parseToolArguments(name: string, text: string): Record<string, unknown>`; `CallToolResult.structuredContent?: unknown` (middleware type); `class V2Context` and `LOG_LEVELS` in `src/v2/context.ts`; `McpV2ServicerOptions.middlewares: Middleware[]`.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-calls-server.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status } from "nice-grpc-common";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import { ToolError } from "../src/errors.js";
import { Middleware, type CallToolResult, type ToolCallContext } from "../src/middleware.js";
import { McpDefinition, type McpClient, type RequestMeta } from "../generated/mcp_v2.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function meta(extra: Partial<RequestMeta> = {}): RequestMeta {
  return {
    protocolVersion: "2026-07-28",
    clientCapabilities: { extensions: {} },
    clientInfo: undefined,
    ...extra,
  };
}

class Block extends Middleware {
  async onToolCall(ctx: ToolCallContext, next: () => Promise<CallToolResult>) {
    if (ctx.toolName === "blocked") {
      return {
        content: [{ type: "text", text: "blocked by middleware", data: new Uint8Array(), mimeType: "", uri: "" }],
        isError: true,
      };
    }
    return next();
  }
}

describe("v2 streaming calls", () => {
  let server: RapidMCP;
  let channel: Channel;
  let v2: McpClient;
  let finished: string[];
  let aborted: string[];

  async function start(opts: Partial<RapidMCPOptions> = {}) {
    finished = [];
    aborted = [];
    server = new RapidMCP({ name: "calls", version: "1.0", ...opts });
    server.use(new Block());
    server.addTool({ name: "echo", execute: async (a: any) => a.text });
    server.addTool({ name: "add", execute: async (a: any) => ({ sum: a.a + a.b }) });
    server.addTool({ name: "blocked", execute: async () => "never" });
    server.addTool({
      name: "friendly",
      execute: async () => {
        throw new ToolError("order id must start with ORD-");
      },
    });
    server.addTool({
      name: "chatty",
      execute: async (_a: unknown, ctx: any) => {
        ctx.log.debug("starting");
        ctx.reportProgress(1, 2);
        ctx.log.warning("halfway");
        ctx.reportProgress(2, 2);
        return "done";
      },
    });
    server.addTool({
      name: "progress_then_fail",
      execute: async (_a: unknown, ctx: any) => {
        ctx.reportProgress(1, 2);
        throw new ToolError("gave up halfway");
      },
    });
    server.addTool({
      name: "wants_sampling",
      execute: async (_a: unknown, ctx: any) => {
        await ctx.sample({ messages: [], maxTokens: 1 });
        return "never";
      },
    });
    server.addTool({
      name: "slow",
      execute: async (_a: unknown, ctx: any) => {
        ctx.signal.addEventListener("abort", () => aborted.push("slow"), { once: true });
        await sleep(600);
        finished.push("slow");
        return "done";
      },
    });
    server.addResource({
      uri: "res://text",
      name: "text",
      mimeType: "text/markdown",
      load: async () => ({ text: "# hello" }),
    });
    server.addResource({
      uri: "res://logo",
      name: "logo",
      mimeType: "image/png",
      load: async () => ({ blob: new Uint8Array([0x89, 0x50]) }),
    });
    server.addResource({
      uri: "res://broken",
      name: "broken",
      load: async () => {
        throw new Error("password is hunter2");
      },
    });
    server.addResourceTemplate({
      uriTemplate: "res://items/{id}",
      name: "item",
      load: async (args) => ({ text: `item ${args.id}` }),
    });
    server.addPrompt({
      name: "greet",
      arguments: [{ name: "who", required: true }, { name: "tone" }],
      load: async (args) => `hi ${args.who}, ${args.tone ?? "kind"}`,
    });
    server.addPrompt({
      name: "broken_prompt",
      load: async () => {
        throw new Error("secret");
      },
    });
    const port = await server.listen();
    channel = createChannel(`127.0.0.1:${port}`);
    v2 = createClientFactory().create(McpDefinition, channel);
  }

  beforeEach(() => start());

  afterEach(async () => {
    channel.close();
    await server.close();
  });

  async function events<T>(stream: AsyncIterable<T>): Promise<T[]> {
    const out: T[] = [];
    for await (const event of stream) out.push(event);
    return out;
  }

  const kinds = (list: Array<{ event?: { $case: string } }>) => list.map((e) => e.event?.$case);

  /** Run a stream expected to fail; return its MCP code and message. */
  async function failure(open: (onTrailer: (t: Metadata) => void) => AsyncIterable<unknown>) {
    let trailer = new Metadata();
    const err = await events(open((t) => (trailer = t))).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ClientError);
    return [Number(trailer.get("mcp-error-code")), (err as ClientError).details];
  }

  const call = (name: string, args = "{}", m = meta(), options = {}) =>
    v2.callTool({ meta: m, name, arguments: args, inputResponses: {}, requestState: new Uint8Array() }, options);

  // ── tool calls ───────────────────────────────────────────────────────────

  it("ends a tool call with one complete event", async () => {
    const list = await events(call("echo", '{"text":"hi"}'));

    expect(kinds(list)).toEqual(["complete"]);
    const result = (list[0].event as any).complete;
    expect(result.content.map((c: any) => [c.type, c.text])).toEqual([["text", "hi"]]);
    expect(result.isError).toBe(false);
    expect(result.structuredContent).toBe("");
    expect(result.meta.serverInfo.name).toBe("calls");
  });

  it("sends an object result as structured content and as text", async () => {
    const result = ((await events(call("add", '{"a":2,"b":3}'))).at(-1)!.event as any).complete;

    expect(JSON.parse(result.structuredContent)).toEqual({ sum: 5 });
    expect(JSON.parse(result.content[0].text)).toEqual({ sum: 5 });
  });

  it("runs middleware around v2 calls", async () => {
    const result = ((await events(call("blocked"))).at(-1)!.event as any).complete;

    expect([result.isError, result.content[0].text]).toEqual([true, "blocked by middleware"]);
  });

  it("returns a ToolError as a complete result marked isError", async () => {
    const result = ((await events(call("friendly"))).at(-1)!.event as any).complete;

    expect([result.isError, result.content[0].text]).toEqual([true, "order id must start with ORD-"]);
  });

  it("sends no progress or log events unless the request asked", async () => {
    expect(kinds(await events(call("chatty")))).toEqual(["complete"]);
  });

  it("echoes the request's progress token", async () => {
    const list = await events(call("chatty", "{}", meta({ progressToken: "tok-7" })));

    expect(kinds(list)).toEqual(["progress", "progress", "complete"]);
    expect(
      list.slice(0, 2).map((e: any) => [e.event.progress.token, e.event.progress.progress, e.event.progress.total]),
    ).toEqual([
      ["tok-7", 1, 2],
      ["tok-7", 2, 2],
    ]);
  });

  it("respects the requested log level", async () => {
    const debug = await events(call("chatty", "{}", meta({ logLevel: "debug" })));
    const warning = await events(call("chatty", "{}", meta({ logLevel: "warning" })));
    const logs = (list: any[]) =>
      list.filter((e) => e.event.$case === "log").map((e) => [e.event.log.level, JSON.parse(e.event.log.data)]);

    expect(logs(debug)).toEqual([
      ["debug", { message: "starting", extra: null }],
      ["warning", { message: "halfway", extra: null }],
    ]);
    expect(logs(warning).map((l) => l[0])).toEqual(["warning"]);
  });

  it("returns a plain result when a log level is set but nothing is logged", async () => {
    expect(kinds(await events(call("echo", '{"text":"x"}', meta({ logLevel: "debug" }))))).toEqual(["complete"]);
  });

  it("rejects an unknown log level", async () => {
    const [code] = await failure((onTrailer) =>
      call("echo", '{"text":"x"}', meta({ logLevel: "loud" }), { onTrailer }),
    );

    expect(code).toBe(-32602);
  });

  it("still delivers a failure that follows progress", async () => {
    const list = await events(call("progress_then_fail", "{}", meta({ progressToken: "t" })));

    expect(kinds(list)).toEqual(["progress", "complete"]);
    const result = (list.at(-1)!.event as any).complete;
    expect([result.isError, result.content[0].text]).toEqual([true, "gave up halfway"]);
  });

  it("rejects an unknown tool and bad arguments as invalid params", async () => {
    expect(await failure((onTrailer) => call("nope", "{}", meta(), { onTrailer }))).toEqual([
      -32602,
      "Tool 'nope' not found",
    ]);
    expect(await failure((onTrailer) => call("echo", "{not json", meta(), { onTrailer }))).toEqual([
      -32602,
      "Invalid arguments for tool 'echo': not valid JSON",
    ]);
    expect(await failure((onTrailer) => call("echo", "[1]", meta(), { onTrailer }))).toEqual([
      -32602,
      "Invalid arguments for tool 'echo': expected a JSON object",
    ]);
  });

  it("says sampling is not available on v2", async () => {
    const [code, message] = await failure((onTrailer) => call("wants_sampling", "{}", meta(), { onTrailer }));

    expect(code).toBe(-32601);
    expect(message).toContain("v2");
  });

  it("aborts the tool's signal when the RPC is cancelled", async () => {
    const controller = new AbortController();
    const reading = events(call("slow", "{}", meta(), { signal: controller.signal })).catch(() => "cancelled");
    await sleep(100);

    controller.abort();
    expect(await reading).toBe("cancelled");
    await sleep(100);

    expect(aborted).toEqual(["slow"]);
  });

  // ── resources ────────────────────────────────────────────────────────────

  const read = (uri: string, options = {}) =>
    v2.readResource({ meta: meta(), uri, inputResponses: {}, requestState: new Uint8Array() }, options);

  it("reads a text resource", async () => {
    const list = await events(read("res://text"));

    expect(kinds(list)).toEqual(["complete"]);
    const item = (list[0].event as any).complete.content[0];
    expect([item.type, item.text, item.mimeType, item.uri]).toEqual([
      "text",
      "# hello",
      "text/markdown",
      "res://text",
    ]);
    expect((list[0].event as any).complete.cache.ttlMs).toBe(0n);
  });

  it("types a binary resource by its mime type", async () => {
    const item = ((await events(read("res://logo")))[0].event as any).complete.content[0];

    expect([item.type, [...item.data], item.mimeType]).toEqual(["image", [0x89, 0x50], "image/png"]);
  });

  it("reads a templated resource", async () => {
    const item = ((await events(read("res://items/42")))[0].event as any).complete.content[0];

    expect(item.text).toBe("item 42");
  });

  it("rejects a missing resource as invalid params", async () => {
    expect(await failure((onTrailer) => read("res://nope", { onTrailer }))).toEqual([
      -32602,
      "Resource 'res://nope' not found",
    ]);
  });

  it("does not leak a failing resource handler's exception", async () => {
    expect(await failure((onTrailer) => read("res://broken", { onTrailer }))).toEqual([
      -32603,
      "Resource handler for 'res://broken' failed",
    ]);
  });

  // ── prompts ──────────────────────────────────────────────────────────────

  const prompt = (name: string, args: Record<string, string> = {}, options = {}) =>
    v2.getPrompt(
      { meta: meta(), name, arguments: args, inputResponses: {}, requestState: new Uint8Array() },
      options,
    );

  it("returns a prompt as a user message", async () => {
    const list = await events(prompt("greet", { who: "Ada" }));

    expect(kinds(list)).toEqual(["complete"]);
    const message = (list[0].event as any).complete.messages[0];
    expect([message.role, message.content.type, message.content.text]).toEqual(["user", "text", "hi Ada, kind"]);
  });

  it("rejects an unknown prompt and a missing required argument as invalid params", async () => {
    expect(await failure((onTrailer) => prompt("nope", {}, { onTrailer }))).toEqual([
      -32602,
      "Prompt 'nope' not found",
    ]);
    expect(await failure((onTrailer) => prompt("greet", {}, { onTrailer }))).toEqual([
      -32602,
      "Missing required argument(s) for prompt 'greet': who",
    ]);
  });

  it("does not leak a failing prompt handler's exception", async () => {
    expect(await failure((onTrailer) => prompt("broken_prompt", {}, { onTrailer }))).toEqual([
      -32603,
      "Prompt handler 'broken_prompt' failed",
    ]);
  });

  // ── auth ─────────────────────────────────────────────────────────────────

  it("requires the token on streaming calls when the server has auth", async () => {
    channel.close();
    await server.close();
    await start({ auth: (token) => token === "s3cret" });

    const denied = await events(call("echo", '{"text":"x"}')).then(
      () => null,
      (e: unknown) => e,
    );
    const allowed = await events(
      call("echo", '{"text":"x"}', meta(), { metadata: Metadata({ authorization: "Bearer s3cret" }) }),
    );

    expect((denied as ClientError).code).toBe(Status.UNAUTHENTICATED);
    expect((allowed.at(-1)!.event as any).complete.content[0].text).toBe("x");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-calls-server.test.ts
```

Expected: every test fails (`UNIMPLEMENTED`, or the server refusing to register a service with missing methods).

- [ ] **Step 3: Shared helpers and structured results**

In `typescript/src/_utils.ts`: add `export` to `isContentResult`, import `{ ErrorCode, McpError }` from `./errors.js`, and append:

```typescript
/** Decode a call's JSON arguments. Anything but a JSON object is InvalidParams. */
export function parseToolArguments(name: string, text: string): Record<string, unknown> {
  if (!text) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new McpError(ErrorCode.InvalidParams, `Invalid arguments for tool '${name}': not valid JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new McpError(
      ErrorCode.InvalidParams,
      `Invalid arguments for tool '${name}': expected a JSON object`,
    );
  }
  return parsed as Record<string, unknown>;
}
```

In `typescript/src/servicer.ts`, replace the argument-parsing block at the top of `_handleCallTool` (from `let args: Record<string, unknown> = {};` through the closing brace of `if (argsJson) { ... }`) with:

```typescript
      const args = parseToolArguments(name, argsJson);
```

and add `parseToolArguments` to the `./_utils.js` import.

In `typescript/src/middleware.ts`, extend the result type:

```typescript
export interface CallToolResult {
  content: Array<{ type: string; text: string; data: Uint8Array; mimeType: string; uri: string }>;
  isError: boolean;
  /** The tool's result when it was a JSON object; carried to v2 clients as structured content. */
  structuredContent?: unknown;
}
```

In `typescript/src/tools/tool-manager.ts`, import `isContentResult` alongside `toContentItems`, and replace the success return in `callTool`:

```typescript
      const result = await tool.handler(validatedArgs, ctx);
      const isObject = typeof result === "object" && result !== null && !Array.isArray(result);
      return {
        content: toContentItems(result),
        isError: false,
        structuredContent: isObject && !isContentResult(result) ? result : undefined,
      };
```

- [ ] **Step 4: The v2 context**

`typescript/src/v2/context.ts`:

```typescript
/** Context for a v2 tool call: everything goes onto the call's own stream. */
import type { CallToolEvent, DeepPartial, RequestMeta } from "../../generated/mcp_v2.js";
import { ErrorCode, McpError } from "../errors.js";

/** RFC 5424 severities, lowest first. */
export const LOG_LEVELS = [
  "debug",
  "info",
  "notice",
  "warning",
  "error",
  "critical",
  "alert",
  "emergency",
];

/**
 * Same surface as the v1 Context, stateless underneath. Progress and log
 * messages are emitted only when the request asked for them in its meta.
 * Requests from the server to the client do not exist on v2.
 */
export class V2Context {
  public readonly log: {
    debug: (message: string) => void;
    info: (message: string) => void;
    warning: (message: string) => void;
    error: (message: string) => void;
  };

  constructor(
    private readonly _meta: RequestMeta,
    private readonly _emit: (event: DeepPartial<CallToolEvent>) => void,
    /** Aborted when the client cancels the call or its deadline passes. */
    public readonly signal: AbortSignal,
  ) {
    this.log = {
      debug: (message) => this._log("debug", message),
      info: (message) => this._log("info", message),
      warning: (message) => this._log("warning", message),
      error: (message) => this._log("error", message),
    };
  }

  private _log(level: string, message: string): void {
    const wanted = this._meta.logLevel;
    if (wanted === undefined) return;
    if (LOG_LEVELS.indexOf(level) < LOG_LEVELS.indexOf(wanted)) return;
    this._emit({
      event: {
        $case: "log",
        log: { level, logger: "", data: JSON.stringify({ message, extra: null }) },
      },
    });
  }

  reportProgress(current: number, total?: number, message = ""): void {
    const token = this._meta.progressToken;
    if (token === undefined) return;
    this._emit({ event: { $case: "progress", progress: { token, progress: current, total, message } } });
  }

  async sample(): Promise<never> {
    throw new McpError(
      ErrorCode.MethodNotFound,
      "Sampling is not available on the v2 protocol; call your LLM provider directly",
    );
  }

  async listRoots(): Promise<never> {
    throw new McpError(
      ErrorCode.MethodNotFound,
      "Roots are not available on the v2 protocol; take paths as tool arguments",
    );
  }

  async elicit(): Promise<never> {
    throw new McpError(ErrorCode.MethodNotFound, "ctx.elicit() is not available on the v2 protocol yet");
  }
}
```

- [ ] **Step 5: The streaming RPCs**

In `typescript/src/v2/servicer.ts`:

Extend the type import from `../../generated/mcp_v2.js` with `type CallToolEvent`, `type CallToolRequest`, `type GetPromptEvent`, `type GetPromptRequest`, `type ReadResourceEvent`, `type ReadResourceRequest`, and add:

```typescript
import { paginate, parseToolArguments } from "../_utils.js";
import { Middleware, type CallToolResult, type ToolCallContext } from "../middleware.js";
import { AsyncQueue } from "../session.js";
import { LOG_LEVELS, V2Context } from "./context.js";
```

(the first replaces the existing `paginate` import).

Add `middlewares: Middleware[];` to `McpV2ServicerOptions`.

At the end of `_checkMeta`, add:

```typescript
    if (meta.logLevel !== undefined && !LOG_LEVELS.includes(meta.logLevel)) {
      throw toServerError(
        new McpError(ErrorCode.InvalidParams, `Unknown log level '${meta.logLevel}'`),
        context.trailer,
      );
    }
```

Append to the class:

```typescript
  /** A deliberate McpError goes out as it is; anything else becomes *fallback* and is logged. */
  private _failure(err: unknown, fallback: string, context: CallContext) {
    if (err instanceof McpError) return toServerError(err, context.trailer);
    console.error(`[rapidmcp] ${fallback}:`, err);
    return toServerError(new McpError(ErrorCode.InternalError, fallback), context.trailer);
  }

  private async _runTool(name: string, argumentsText: string, ctx: V2Context): Promise<CallToolResult> {
    const args = parseToolArguments(name, argumentsText);
    const tool = this._opts.toolManager.getTool(name);
    if (!tool) throw new McpError(ErrorCode.InvalidParams, `Tool '${name}' not found`);

    let inputSchema: Record<string, unknown> | null = null;
    if (tool.inputSchema && tool.inputSchema !== "{}") {
      try {
        inputSchema = JSON.parse(tool.inputSchema) as Record<string, unknown>;
      } catch {
        // A schema that does not parse is simply not offered to middleware.
      }
    }
    const base = (toolCtx: ToolCallContext) =>
      this._opts.toolManager.callTool(toolCtx.toolName, toolCtx.arguments, toolCtx.ctx);
    const chain = Middleware.buildChain(this._opts.middlewares, base);
    return chain({ toolName: name, arguments: args, ctx, inputSchema });
  }

  async *callTool(
    request: CallToolRequest,
    context: CallContext,
  ): AsyncGenerator<DeepPartial<CallToolEvent>> {
    this._checkMeta(request.meta, context);
    const name = request.name;

    // Events the tool emits and the "stop reading" marker share one queue, so
    // everything emitted before the tool settles is delivered before its result.
    const DONE = Symbol("done");
    const queue = new AsyncQueue<DeepPartial<CallToolEvent> | typeof DONE>();
    const ctx = new V2Context(request.meta!, (event) => queue.enqueue(event), context.signal);
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
    if ("error" in settled) throw this._failure(settled.error, `Tool call '${name}' failed`, context);
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

  async *readResource(
    request: ReadResourceRequest,
    context: CallContext,
  ): AsyncGenerator<DeepPartial<ReadResourceEvent>> {
    this._checkMeta(request.meta, context);
    let content;
    try {
      content = await this._opts.resourceManager.readResource(request.uri);
    } catch (err) {
      throw this._failure(err, `Resource handler for '${request.uri}' failed`, context);
    }
    yield { event: { $case: "complete", complete: { meta: this._resultMeta(), content, cache: NO_CACHE } } };
  }

  async *getPrompt(
    request: GetPromptRequest,
    context: CallContext,
  ): AsyncGenerator<DeepPartial<GetPromptEvent>> {
    this._checkMeta(request.meta, context);
    const name = request.name;
    const prompt = this._opts.promptManager.listPrompts().find((p) => p.name === name);
    if (prompt) {
      const missing = prompt.arguments
        .filter((a) => a.required && !(a.name in request.arguments))
        .map((a) => a.name);
      if (missing.length > 0) {
        throw toServerError(
          new McpError(
            ErrorCode.InvalidParams,
            `Missing required argument(s) for prompt '${name}': ${missing.join(", ")}`,
          ),
          context.trailer,
        );
      }
    }
    let messages;
    try {
      messages = await this._opts.promptManager.getPrompt(name, request.arguments);
    } catch (err) {
      throw this._failure(err, `Prompt handler '${name}' failed`, context);
    }
    yield { event: { $case: "complete", complete: { meta: this._resultMeta(), messages } } };
  }
```

In `typescript/src/server.ts`, add `middlewares: this._middlewares,` to the `new McpV2Servicer({ ... })` options.

- [ ] **Step 6: Run the tests to verify they pass**

```powershell
cd typescript; npx tsc -p tsconfig.build.json --noEmit; npx vitest run tests/v2-calls-server.test.ts; npx vitest run
```

Expected: type check clean; 21 tests pass in the new file; the full suite stays green except `tests/v2-client.test.ts` "says which operations v2 does not carry yet", which Task 5 rewrites.

- [ ] **Step 7: Commit**

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src typescript/tests/v2-calls-server.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): v2 tool calls, resource reads and prompts as streaming RPCs"
```

---

### Task 5: TypeScript client — calls on v2

**Files:**
- Modify: `typescript/src/session.ts` (`NotificationRegistry.has`)
- Modify: `typescript/src/types.ts` (`CallToolResult.structuredContent`)
- Modify: `typescript/src/v2/client-transport.ts`
- Modify: `typescript/src/client.ts`
- Modify: `typescript/tests/v2-client.test.ts` (one test now names a different operation)
- Modify: `CHANGELOG.md`
- Test: `typescript/tests/v2-calls-client.test.ts`

**Interfaces:**
- Consumes: the Task 1 stubs; `errorFromRpc`.
- Produces: `V2Transport` constructor gains a fifth argument `notifications: NotificationRegistry`; `callTool(name, args, opts?: { signal?: AbortSignal; timeout?: number })`, `readResource(uri)`, `getPrompt(name, args)`; `CallToolResult.structuredContent?: unknown` (client type); `NotificationRegistry.has(type): boolean`.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-calls-client.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError, ToolError } from "../src/errors.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("modern client: calls, reads and prompts over v2", () => {
  let server: RapidMCP;
  let client: Client;
  let aborted: string[];
  let port: number;

  beforeEach(async () => {
    aborted = [];
    server = new RapidMCP({ name: "calls", version: "1.0" });
    server.addTool({ name: "echo", execute: async (a: any) => a.text });
    server.addTool({ name: "add", execute: async (a: any) => ({ sum: a.a + a.b }) });
    server.addTool({
      name: "friendly",
      execute: async () => {
        throw new ToolError("order id must start with ORD-");
      },
    });
    server.addTool({
      name: "chatty",
      execute: async (_a: unknown, ctx: any) => {
        ctx.log.info("working");
        ctx.reportProgress(1, 2);
        return "done";
      },
    });
    server.addTool({
      name: "slow",
      execute: async (_a: unknown, ctx: any) => {
        ctx.signal.addEventListener("abort", () => aborted.push("slow"), { once: true });
        await sleep(600);
        return "done";
      },
    });
    server.addResource({ uri: "res://text", name: "text", load: async () => ({ text: "hello" }) });
    server.addResource({
      uri: "res://logo",
      name: "logo",
      mimeType: "image/png",
      load: async () => ({ blob: new Uint8Array([0x89, 0x50]) }),
    });
    server.addPrompt({
      name: "greet",
      arguments: [{ name: "who", required: true }],
      load: async (args) => `hi ${args.who}`,
    });
    port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  const codeOf = (p: Promise<unknown>) =>
    p.then(
      () => null,
      (e: unknown) => (e instanceof McpError ? e.code : `not an McpError: ${String(e)}`),
    );

  it("calls a tool", async () => {
    await client.connect();

    const result = await client.callTool("echo", { text: "hi" });

    expect([result.isError, result.content[0].text, result.structuredContent]).toEqual([false, "hi", undefined]);
  });

  it("parses structured content", async () => {
    await client.connect();

    const result = await client.callTool("add", { a: 2, b: 3 });

    expect(result.structuredContent).toEqual({ sum: 5 });
    expect(JSON.parse(result.content[0].text)).toEqual({ sum: 5 });
  });

  it("returns a ToolError as an error result", async () => {
    await client.connect();

    const result = await client.callTool("friendly");

    expect([result.isError, result.content[0].text]).toEqual([true, "order id must start with ORD-"]);
  });

  it("rejects an unknown tool with invalid params", async () => {
    await client.connect();

    expect(await codeOf(client.callTool("nope"))).toBe(-32602);
  });

  it("feeds progress and log handlers from the call's events", async () => {
    const progress: any[] = [];
    const logs: any[] = [];
    client.onNotification("progress", (payload) => void progress.push(JSON.parse(payload)));
    client.onNotification("log", (payload) => void logs.push(JSON.parse(payload)));
    await client.connect();

    const result = await client.callTool("chatty");

    expect(result.content[0].text).toBe("done");
    expect(progress.map((p) => [p.progress, p.total])).toEqual([[1, 2]]);
    expect(progress[0].token).toBeTruthy();
    expect(logs).toEqual([{ level: "info", message: "working", extra: null }]);
  });

  it("survives a notification handler that throws", async () => {
    client.onNotification("progress", () => {
      throw new Error("handler bug");
    });
    await client.connect();

    const result = await client.callTool("chatty");

    expect(result.content[0].text).toBe("done");
  });

  it("times out with 408 and aborts the tool", async () => {
    await client.connect();

    expect(await codeOf(client.callTool("slow", {}, { timeout: 200 }))).toBe(408);
    await sleep(100);

    expect(aborted).toEqual(["slow"]);
  });

  it("aborts the tool when the caller's signal aborts", async () => {
    await client.connect();
    const controller = new AbortController();

    const call = client.callTool("slow", {}, { signal: controller.signal }).catch((e) => e.message);
    await sleep(100);
    controller.abort();

    expect(await call).toBe("Aborted");
    await sleep(100);
    expect(aborted).toEqual(["slow"]);
  });

  it("reads resources", async () => {
    await client.connect();

    const text = await client.readResource("res://text");
    const logo = await client.readResource("res://logo");

    expect([text.content[0].type, text.content[0].text]).toEqual(["text", "hello"]);
    expect([logo.content[0].type, [...logo.content[0].data]]).toEqual(["image", [0x89, 0x50]]);
    expect(await codeOf(client.readResource("res://nope"))).toBe(-32602);
  });

  it("gets a prompt", async () => {
    await client.connect();

    const result = await client.getPrompt("greet", { who: "Ada" });

    expect(result.messages.map((m) => [m.role, m.content.text])).toEqual([["user", "hi Ada"]]);
    expect(await codeOf(client.getPrompt("greet"))).toBe(-32602);
  });
});
```

In `typescript/tests/v2-client.test.ts`, replace the test "says which operations v2 does not carry yet" with:

```typescript
  it("says which operations v2 does not carry yet", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    let err: unknown = null;
    try {
      client.subscribeResource("res://a");
    } catch (e) {
      err = e;
    }

    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(-32601);
    expect((err as McpError).message).toContain("legacy");
  });
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-calls-client.test.ts
```

Expected: FAIL — `callTool is not available on the v2 protocol yet` (and the same for `readResource`, `getPrompt`).

- [ ] **Step 3: Supporting changes**

`typescript/src/session.ts`, in `NotificationRegistry`:

```typescript
  /** True when at least one handler is registered for *type*. */
  has(type: string): boolean {
    return (this._handlers.get(type)?.length ?? 0) > 0;
  }
```

`typescript/src/types.ts`, in the client `CallToolResult` interface:

```typescript
export interface CallToolResult {
  content: ContentItem[];
  isError: boolean;
  /** The tool's result as a JSON value, when the server sent one (v2 only). */
  structuredContent?: unknown;
}
```

- [ ] **Step 4: The transport**

In `typescript/src/v2/client-transport.ts`:

Imports — extend:

```typescript
import {
  McpDefinition,
  type CallToolResult as WireCallToolResult,
  type GetPromptResult as WireGetPromptResult,
  type McpClient,
  type ReadResourceResult as WireReadResourceResult,
  type RequestMeta,
} from "../../generated/mcp_v2.js";
import type { NotificationRegistry } from "../session.js";
```

and add to the `../types.js` import: `convertCallToolResult`, `convertGetPromptResult`, `convertReadResourceResult`, `type CallToolResult`, `type GetPromptResult`, `type ReadResourceResult`.

Constructor — a fifth parameter and a counter:

```typescript
  private _client: McpClient;
  private _nextProgressToken = 1;

  constructor(
    channel: Channel,
    private readonly _opts: ClientOptions,
    private readonly _timeoutMs: number,
    private readonly _supportsElicitation: () => boolean,
    private readonly _notifications: NotificationRegistry,
  ) {
    this._client = createClientFactory().create(McpDefinition, channel);
  }
```

`_meta` takes a flag:

```typescript
  private _meta(events = false): RequestMeta {
    return {
      protocolVersion: PROTOCOL_VERSION,
      clientCapabilities: {
        elicitation: this._supportsElicitation() ? { form: true, url: false } : undefined,
        extensions: {},
      },
      clientInfo: { name: "rapidmcp-typescript", version: "0.3.0" },
      progressToken:
        events && this._notifications.has("progress") ? `p${this._nextProgressToken++}` : undefined,
      logLevel: events && this._notifications.has("log") ? "debug" : undefined,
    };
  }
```

Append to the class:

```typescript
  private async _notify(kind: string, payload: unknown): Promise<void> {
    try {
      await this._notifications.dispatch(kind, JSON.stringify(payload));
    } catch (err) {
      console.error(`[rapidmcp] notification handler for '${kind}' failed:`, err);
    }
  }

  /** Run a streaming RPC to its terminal event, feeding progress and log handlers. */
  private async _stream<R>(
    open: (options: CallOptions) => AsyncIterable<{ event?: { $case: string } | undefined }>,
    opts: { signal?: AbortSignal; timeout?: number } = {},
  ): Promise<R> {
    if (opts.signal?.aborted) throw new McpError(-1, "Aborted");
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, opts.timeout ?? this._timeoutMs);
    const onAbort = () => controller.abort();
    opts.signal?.addEventListener("abort", onAbort, { once: true });

    let trailer: Metadata | null = null;
    const options: CallOptions = {
      signal: controller.signal,
      onTrailer: (t) => {
        trailer = t;
      },
    };
    if (this._opts.token) options.metadata = buildMetadata(this._opts);

    try {
      for await (const message of open(options)) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any -- one loop serves three event unions
        const event = message.event as any;
        if (!event) continue;
        if (event.$case === "progress") {
          const p = event.progress;
          await this._notify("progress", {
            progress: p.progress,
            total: p.total ?? null,
            message: p.message,
            token: p.token,
          });
        } else if (event.$case === "log") {
          const data = event.log.data ? JSON.parse(event.log.data) : {};
          await this._notify("log", {
            level: event.log.level,
            message: data.message ?? null,
            extra: data.extra ?? null,
          });
        } else if (event.$case === "complete") {
          return event.complete as R;
        } else if (event.$case === "inputRequired") {
          throw new McpError(
            ErrorCode.MethodNotFound,
            "The server asked for input, which this client cannot give yet",
          );
        }
      }
      throw new McpError(ErrorCode.InternalError, "The server ended the call without a result");
    } catch (err) {
      if (err instanceof McpError) throw err;
      if (err instanceof ClientError) {
        const mapped = errorFromRpc(err.code, err.details, trailer);
        if (mapped) throw mapped;
      } else if (err instanceof Error && err.name === "AbortError") {
        throw timedOut
          ? new McpError(ErrorCode.RequestTimeout, "Request timeout")
          : new McpError(-1, "Aborted");
      }
      throw err;
    } finally {
      clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
    }
  }

  async callTool(
    name: string,
    args: Record<string, unknown>,
    opts: { signal?: AbortSignal; timeout?: number } = {},
  ): Promise<CallToolResult> {
    const wire = await this._stream<WireCallToolResult>(
      (o) =>
        this._client.callTool(
          { meta: this._meta(true), name, arguments: JSON.stringify(args), inputResponses: {} },
          o,
        ),
      opts,
    );
    return {
      ...convertCallToolResult(wire),
      structuredContent: wire.structuredContent ? JSON.parse(wire.structuredContent) : undefined,
    };
  }

  async readResource(uri: string): Promise<ReadResourceResult> {
    const wire = await this._stream<WireReadResourceResult>((o) =>
      this._client.readResource({ meta: this._meta(), uri, inputResponses: {} }, o),
    );
    return convertReadResourceResult(wire);
  }

  async getPrompt(name: string, args: Record<string, string>): Promise<GetPromptResult> {
    const wire = await this._stream<WireGetPromptResult>((o) =>
      this._client.getPrompt({ meta: this._meta(), name, arguments: args, inputResponses: {} }, o),
    );
    return convertGetPromptResult(
      wire as unknown as Parameters<typeof convertGetPromptResult>[0],
    );
  }
```

- [ ] **Step 5: Route the client**

In `typescript/src/client.ts`:

In `_doConnect`, pass the registry as the fifth argument:

```typescript
      const transport = new V2Transport(
        this._channel,
        this._opts,
        this._requestTimeout,
        () => this._elicitationHandler !== null,
        this._notifications,
      );
```

Replace `this._v1Only("callTool");` with:

```typescript
    if (this._v2) return this._v2.callTool(name, args, opts);
```

Replace `this._v1Only("readResource");` with:

```typescript
    if (this._v2) return this._v2.readResource(uri);
```

Replace `this._v1Only("getPrompt");` with:

```typescript
    if (this._v2) return this._v2.getPrompt(name, args);
```

- [ ] **Step 6: Run the tests to verify they pass**

```powershell
cd typescript; npx tsc -p tsconfig.build.json --noEmit; npx vitest run tests/v2-calls-client.test.ts tests/v2-client.test.ts; npx vitest run
```

Expected: type check clean; 10 new tests pass; the full suite is green.

- [ ] **Step 7: Changelog and commit**

Replace both "Protocol v2 (experimental, phase 1)" bullets in `CHANGELOG.md` (one under Python, one under TypeScript) with, respectively:

```markdown
- **Protocol v2 (experimental):** servers also answer the stateless `mcp.v2.Mcp` service, following MCP 2026-07-28 — discovery, lists, completion, and tool calls, resource reads and prompts as streaming RPCs with per-request progress and log messages, structured tool results, deadlines and cancellation. `Client(mode="modern")` speaks it; `mode="auto"` tries it and falls back to v1; the default stays `"legacy"`. Not on v2 yet: asking the user for input, and subscriptions
```

```markdown
- **Protocol v2 (experimental):** servers also answer the stateless `mcp.v2.Mcp` service, following MCP 2026-07-28 — discovery, lists, completion, and tool calls, resource reads and prompts as streaming RPCs with per-request progress and log messages, structured tool results, deadlines and cancellation. `new Client(addr, { mode: "modern" })` speaks it; `mode: "auto"` tries it and falls back to v1; the default stays `"legacy"`. Not on v2 yet: asking the user for input, and subscriptions
```

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src typescript/tests CHANGELOG.md
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): modern clients call tools, read resources and get prompts over v2"
```
