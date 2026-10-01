# Breaking changes — next release

Release note for the work on `fix/review-findings`. Everything here is already
listed (tersely) under `[Unreleased]` in `CHANGELOG.md`; this file is the version
with the "what do I have to change" part, meant to be pasted into the release
notes when the packages are built.

The wire format did not change — `proto/mcp.proto` is untouched — so old clients
keep talking to new servers and the other way round. What changed is behaviour
on top of the wire.

## Before building

- [ ] Bump versions. These are breaking, so a minor bump on a 0.x line:
      `python/pyproject.toml` 0.4.0 → 0.5.0 (then `uv lock` for the self-reference),
      `typescript/package.json` 0.2.0 → 0.3.0.
- [ ] In `CHANGELOG.md`, rename both `[Unreleased]` headings to the new versions with the date.
- [ ] Rebuild `typescript/dist` (`npm run build`) — the current build predates all of this.
- [ ] Run the Docker-backed tests (`tests/test_tls_docker.py`, `tests/test_stress_subprocess.py`);
      they were not run while these changes were made.
- [ ] Update `CLAUDE.md` and the READMEs (test counts, TypeScript feature list, new options).
- [ ] Decide the two open questions at the bottom of this file.

## Python (`rapidmcp`)

### Tool failures no longer return a traceback

| | Before | Now |
|---|---|---|
| handler raises `RuntimeError("boom")` | full `Traceback (most recent call last): …` text | `Error calling tool 'name': boom` |
| handler raises `ToolError("msg")` | traceback ending in `ToolError: msg` | `msg` |

`is_error` is `True` in both cases, as before. The traceback is still written to
the server log.

**What to change:** anything that parses the error text (tests asserting on
`"Traceback"`, clients matching exception class names). To hide the exception
message as well, construct the server with `RapidMCP(..., mask_error_details=True)`.
Raise `ToolError` for messages meant for the model.

### Prompts are returned as `user` messages

`get_prompt` used to return the prompt text with role `assistant`; it is now
`user`, which is what the TypeScript server and FastMCP return.

**What to change:** code that checks `message.role == "assistant"`. With the
LangChain adapter a prompt now becomes a `HumanMessage` instead of an `AIMessage`.

### Tool input schemas describe the real types

Only `str`, `int`, `float` and `bool` used to be mapped; every other annotation
was advertised as `"string"`.

| Annotation | Before | Now |
|---|---|---|
| `list[str]` | `{"type": "string"}` | `{"type": "array", "items": {"type": "string"}}` |
| `dict[str, int]` | `{"type": "string"}` | `{"type": "object", "additionalProperties": {"type": "integer"}}` |
| `int \| None` | `{"type": "string"}` | `{"anyOf": [{"type": "integer"}, {"type": "null"}]}` |
| `Literal["a", "b"]`, `Enum` | `{"type": "string"}` | `{"enum": [...]}` |
| no annotation, `Any`, custom class | `{"type": "string"}` | `{}` |
| `*args`, `**kwargs` | listed as parameters | not listed |

**What to change:** handlers that compensated for the old schema — e.g. a
`tags: list[str]` parameter that actually received a string and called
`json.loads` or `.split(",")` on it. Models will now send a real list.

### In-flight tools are cancelled when nobody is waiting

A tool is cancelled (`asyncio.CancelledError`) when the client disconnects, when
the client cancels the call, and when the client's request timeout expires.
Before, the tool ran to completion in all three cases.

**What to change:** tools that are meant to keep running after the caller has
gone must hand the work off themselves (`asyncio.create_task`, a queue, a job
runner). Tools holding resources should release them in `try/finally`.

### Error codes follow MCP

Codes a server sends now use the JSON-RPC values MCP specifies. Codes the
client raises by itself keep their HTTP-style numbers, so the two cannot be
confused.

| Situation | Before | Now |
|---|---|---|
| Unknown tool, resource or prompt | `404` | `-32602` |
| Arguments are not a JSON object | `400` | `-32602` |
| Unknown message type | `400` | `-32601` |
| A handler failed | `500` | `-32603` |
| Client lacks a capability the tool needs | `400` | `-32021` |
| Request timed out (raised by the client) | `408` | `408` |
| Request cancelled (raised by the client) | `499` from the server | `499`, raised locally |
| Not connected (raised by the client) | `503` | `503` |

The constants are in `rapidmcp.errors` (`INVALID_PARAMS`, `INTERNAL_ERROR`, …).

Two related changes:

- An `McpError` raised inside a tool is now returned as an error instead of
  `is_error` tool output. The usual case is `ctx.elicit()` / `ctx.sample()`
  against a client that did not declare the capability: `call_tool` now raises
  `McpError(-32021)` instead of returning an error result. `ToolError` still
  produces `is_error` output.
- The server no longer answers a cancelled call. `Client.cancel(request_id)`
  fails the pending call locally with `McpError(499)`.

**What to change:** `except McpError` blocks and tests that compare `code`
against `404`, `400` or `500`.

### Annotation hints default to "not set"

`@server.tool(read_only=..., destructive=..., idempotent=..., open_world=...)` and
`ToolAnnotations` now default every hint to `None` instead of `False`.
`ToolAnnotations().destructive_hint` is `None`. On the v1 protocol an unset
hint is still sent as `false`, so v1 clients see no change; on v2 it is left
unset and the client reports MCP's defaults (destructive and open-world).

