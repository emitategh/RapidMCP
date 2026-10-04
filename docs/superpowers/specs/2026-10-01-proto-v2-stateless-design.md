# RapidMCP proto v2 — following MCP 2026-07-28

Status: approved by the owner on 2026-10-01, with the five recommended decisions
at the end accepted · phase 1 plan: `docs/superpowers/plans/2026-10-01-proto-v2-phase-1.md`

## What this is for

MCP revision `2026-07-28` changed the shape of the protocol: it is stateless,
servers no longer send requests to clients, and several methods were removed or
replaced. RapidMCP today implements the session model that revision replaces.

The goal is a second protocol version for RapidMCP that carries the
`2026-07-28` semantics over gRPC, while servers and clients built on the
current protocol keep working.

**What the owner asked for:** track the `2026-07-28` semantics; write the
design before touching code; keep the current stream as the legacy path.

**Assumptions I made** (correct me):

- "Follow the spec" means follow its semantics. RapidMCP uses protobuf, and the
  spec requires custom transports to keep the JSON-RPC message format, so
  RapidMCP cannot be a conforming MCP transport and this design does not try
  to make it one.
- Python and TypeScript move together and stay wire-compatible with each other.
- Existing user code (`@server.tool`, `ctx.elicit`, `client.call_tool`) should
  keep working with as few changes as possible.

**Success looks like:**

1. A v2 client and a v2 server exchange every supported MCP operation with no
   handshake and no state held per connection.
2. A tool written once with `ctx.elicit()` works against both a v1 and a v2 client.
3. A v1 client still works against a server that also serves v2, and a v2
   client falls back to v1 against an old server.
4. Any replica behind a plain gRPC load balancer can answer any v2 request.
5. The per-call latency of v2 is measured against v1 and published; see Risks.

## Out of scope

- JSON-RPC framing, Streamable HTTP interop, and the OAuth authorization framework.
- The Tasks extension and MCP Apps.
- Sampling and Roots in v2. The spec deprecates both and says new
  implementations should not adopt them; they stay available on v1 only.
- Server-side session storage (FastMCP's `UserSession` / `SessionId`). State
  that spans requests is passed as explicit tool arguments, as the spec says.
- A client-side response cache. v2 carries the cache hints; using them is later work.
- Method-agnostic middleware hooks (`on_request` for every message). gRPC
  interceptors already cover this; a RapidMCP-level hook can follow separately.

## Approach

Three shapes were considered.

**A. Keep the single bidirectional stream and add per-request metadata to it.**
Smallest change. But the stream stays pinned to one server process, so a load
balancer cannot spread requests; cancellation and correlation stay custom
(request ids, cancel messages) on top of HTTP/2, which already multiplexes.

**B. One gRPC method per MCP operation (recommended).** Lists and discovery are
unary; operations that run user code and may emit progress or log messages are
server-streaming. Cancellation is cancelling the RPC, timeouts are gRPC
deadlines, auth interceptors run per request, and standard gRPC tooling
(load balancers, `grpcurl`, OpenTelemetry interceptors) sees each operation by
name. This is the direct equivalent of the spec's Streamable HTTP binding:
one request, one response or one request-scoped stream. The cost is a new
service and a per-call HTTP/2 stream instead of a message on an open stream.

**C. One generic server-streaming method carrying a `oneof` of all requests.**
Mirrors the spec's single endpoint, but gives up per-method typing and
observability for no gain over B.

The design below is B. With no server-to-client requests left in the protocol,
the reason for the bidirectional stream is gone.

## The v2 service

New file `proto/mcp_v2.proto`, package `mcp.v2`. `proto/mcp.proto` (v1) is not
changed. A server registers both services on the same port.

```proto
service Mcp {
  rpc Discover(DiscoverRequest) returns (DiscoverResult);

  rpc ListTools(ListToolsRequest) returns (ListToolsResult);
  rpc CallTool(CallToolRequest) returns (stream CallToolEvent);

  rpc ListResources(ListResourcesRequest) returns (ListResourcesResult);
  rpc ListResourceTemplates(ListResourceTemplatesRequest) returns (ListResourceTemplatesResult);
  rpc ReadResource(ReadResourceRequest) returns (stream ReadResourceEvent);

  rpc ListPrompts(ListPromptsRequest) returns (ListPromptsResult);
  rpc GetPrompt(GetPromptRequest) returns (stream GetPromptEvent);

  rpc Complete(CompleteRequest) returns (CompleteResult);

  rpc Listen(ListenRequest) returns (stream ListenEvent);
}
```

There is no `Initialize`, no `Ping` and no `SetLevel`; the spec removed all
three. Liveness is gRPC keepalive or the standard gRPC health service.

### Per-request metadata

Every request message has `RequestMeta meta = 1`. This is the spec's `_meta`.

```proto
message RequestMeta {
  string protocol_version = 1;                 // required, e.g. "2026-07-28"
  ClientCapabilities client_capabilities = 2;  // required
  Implementation client_info = 3;              // name + version, optional
  optional string log_level = 4;               // opt in to log messages for this request
  optional string progress_token = 5;          // opt in to progress for this request
}

message ClientCapabilities {
  ElicitationCapability elicitation = 1;       // unset = not supported
  map<string, google.protobuf.Struct> extensions = 15;
}
message ElicitationCapability { bool form = 1; bool url = 2; }
```

Rules, all from the spec:

- A request without `protocol_version` or `client_capabilities` is rejected
  with `-32602`.
- A version the server does not serve is rejected with `-32022` and the list
  of supported versions.
- The server never uses a capability the request did not declare; needing one
  that is missing is `-32021`.
- The server reads nothing from earlier requests on the same connection.

Trace context (`traceparent`, `tracestate`, `baggage`) travels as gRPC
metadata, which is where gRPC's OpenTelemetry instrumentation already puts it,
rather than in the message body. The tool name of a `CallTool` request is
mirrored into a `mcp-name` metadata key so proxies can route without decoding
the body; the body stays the source of truth.

### Results, and where `resultType` went

The spec adds a required `resultType` of `complete` or `input_required`. In
protobuf this is a `oneof`, so there is no string field to get wrong:

```proto
message CallToolEvent {
  oneof event {
    Progress progress = 1;            // only if meta.progress_token was set
    LogMessage log = 2;               // only if meta.log_level was set
    CallToolResult complete = 3;      // terminal
    InputRequired input_required = 4; // terminal
  }
}
```

A stream carries any number of `progress` / `log` events and ends with exactly
one terminal event. `ReadResourceEvent` and `GetPromptEvent` have the same
shape. Unary results are always complete.

Every terminal result and every unary result carries `ResultMeta` with the
server's `Implementation` (name and version), as the spec asks.

### Discovery

```proto
message DiscoverResult {
  ResultMeta meta = 1;
  repeated string supported_versions = 2;
  ServerCapabilities capabilities = 3;
  string instructions = 4;
  CacheHint cache = 5;
}
message ServerCapabilities {
  ToolsCapability tools = 1;         // { bool list_changed }
  ResourcesCapability resources = 2; // { bool list_changed; bool subscribe }
  PromptsCapability prompts = 3;     // { bool list_changed }
  bool logging = 4;
  map<string, google.protobuf.Struct> extensions = 15;
}
```

Calling `Discover` is optional for a client, mandatory for a server to implement.

### Cache hints

```proto
message CacheHint { uint64 ttl_ms = 1; CacheScope scope = 2; }  // PUBLIC | PRIVATE
```

Present on `DiscoverResult`, the four list results and the complete
`ReadResource` result. Server API: `RapidMCP(cache_ttl=..., cache_scope=...)`,
defaulting to `0` and `private`, which is the spec's "immediately stale".

### Asking the client for input (multi-round-trip requests)

A server can no longer send a request to the client mid-call. Instead the call
ends with `input_required`, and the client calls again with the answers.

```proto
message InputRequired {
  map<string, InputRequest> input_requests = 1;  // keys chosen by the server
  bytes request_state = 2;                        // opaque to the client
}
message InputRequest  { oneof request  { ElicitRequest elicit = 1; } }
message InputResponse { oneof response { ElicitResult  elicit = 1; } }

message CallToolRequest {
  RequestMeta meta = 1;
  string name = 2;
  google.protobuf.Struct arguments = 3;
  map<string, InputResponse> input_responses = 4;  // retry only
  bytes request_state = 5;                          // retry only, echoed unchanged
}
```

`ElicitRequest` carries the message and one of two modes from the spec: a form
(a JSON Schema, as a `Struct`) or a URL for interactions that must not pass
through the client. `ElicitResult` carries the action (`accept`, `decline`,
`cancel`) and, for an accepted form, the content as a `Struct`. A server never
sends a mode the request's capabilities did not declare.

`ReadResourceRequest` and `GetPromptRequest` carry the same two retry fields.
Field numbers 2 and 3 of both `oneof`s are reserved for sampling and roots
should they ever be needed.

**Amendment (2026-10-01, while planning phase 1):** JSON-shaped values —
tool arguments, input and output schemas, elicitation forms and content,
extension settings — travel as JSON text (`string`), as in v1, not as
`google.protobuf.Struct`. `Struct` stores every number as a double, so an
integer argument or a schema's `minLength: 2` would arrive as `2.0`, and
integers above 2^53 would lose precision. Read every `Struct` in the sketches
of this document as a JSON string. In the same spirit, the four tool
annotation hints are `optional bool` in v2, so "not set" is distinguishable
from `false` and the client can apply MCP's defaults.

**Authoring a tool.** There are two levels.

The explicit level matches the spec one-to-one: the tool reads
`ctx.input_responses` and returns `InputRequired(...)` itself.

The convenient level keeps today's call:

```python
@server.tool()
async def deploy(service: str, ctx: Context) -> str:
    answer = await ctx.elicit("Deploy to production?", fields={"confirm": BoolField()})
    if not answer.accepted:
        return "cancelled"
    return do_deploy(service)
```

On a v1 connection `ctx.elicit()` sends a request down the stream and waits,
as it does now. On a v2 request it looks for the answer under a key derived
from its position in the call (`elicit-0`, `elicit-1`, …, or an explicit
`key=`). If the answer is there it returns it. If not, it ends the call with
`input_required`, and the tool **runs again from the top** when the client
retries. This is what makes one tool work on both eras.

The consequence must be documented prominently: on v2, everything before an
unanswered `ctx.elicit()` runs once per round. Ask first, act afterwards.

**`request_state`.** A client only sends answers to the latest questions, so
earlier answers ride in `request_state`. The framework builds and checks it;
tool authors never see it. Following the spec's requirements, it is:

- integrity-protected with HMAC-SHA256 under `RapidMCP(state_secret=...)`. If
  no secret is configured the server generates one at start-up, which is
  correct for a single process and wrong behind a load balancer; the server
  logs a warning when it issues state without a configured secret.
- bound to the operation: method, tool/resource/prompt name, and a digest of
  the arguments. State presented on a different request is rejected.
- bound to the caller when the server has `auth` configured (a digest of the
  bearer token).
- short-lived: an expiry inside the signed payload, default ten minutes.

State that fails any check is rejected with `-32602`. The client is capped at
ten rounds per call, after which it raises.

### Subscriptions

`Listen` replaces `subscribe_res` and the unconditional broadcast.

```proto
message ListenRequest {
  RequestMeta meta = 1;
  NotificationFilter notifications = 2;
}
message NotificationFilter {
  bool tools_list_changed = 1;
  bool prompts_list_changed = 2;
  bool resources_list_changed = 3;
  repeated string resource_subscriptions = 4;
}
message ListenEvent {
  oneof event {
    NotificationFilter acknowledged = 1;  // always first: what the server agreed to
    ToolsListChanged tools_list_changed = 2;
    PromptsListChanged prompts_list_changed = 3;
    ResourcesListChanged resources_list_changed = 4;
    ResourceUpdated resource_updated = 5; // { string uri }
  }
}
```

The server sends only what was asked for. The stream ending with status `OK`
is a graceful close; any other status is a drop the client may reconnect from.
The spec's `subscriptionId` exists to demultiplex notifications on a shared
channel; here each subscription is its own RPC, so it is not needed.

A server holds subscription state only for the life of a `Listen` call. Behind
a load balancer, `notify_tools_list_changed()` reaches the subscribers
connected to that replica; fanning out across replicas needs a message bus and
is not part of this design.

### Progress and logging

`ctx.report_progress()` emits a `Progress` event (`token`, `progress`, `total`,
`message`) on the call's own stream, and only when the request set
`progress_token`; the token is echoed back unchanged. `ctx.info()` and friends emit a
`LogMessage` only when the request set `log_level`, and only at or above it.
Otherwise both are silently dropped, which is what the spec requires. Logging
is deprecated in the spec; it is kept here because it costs almost nothing,
and marked deprecated in the docs.

### Cancellation and timeouts

The client cancels a request by cancelling the RPC. The server sees the
cancellation through gRPC, stops the handler (Python: `CancelledError`;
TypeScript: `ctx.signal`) and sends nothing. A client timeout is a gRPC
deadline, so the server also knows how much time is left. There are no cancel
messages and no request ids in v2.

### Errors

Failures are gRPC statuses, so standard tooling sees something meaningful, and
the exact MCP code travels in trailing metadata so the client can rebuild the
same `McpError` it raises today.

| MCP code | Meaning | gRPC status |
|---|---|---|
| `-32602` | invalid params; unknown tool, resource or prompt; bad `request_state` | `INVALID_ARGUMENT` |
| `-32601` | unknown method | `UNIMPLEMENTED` |
| `-32603` | internal error | `INTERNAL` |
| `-32021` | missing client capability | `FAILED_PRECONDITION` |
| `-32022` | unsupported protocol version | `FAILED_PRECONDITION` |
| — | bad or missing token | `UNAUTHENTICATED` |
| — | cancelled by the client | `CANCELLED` |
| — | deadline exceeded | `DEADLINE_EXCEEDED` |

Trailing metadata: `mcp-error-code` (the integer) and `mcp-error-data-bin` (a
serialized `ErrorData` message, e.g. the supported versions for `-32022`).

A tool that fails is still a successful call whose result has `is_error` set,
as today.

## Serving both versions

A server registers `mcp.Mcp` (v1) and `mcp.v2.Mcp` on one port. Tools,
resources, prompts and middleware are registered once and served by both.
v1 is frozen: it gets fixes, not features.

A client takes `mode`:

- `"auto"` (default): call `mcp.v2.Mcp/Discover`. A gRPC `UNIMPLEMENTED` means
  an old server, and the client falls back to the v1 stream. The answer is
  remembered for the life of the client.
- `"modern"`: v2 only.
- `"legacy"`: v1 only.

The public client API does not change shape:

| Call | On v2 |
|---|---|
| `list_tools`, `list_resources`, `list_prompts`, `list_resource_templates`, `complete` | unary RPC |
| `call_tool`, `read_resource`, `get_prompt` | streaming RPC; the client runs the input-required loop using the registered elicitation handler |
| `on_notification("progress" / "log", …)` | fed from the call's stream; the client sets `progress_token` / `log_level` when a handler is registered |
| `on_notification("tools_list_changed", …)`, `subscribe_resource(uri)` | open (or reopen) one `Listen` stream with the union of what was asked |
| `ping()` | a `Discover` call |
| `cancel(request_id)` | not available; cancel the awaiting task (Python) or abort the signal (TypeScript) |
| `set_sampling_handler`, `set_roots_handler` | ignored on v2; a warning is logged once |
| `server_info` | filled from `Discover` |

The LangChain and LiveKit adapters sit on the client and need no changes.

## Components

Each language gets the same four units.

| Unit | Does | Depends on |
|---|---|---|
| Generated stubs (`mcp_v2_pb2*`, `generated/mcp_v2.ts`) | wire types | `proto/mcp_v2.proto` |
| v2 servicer | one handler per RPC; builds a per-request `Context` from `meta`; maps errors to statuses | tool / resource / prompt managers (shared with v1) |
| Request-state codec | sign, verify, expire and bind `request_state` | nothing else; pure functions, unit-tested alone |
| v2 client transport | the RPC calls, the input-required loop, the `Listen` stream | generated stubs |

`Context` gets a small internal interface (`elicit`, `report_progress`, `log`)
with two implementations, one per era, so tool code and middleware do not know
which era they are running in. `Client` gets the same split: a transport
interface with a v1 and a v2 implementation behind the existing methods.

One existing defect has to be fixed along the way: the Python auth
interceptor's wrapper for server-streaming methods is a coroutine where it
must be an async generator (the code carries a note saying so). v1 never used
that path; v2 does.

## Testing

- **Codec:** unit tests for tampered, expired, wrong-operation and
  wrong-principal state.
- **Per RPC:** a real loopback server and client in each language, as the
  current integration tests do.
- **Statelessness:** two server instances sharing a `state_secret`, with each
  request of a multi-round call sent to a different instance.
- **Dual era:** a v1 client against a dual server; a `mode="auto"` client
  against a v1-only server; the same tool using `ctx.elicit()` under both.
- **Cross-language:** a Python client against a TypeScript server and the
  reverse, for every RPC. The repo has no such test today, and this is what
  keeps the two implementations honest.
- **Benchmark:** v2 `CallTool` against v1 and against FastMCP over Streamable
  HTTP, using the existing harness in `benchmark/`.

## Delivery

This is too large for one implementation plan. Five phases, each shippable:

1. Proto v2, code generation in both languages, `Discover`, the unary list
   RPCs, error mapping, dual registration, client `mode`.
2. `CallTool`, `ReadResource`, `GetPrompt` as streams, with progress and
   logging gated by `meta`, deadlines and cancellation.
3. Input-required rounds: the state codec, era-neutral `ctx.elicit()`, the
   client loop.
4. `Listen`.
5. Cache hints, cross-language tests, benchmark, documentation, and the v1
   deprecation notice.

The first implementation plan covers phase 1 only.

## Risks

- **Latency.** RapidMCP's headline is low per-call latency on an open stream.
  v2 opens an HTTP/2 stream per call. HTTP/2 multiplexes these on one
  connection and header compression keeps them small, so the cost should be
  modest, but it is not measured. Phase 2 ends with the benchmark; if v2
  `CallTool` is more than twice the v1 median, the design is revisited before
  phase 3 (the fallback is approach A for calls only).
- **Re-running tools.** The convenient `ctx.elicit()` re-executes the tool per
  round. Tools with side effects before the question will repeat them. The
  explicit form is the escape hatch; the docs must lead with the rule.
- **Two code paths.** Until v1 is removed, every server feature exists twice
  at the wire layer. Sharing the managers and `Context` interface limits this
  to the servicer and client transport.

## Addendum (2026-10-04): what "implement all missing" adds

The owner asked for everything the standard has and RapidMCP lacks. Phases 2–5
above cover most of it. Three items were in neither version and are added here.
JSON-RPC framing, the stdio and HTTP transports, OAuth, the Tasks extension and
MCP Apps stay out of scope, for the reason given under "Out of scope".

**Structured tool results (phase 2).** A v2 `CallToolResult` carries
`structured_content`, the tool's result as JSON text, next to `content`. A tool
that returns a JSON object (a `dict` in Python, a plain object in TypeScript)
gets both: the object as structured content and the same JSON as a text block,
which is what the standard asks for so older clients still see something. The
result is not validated against the tool's output schema; that stays the
author's responsibility. v1 is unchanged.

**Trace context (phase 5).** `traceparent`, `tracestate` and `baggage` travel as
gRPC metadata on v2 calls. The client takes a `trace_context` provider, called
once per request; the server exposes what arrived as `ctx.trace_context`.
RapidMCP only carries the values; creating spans is left to the application's
OpenTelemetry setup.

**Icons (phase 5).** Tools, resources, resource templates, prompts and the
server itself can declare `icons` (`src`, `mime_type`, `sizes`, `theme`), sent
on v2 only. The standard's rules for consuming icons (HTTPS or `data:` only,
same origin, no credentials) are the client application's to enforce; the
library validates the scheme of `src` when an icon is registered and otherwise
passes icons through.

## Decisions for the owner

These are the choices where a different answer changes the design. My
recommendation is the one written into the document above.

1. **Service shape:** one RPC per operation (B), over the alternatives A and C.
2. **Sampling and roots:** left out of v2, available on v1 only.
3. **`ctx.elicit()` on v2:** re-run the tool per round, rather than offering
   only the explicit return-`InputRequired` form.
4. **How long v1 lives:** this document does not set a removal date. A
   reasonable rule is to keep v1 until the spec itself removes the features it
   carries, which is no earlier than 2027-07-28.
5. **Versioning:** ship the current branch as 0.5.0 (Python) and 0.3.0
   (TypeScript) first; v2 arrives in the release after, with v2 as the client
   default only once phase 3 is done.