**What to change:** code that reads `tool.annotations.<hint> is False` on the
server side. Pass `destructive=False` explicitly to assert that a tool is not
destructive.

### Sampling and roots are deprecated

MCP 2026-07-28 deprecates Sampling and Roots (removal no earlier than
2027-07-28). `ctx.sample()` and `ctx.list_roots()` still work and are
documented as deprecated. Call your LLM provider from the server, and take
directories or files as tool arguments.

### Smaller behaviour changes

- **Requests on a dead connection** raise `McpError(503)` immediately instead of
  `McpError(408)` after 30 seconds.
- **Capabilities:** `tools_list_changed` is now `True`, and a server with only
  resource templates reports `resources=True`.
- **URI templates:** a percent-encoded slash or backslash no longer matches a
  single-segment `{var}` (`res://files/a%2Fb` against `res://files/{name}` is
  now "not found"); use `{name*}` if slashes are legitimate. No variable may
  contain a `.` or `..` segment or a NUL byte.
- **Failing handlers:** a completion handler that raises now produces
  `McpError(500)` for that request; it used to end the whole stream with a gRPC
  `UNKNOWN` status.
- **`Client._REQUEST_TIMEOUT` is gone.** Pass `Client(..., request_timeout=...)`
  or `call_tool(..., timeout=...)`.
- **`rapidmcp.__version__`** now reports the installed package version (it said
  `0.1.0` regardless).

## TypeScript (`@emitate/rapidmcp`)

### Tool failures name the tool

| | Before | Now |
|---|---|---|
| `execute` throws `new Error("boom")` | `boom` | `Error calling tool 'name': boom` |
| `execute` throws `new ToolError("msg")` | `msg` | `msg` (unchanged) |

**What to change:** assertions on the exact error text. `new RapidMCP({ maskErrorDetails: true })`
drops the message for non-`ToolError` exceptions.

### Timeouts reject with code 408

A request that times out rejects with `McpError` code `408` (was `-1`). The
message is still `Request timeout`.

**What to change:** `err.code === -1` checks. Note `-1` is still the code for
`Aborted`.

### `cancel` now does something

Cancelling a call aborts `ctx.signal` in the tool, and the pending call rejects
on the client with `McpError` code `499`; the server sends no response for a
cancelled call. The signal is also aborted when the call times out on
the client and when the session ends. A tool that ignores `ctx.signal` still
runs to completion, but its result is discarded.

A call whose `AbortSignal` is already aborted is no longer sent to the server.

**What to change:** nothing is required; long-running tools should start
honouring `ctx.signal`.

### Error codes follow MCP

| Situation | Before | Now |
|---|---|---|
| Unknown tool, resource or prompt | `404` | `-32602` |
| Client lacks a capability the tool needs | `400` | `-32021` |
| Request timed out (raised by the client) | `-1` | `408` |
| Request cancelled (raised by the client) | — | `499` |
| Not connected (raised by the client) | — | `503` |

`ErrorCode` is exported with these values. An `McpError` thrown inside a tool
is now returned as an error instead of `isError` tool output; `ToolError` still
produces `isError` output.

When a resource, prompt, completion handler or middleware throws anything
other than an `McpError`, the client now receives a fixed message such as
`Resource handler for 'res://x' failed` with `-32603`; the exception text is
written to the server log. Tool arguments that are not a JSON object are
rejected with `-32602` before the tool runs.

**What to change:** checks against `404` / `400`, and callers that expected
`ctx.elicit()` on an unsupported client to come back as a tool result.

### Sampling and roots are deprecated

`ctx.sample()` and `ctx.listRoots()` are marked `@deprecated`, following MCP
2026-07-28 (removal no earlier than 2027-07-28).

### Smaller behaviour changes

- **Capabilities** reflect what is registered. A server with no resources or
  prompts used to claim both.
- **URI template variables are percent-decoded**, and `{?a,b}` query parameters
  are now extracted. A `load` function that decoded its arguments itself will
  now double-decode. The same encoded-slash rule as Python applies.
- **Closing a connection** rejects pending requests with `McpError(503, "Connection closed")`
  (was a plain `Error("cancelled")`); requests on a dead connection reject at
  once with `McpError(503)` and `isConnected` turns `false`.
- **Failing handlers are logged.** A notification handler that throws is
  written to `console.error` instead of surfacing as an unhandled rejection.
- **New direct dependency:** `@grpc/grpc-js` (it was already installed
  transitively through `nice-grpc`).

## Open questions before release

1. **Default bind address (Python).** The server still listens on all
   interfaces unless `host` is given. The TypeScript server and FastMCP default
   to `127.0.0.1`. Changing the default is one more breaking change (containers
   would need `--host 0.0.0.0`), so it is cheapest to do it in this release if
   it is going to be done at all.
2. **Tool annotation hints.** `destructive_hint` / `open_world_hint` are plain
   proto3 bools, so "not set" arrives as `false`, the opposite of the MCP
   defaults. Fixing it needs `optional` fields in the proto and regenerated
   stubs in both languages.
