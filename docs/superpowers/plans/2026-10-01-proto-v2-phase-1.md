# Proto v2, Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second, stateless gRPC service (`mcp.v2.Mcp`) served next to v1, with discovery, the list operations and completion, MCP error codes carried over gRPC statuses, and a client that can be told which protocol version to speak.

**Architecture:** A new proto file defines one unary RPC per operation; every request carries its own protocol version and client capabilities. Each language gets a v2 servicer that reuses the existing tool / resource / prompt managers, a small error-mapping module, and a v2 client transport selected by a `mode` option. v1 is not modified.

**Tech Stack:** Python 3.10+ (`grpcio` aio, `grpcio-tools`, `pytest`, `ruff`, `uv`); TypeScript (`nice-grpc`, `ts-proto` via `buf`, `vitest`).

**Spec:** `docs/superpowers/specs/2026-10-01-proto-v2-stateless-design.md` (read the "Amendment" note: JSON-shaped values are JSON strings, not `Struct`).

## Global Constraints

- `proto/mcp.proto` and the v1 generated stubs are not changed. Generate only `mcp_v2.proto`.
- Protocol version string: `2026-07-28`. It is the only supported version.
- A v2 request without `meta.protocol_version` or without `meta.client_capabilities` is rejected with MCP code `-32602`.
- An unsupported version is rejected with `-32022`, with the supported versions in the error data.
- Error mapping (MCP code → gRPC status): `-32602` → `INVALID_ARGUMENT`; `-32601` → `UNIMPLEMENTED`; `-32603` → `INTERNAL`; `-32021` and `-32022` → `FAILED_PRECONDITION`; anything else → `UNKNOWN`. The exact MCP code travels in trailing metadata `mcp-error-code`; structured data in `mcp-error-data-bin`.
- The client `mode` default stays `"legacy"` in this phase. v2 has no `CallTool`, `ReadResource` or `GetPrompt` yet; on a v2 connection those raise MCP `-32601`.
- Python: never bare `pip install`; use the project venv (`python/.venv`). Run `ruff format src tests` and `ruff check src tests` before each commit.
- Git: this repo needs `-c safe.directory=D:/Trabajo/mcp-grpc` on every git command. Commit messages carry no co-author or tool attribution lines.
- Commands below are written for PowerShell from the repo root `D:\Trabajo\mcp-grpc`.

## Review Focus

1. **`mode="auto"` against a server that is down** — must raise a connection error promptly, not decide "old server" and hang on the v1 stream. Test in Task 4 and Task 7.
2. **Auth on the new RPCs** — a server with `auth=` must reject v2 calls without a valid token, exactly like v1. Test in Task 3 and Task 6.
3. **A request with no `meta` at all** (e.g. from `grpcurl`) — must be `-32602`, not a crash or a default-version answer. Test in Task 3 and Task 6.
4. **A tool with no annotations** — the client must report MCP's defaults (`destructive` true, `open_world` true), not `false`. Test in Task 4 and Task 7.
5. **A garbage pagination cursor** on a v2 list — must behave like v1 (treated as offset 0), not error. Test in Task 3 and Task 6.

---

### Task 1: The v2 proto and generated stubs

**Files:**
- Create: `proto/mcp_v2.proto`
- Modify: `python/generate.py`
- Create (generated): `python/src/rapidmcp/_generated/mcp_v2_pb2.py`, `mcp_v2_pb2.pyi`, `mcp_v2_pb2_grpc.py`
- Create (generated): `typescript/generated/mcp_v2.ts`
- Test: `python/tests/test_v2_proto.py`, `typescript/tests/v2-proto.test.ts`

**Interfaces:**
- Produces (Python): `from rapidmcp._generated import mcp_v2_pb2, mcp_v2_pb2_grpc` with `mcp_v2_pb2_grpc.McpServicer`, `McpStub`, `add_McpServicer_to_server`.
- Produces (TypeScript): `import { McpDefinition, type McpServiceImplementation, type McpClient, ErrorData, CacheScope, ... } from "../generated/mcp_v2.js"`.

- [ ] **Step 1: Write the failing tests**

`python/tests/test_v2_proto.py`:

```python
"""The v2 stubs exist and describe the phase 1 service."""


def test_v2_service_has_the_phase_1_methods():
    from rapidmcp._generated import mcp_v2_pb2

    service = mcp_v2_pb2.DESCRIPTOR.services_by_name["Mcp"]

    assert service.full_name == "mcp.v2.Mcp"
    assert sorted(m.name for m in service.methods) == [
        "Complete",
        "Discover",
        "ListPrompts",
        "ListResourceTemplates",
        "ListResources",
        "ListTools",
    ]


def test_annotation_hints_can_be_left_unset():
    from rapidmcp._generated import mcp_v2_pb2

    unset = mcp_v2_pb2.ToolAnnotations(title="t")
    explicit = mcp_v2_pb2.ToolAnnotations(destructive_hint=False)

    assert not unset.HasField("destructive_hint")
    assert explicit.HasField("destructive_hint")
```

`typescript/tests/v2-proto.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { McpDefinition, ToolAnnotations } from "../generated/mcp_v2.js";

describe("v2 generated stubs", () => {
  it("describe the phase 1 service", () => {
    expect(McpDefinition.fullName).toBe("mcp.v2.Mcp");
    expect(Object.keys(McpDefinition.methods).sort()).toEqual([
      "complete",
      "discover",
      "listPrompts",
      "listResourceTemplates",
      "listResources",
      "listTools",
    ]);
  });

  it("let annotation hints be left unset", () => {
    const decoded = ToolAnnotations.decode(ToolAnnotations.encode({ title: "t" }).finish());
    expect(decoded.destructiveHint).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_proto.py -q
cd ..\typescript; npx vitest run tests/v2-proto.test.ts
```

Expected: both fail on the missing module (`cannot import name 'mcp_v2_pb2'`, `Failed to resolve import "../generated/mcp_v2.js"`).

- [ ] **Step 3: Write the proto**

`proto/mcp_v2.proto`:

```proto
syntax = "proto3";
package mcp.v2;

// RapidMCP protocol v2 — MCP 2026-07-28 semantics over gRPC.
// Stateless: every request carries its own RequestMeta; nothing is inferred
// from earlier requests on the same connection.
service Mcp {
  rpc Discover(DiscoverRequest) returns (DiscoverResult);
  rpc ListTools(ListToolsRequest) returns (ListToolsResult);
  rpc ListResources(ListResourcesRequest) returns (ListResourcesResult);
  rpc ListResourceTemplates(ListResourceTemplatesRequest) returns (ListResourceTemplatesResult);
  rpc ListPrompts(ListPromptsRequest) returns (ListPromptsResult);
  rpc Complete(CompleteRequest) returns (CompleteResult);
}

// ── Per-request / per-result metadata ─────────────────────────────────────

message Implementation {
  string name    = 1;
  string version = 2;
}

message ElicitationCapability {
  bool form = 1;
  bool url  = 2;
}

message ClientCapabilities {
  ElicitationCapability elicitation = 1;  // unset = not supported
  map<string, string> extensions = 15;    // extension id -> settings as JSON text
}

message RequestMeta {
  string protocol_version                = 1;  // required, e.g. "2026-07-28"
  ClientCapabilities client_capabilities = 2;  // required
  Implementation client_info             = 3;
  optional string log_level              = 4;  // opt in to log messages for this request
  optional string progress_token         = 5;  // opt in to progress for this request
}

message ResultMeta {
  Implementation server_info = 1;
}

enum CacheScope {
  CACHE_SCOPE_PRIVATE = 0;
  CACHE_SCOPE_PUBLIC  = 1;
}

message CacheHint {
  uint64 ttl_ms    = 1;  // 0 = immediately stale
  CacheScope scope = 2;
}

// Carried in the `mcp-error-data-bin` trailing metadata of a failed RPC.
message ErrorData {
  repeated string supported_versions    = 1;  // for -32022
  string requested_version              = 2;  // for -32022
  repeated string required_capabilities = 3;  // for -32021
}

// ── Discovery ─────────────────────────────────────────────────────────────

message ToolsCapability     { bool list_changed = 1; }
message ResourcesCapability { bool list_changed = 1; bool subscribe = 2; }
message PromptsCapability   { bool list_changed = 1; }

message ServerCapabilities {
  ToolsCapability tools         = 1;  // unset = no tools
  ResourcesCapability resources = 2;  // unset = no resources
  PromptsCapability prompts     = 3;  // unset = no prompts
  bool logging                  = 4;
  map<string, string> extensions = 15;
}

message DiscoverRequest { RequestMeta meta = 1; }

message DiscoverResult {
  ResultMeta meta                    = 1;
  repeated string supported_versions = 2;
  ServerCapabilities capabilities    = 3;
  string instructions                = 4;
  CacheHint cache                    = 5;
}

// ── Tools ─────────────────────────────────────────────────────────────────

message ToolAnnotations {
  string title                   = 1;
  optional bool read_only_hint   = 2;
  optional bool destructive_hint = 3;
  optional bool idempotent_hint  = 4;
  optional bool open_world_hint  = 5;
}

message Tool {
  string name                 = 1;
  string description          = 2;
  string input_schema         = 3;  // JSON Schema as JSON text
  string output_schema        = 4;  // JSON Schema as JSON text, empty = none
  ToolAnnotations annotations = 5;  // unset = no annotations
}

message ListToolsRequest { RequestMeta meta = 1; string cursor = 2; }

message ListToolsResult {
  ResultMeta meta     = 1;
  repeated Tool tools = 2;
  string next_cursor  = 3;
  CacheHint cache     = 4;
}

// ── Resources ─────────────────────────────────────────────────────────────

message Resource {
  string uri         = 1;
  string name        = 2;
  string description = 3;
  string mime_type   = 4;
}

message ListResourcesRequest { RequestMeta meta = 1; string cursor = 2; }

message ListResourcesResult {
  ResultMeta meta             = 1;
  repeated Resource resources = 2;
  string next_cursor          = 3;
  CacheHint cache             = 4;
}

message ResourceTemplate {
  string uri_template = 1;
  string name         = 2;
  string description  = 3;
  string mime_type    = 4;
}

message ListResourceTemplatesRequest { RequestMeta meta = 1; string cursor = 2; }

message ListResourceTemplatesResult {
  ResultMeta meta                     = 1;
  repeated ResourceTemplate templates = 2;
  string next_cursor                  = 3;
  CacheHint cache                     = 4;
}

// ── Prompts ───────────────────────────────────────────────────────────────

message PromptArgument {
  string name        = 1;
  string description = 2;
  bool required      = 3;
}

message Prompt {
  string name                       = 1;
  string description                = 2;
  repeated PromptArgument arguments = 3;
}

message ListPromptsRequest { RequestMeta meta = 1; string cursor = 2; }

message ListPromptsResult {
  ResultMeta meta         = 1;
  repeated Prompt prompts = 2;
  string next_cursor      = 3;
  CacheHint cache         = 4;
}

// ── Completion ────────────────────────────────────────────────────────────

message CompletionRef { string type = 1; string name = 2; }
message CompletionArg { string name = 1; string value = 2; }

message CompleteRequest {
  RequestMeta meta       = 1;
  CompletionRef ref      = 2;
  CompletionArg argument = 3;
}

message CompleteResult {
  ResultMeta meta        = 1;
  repeated string values = 2;
  bool has_more          = 3;
  int32 total            = 4;
}
```

- [ ] **Step 4: Teach `python/generate.py` about more than one proto**

Replace the whole file with:

```python
"""Generate Python gRPC stubs from the files in proto/.

Usage:
    python generate.py                 # every proto
    python generate.py mcp_v2.proto    # only the named ones
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROTO_DIR = ROOT / "proto"
OUT_DIR = Path(__file__).parent / "src" / "rapidmcp" / "_generated"
PROTO_FILES = ["mcp.proto", "mcp_v2.proto"]


def generate(proto_name: str) -> None:
    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO_DIR}",
        f"--python_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        f"--pyi_out={OUT_DIR}",
        str(PROTO_DIR / proto_name),
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    # grpc_tools generates a bare `import <stem>_pb2`, which breaks when the
    # file lives inside a package. Rewrite it to an absolute package import.
    stem = Path(proto_name).stem
    module = f"{stem}_pb2"
    alias = module.replace("_", "__")
    grpc_file = OUT_DIR / f"{module}_grpc.py"
    text = grpc_file.read_text()
    text = text.replace(
        f"import {module} as {alias}", f"from rapidmcp._generated import {module} as {alias}"
    )
    grpc_file.write_text(text)
    print(f"Fixed import in {grpc_file.name}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    init = OUT_DIR / "__init__.py"
    if not init.exists():
        init.write_text("")
    for name in sys.argv[1:] or PROTO_FILES:
        generate(name)
    print("Proto generation complete.")


if __name__ == "__main__":
    main()
```

(The old script blanked `_generated/__init__.py` on every run, wiping the `sys.path` shim that file contains. The new one leaves an existing `__init__.py` alone.)

- [ ] **Step 5: Generate both languages, v2 only**

```powershell
cd python; .\.venv\Scripts\python.exe generate.py mcp_v2.proto
cd ..\typescript; npm run generate -- --path ../proto/mcp_v2.proto
```

Expected: three new files under `python/src/rapidmcp/_generated/` and `typescript/generated/mcp_v2.ts`. Then confirm v1 is untouched:

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc status --short
```

Expected: only new (`??`) files plus `M python/generate.py`. If `mcp_pb2*` or `generated/mcp.ts` show as modified, restore them with `git checkout -- <file>`.

- [ ] **Step 6: Run the tests to verify they pass**

Same two commands as Step 2. Expected: PASS (2 tests each). Then the full suites and the build type check:

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
cd ..\typescript; npx vitest run; npx tsc -p tsconfig.build.json --noEmit
```

- [ ] **Step 7: Commit**

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add proto/mcp_v2.proto python/generate.py python/src/rapidmcp/_generated python/tests/test_v2_proto.py typescript/generated/mcp_v2.ts typescript/tests/v2-proto.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(proto): add the v2 service definition and generated stubs"
```

---

### Task 2: Python — MCP errors over gRPC statuses

**Files:**
- Modify: `python/src/rapidmcp/errors.py`
- Create: `python/src/rapidmcp/_v2_errors.py`
- Test: `python/tests/test_v2_errors.py`

**Interfaces:**
- Produces: `McpError(code, message, data=None)` with a `.data` attribute (`dict | None`); `rapidmcp.errors.UNSUPPORTED_PROTOCOL_VERSION = -32022`.
- Produces: `rapidmcp._v2_errors.status_for(code: int) -> grpc.StatusCode`, `trailers_for(error: McpError) -> tuple[tuple[str, str | bytes], ...]`, `async abort(context, error: McpError) -> NoReturn`, `error_from_rpc(status: grpc.StatusCode, details: str | None, trailers) -> McpError | None`.
- Error data keys: `"supported"` (list of str), `"requested"` (str), `"required_capabilities"` (list of str).

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_errors.py`:

```python
"""MCP errors survive a trip through a gRPC status and its trailing metadata."""

import grpc
import pytest

from rapidmcp._v2_errors import error_from_rpc, status_for, trailers_for
from rapidmcp.errors import McpError


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (-32602, grpc.StatusCode.INVALID_ARGUMENT),
        (-32601, grpc.StatusCode.UNIMPLEMENTED),
        (-32603, grpc.StatusCode.INTERNAL),
        (-32021, grpc.StatusCode.FAILED_PRECONDITION),
        (-32022, grpc.StatusCode.FAILED_PRECONDITION),
        (1234, grpc.StatusCode.UNKNOWN),
    ],
)
def test_status_for(code, status):
    assert status_for(code) is status


def test_error_round_trips_with_its_exact_code_and_data():
    sent = McpError(
        -32022,
        "Unsupported protocol version",
        data={"supported": ["2026-07-28"], "requested": "1900-01-01"},
    )

    received = error_from_rpc(status_for(sent.code), sent.message, trailers_for(sent))

    assert (received.code, received.message) == (-32022, "Unsupported protocol version")
    assert received.data == {"supported": ["2026-07-28"], "requested": "1900-01-01"}


def test_error_without_data_round_trips_with_no_data():
    received = error_from_rpc(
        grpc.StatusCode.INVALID_ARGUMENT, "bad", trailers_for(McpError(-32602, "bad"))
    )

    assert (received.code, received.data) == (-32602, None)


def test_transport_failures_become_local_client_errors():
    timeout = error_from_rpc(grpc.StatusCode.DEADLINE_EXCEEDED, "Deadline Exceeded", ())
    down = error_from_rpc(grpc.StatusCode.UNAVAILABLE, "failed to connect", ())

    assert (timeout.code, down.code) == (408, 503)


def test_other_grpc_failures_are_not_mcp_errors():
    assert error_from_rpc(grpc.StatusCode.UNAUTHENTICATED, "Invalid token", ()) is None
    assert error_from_rpc(grpc.StatusCode.UNIMPLEMENTED, "Method not found!", None) is None
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_errors.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'rapidmcp._v2_errors'`.

- [ ] **Step 3: Implement**

In `python/src/rapidmcp/errors.py`, add the constant next to `MISSING_CLIENT_CAPABILITY` and give `McpError` its data:

```python
MISSING_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022
```

```python
class McpError(Exception):
    """Application-level error from the MCP protocol."""

    def __init__(self, code: int, message: str, data: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
```

`python/src/rapidmcp/_v2_errors.py`:

```python
"""Carry MCP errors over gRPC: a status tooling understands, plus the exact
MCP code (and any structured data) in trailing metadata."""

from __future__ import annotations

from typing import NoReturn

import grpc

from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    MISSING_CLIENT_CAPABILITY,
    NOT_CONNECTED,
    REQUEST_TIMEOUT,
    UNSUPPORTED_PROTOCOL_VERSION,
    McpError,
)

CODE_KEY = "mcp-error-code"
DATA_KEY = "mcp-error-data-bin"

_STATUS = {
    INVALID_PARAMS: grpc.StatusCode.INVALID_ARGUMENT,
    METHOD_NOT_FOUND: grpc.StatusCode.UNIMPLEMENTED,
    INTERNAL_ERROR: grpc.StatusCode.INTERNAL,
    MISSING_CLIENT_CAPABILITY: grpc.StatusCode.FAILED_PRECONDITION,
    UNSUPPORTED_PROTOCOL_VERSION: grpc.StatusCode.FAILED_PRECONDITION,
}


def status_for(code: int) -> grpc.StatusCode:
    return _STATUS.get(code, grpc.StatusCode.UNKNOWN)


def trailers_for(error: McpError) -> tuple[tuple[str, str | bytes], ...]:
    trailers: list[tuple[str, str | bytes]] = [(CODE_KEY, str(error.code))]
    if error.data:
        data = pb.ErrorData(
            supported_versions=error.data.get("supported", []),
            requested_version=error.data.get("requested", ""),
            required_capabilities=error.data.get("required_capabilities", []),
        )
        trailers.append((DATA_KEY, data.SerializeToString()))
    return tuple(trailers)


async def abort(context, error: McpError) -> NoReturn:
    """End the RPC with *error*. Never returns: ``context.abort`` raises."""
    context.set_trailing_metadata(trailers_for(error))
    await context.abort(status_for(error.code), error.message)
    raise AssertionError("context.abort returned")  # pragma: no cover


def _data_from(raw: bytes) -> dict | None:
    parsed = pb.ErrorData.FromString(raw)
    data: dict = {}
    if parsed.supported_versions:
        data["supported"] = list(parsed.supported_versions)
    if parsed.requested_version:
        data["requested"] = parsed.requested_version
    if parsed.required_capabilities:
        data["required_capabilities"] = list(parsed.required_capabilities)
    return data or None


def error_from_rpc(status: grpc.StatusCode, details: str | None, trailers) -> McpError | None:
    """The McpError a failed RPC stands for, or None when it is not one.

    None means "re-raise the gRPC error as it is" — an auth failure, or an
    UNIMPLEMENTED from a server that does not serve v2 at all.
    """
    found = dict(trailers or ())
    if CODE_KEY in found:
        raw = found.get(DATA_KEY)
        return McpError(int(found[CODE_KEY]), details or "", data=_data_from(raw) if raw else None)
    if status is grpc.StatusCode.DEADLINE_EXCEEDED:
        return McpError(REQUEST_TIMEOUT, details or "Request timed out")
    if status is grpc.StatusCode.UNAVAILABLE:
        return McpError(NOT_CONNECTED, details or "Server unavailable")
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_errors.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
```

Expected: 10 new tests pass; the full suite stays green.

- [ ] **Step 5: Commit**

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp/errors.py python/src/rapidmcp/_v2_errors.py python/tests/test_v2_errors.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): map MCP errors onto gRPC statuses for the v2 protocol"
```

---

### Task 3: Python — the v2 servicer, served next to v1

**Files:**
- Create: `python/src/rapidmcp/_v2_servicer.py`
- Modify: `python/src/rapidmcp/server.py` (`_start_grpc`, inside `bind`)
- Test: `python/tests/test_v2_server.py`

**Interfaces:**
- Consumes: `rapidmcp._v2_errors.abort`, `McpError(code, message, data)`, `mcp_v2_pb2`, `mcp_v2_pb2_grpc`, and from the server: `server.name`, `server.version`, `server.page_size`, `server._tools`, `server._resources`, `server._resource_templates`, `server._prompts`, `server._completions`.
- Produces: `rapidmcp._v2_servicer._McpV2Servicer(server)`, `rapidmcp._v2_servicer.SUPPORTED_VERSIONS: tuple[str, ...] = ("2026-07-28",)`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_server.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_server.py -q
```

Expected: every v2 test fails with gRPC `UNIMPLEMENTED` ("Method not found!"); `test_v1_clients_still_work_on_the_same_port` passes.

- [ ] **Step 3: Write the servicer**

`python/src/rapidmcp/_v2_servicer.py`:

```python
"""v2 servicer — MCP 2026-07-28 semantics, one RPC per operation.

Stateless by construction: every handler reads what it needs from the
request's own ``meta`` and from the server's registries, never from anything
an earlier request left behind.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc
from rapidmcp._utils import _invoke, _paginate
from rapidmcp._v2_errors import abort
from rapidmcp.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    UNSUPPORTED_PROTOCOL_VERSION,
    McpError,
)

if TYPE_CHECKING:
    from rapidmcp.server import RapidMCP

logger = logging.getLogger("rapidmcp.server")

SUPPORTED_VERSIONS: tuple[str, ...] = ("2026-07-28",)


class _McpV2Servicer(mcp_v2_pb2_grpc.McpServicer):
    def __init__(self, server: RapidMCP) -> None:
        self._server = server

    # ── shared pieces ────────────────────────────────────────────────────

    def _result_meta(self) -> pb.ResultMeta:
        return pb.ResultMeta(
            server_info=pb.Implementation(name=self._server.name, version=self._server.version)
        )

    @staticmethod
    def _no_cache() -> pb.CacheHint:
        return pb.CacheHint(ttl_ms=0, scope=pb.CACHE_SCOPE_PRIVATE)

    async def _check_meta(self, request, context) -> None:
        """Reject a request whose metadata is missing or names a version we do not serve."""
        meta = request.meta
        if not meta.protocol_version or not meta.HasField("client_capabilities"):
            await abort(
                context,
                McpError(
                    INVALID_PARAMS,
                    "Request meta must carry protocol_version and client_capabilities",
                ),
            )
        if meta.protocol_version not in SUPPORTED_VERSIONS:
            await abort(
                context,
                McpError(
                    UNSUPPORTED_PROTOCOL_VERSION,
                    "Unsupported protocol version",
                    data={
                        "supported": list(SUPPORTED_VERSIONS),
                        "requested": meta.protocol_version,
                    },
                ),
            )

    # ── RPCs ─────────────────────────────────────────────────────────────

    async def Discover(self, request, context):
        await self._check_meta(request, context)
        server = self._server
        capabilities = pb.ServerCapabilities()
        if server._tools:
            capabilities.tools.CopyFrom(pb.ToolsCapability(list_changed=True))
        if server._resources or server._resource_templates:
            capabilities.resources.CopyFrom(
                pb.ResourcesCapability(list_changed=True, subscribe=True)
            )
        if server._prompts:
            capabilities.prompts.CopyFrom(pb.PromptsCapability(list_changed=True))
        return pb.DiscoverResult(
            meta=self._result_meta(),
            supported_versions=SUPPORTED_VERSIONS,
            capabilities=capabilities,
            cache=self._no_cache(),
        )

    async def ListTools(self, request, context):
        await self._check_meta(request, context)
        tools = []
        for t in self._server._tools.values():
            tool = pb.Tool(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
                output_schema=t.output_schema,
            )
            if t.annotations:
                tool.annotations.CopyFrom(
                    pb.ToolAnnotations(
                        title=t.annotations.title,
                        read_only_hint=t.annotations.read_only_hint,
                        destructive_hint=t.annotations.destructive_hint,
                        idempotent_hint=t.annotations.idempotent_hint,
                        open_world_hint=t.annotations.open_world_hint,
                    )
                )
            tools.append(tool)
        page, next_cursor = _paginate(tools, request.cursor, self._server.page_size)
        return pb.ListToolsResult(
            meta=self._result_meta(), tools=page, next_cursor=next_cursor, cache=self._no_cache()
        )

    async def ListResources(self, request, context):
        await self._check_meta(request, context)
        resources = [
            pb.Resource(uri=r.uri, name=r.name, description=r.description, mime_type=r.mime_type)
            for r in self._server._resources.values()
        ]
        page, next_cursor = _paginate(resources, request.cursor, self._server.page_size)
        return pb.ListResourcesResult(
            meta=self._result_meta(),
            resources=page,
            next_cursor=next_cursor,
            cache=self._no_cache(),
        )

    async def ListResourceTemplates(self, request, context):
        await self._check_meta(request, context)
        templates = [
            pb.ResourceTemplate(
                uri_template=t.uri_template,
                name=t.name,
                description=t.description,
                mime_type=t.mime_type,
            )
            for t in self._server._resource_templates.values()
        ]
        page, next_cursor = _paginate(templates, request.cursor, self._server.page_size)
        return pb.ListResourceTemplatesResult(
            meta=self._result_meta(),
            templates=page,
            next_cursor=next_cursor,
            cache=self._no_cache(),
        )

    async def ListPrompts(self, request, context):
        await self._check_meta(request, context)
        prompts = [
            pb.Prompt(
                name=p.name,
                description=p.description,
                arguments=[pb.PromptArgument(**a) for a in p.arguments],
            )
            for p in self._server._prompts.values()
        ]
        page, next_cursor = _paginate(prompts, request.cursor, self._server.page_size)
        return pb.ListPromptsResult(
            meta=self._result_meta(), prompts=page, next_cursor=next_cursor, cache=self._no_cache()
        )

    async def Complete(self, request, context):
        await self._check_meta(request, context)
        completion = self._server._completions.get(request.ref.name)
        if not completion:
            return pb.CompleteResult(meta=self._result_meta())
        try:
            values = await _invoke(
                completion.handler, request.argument.name, request.argument.value
            )
        except Exception:
            logger.exception("Completion handler for '%s' raised", request.ref.name)
            await abort(
                context,
                McpError(INTERNAL_ERROR, f"Completion handler for '{request.ref.name}' failed"),
            )
        return pb.CompleteResult(meta=self._result_meta(), values=values, total=len(values))
```

- [ ] **Step 4: Register it next to v1**

In `python/src/rapidmcp/server.py`, add to the imports:

```python
from rapidmcp._generated import mcp_pb2, mcp_pb2_grpc, mcp_v2_pb2_grpc
from rapidmcp._v2_servicer import _McpV2Servicer
```

(the first line replaces the existing `from rapidmcp._generated import mcp_pb2, mcp_pb2_grpc`), and inside `_start_grpc`'s `bind`, directly after the existing `add_McpServicer_to_server` line:

```python
            mcp_pb2_grpc.add_McpServicer_to_server(_McpServicer(self), grpc_server)
            mcp_v2_pb2_grpc.add_McpServicer_to_server(_McpV2Servicer(self), grpc_server)
```

- [ ] **Step 5: Run the tests to verify they pass**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_server.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
```

Expected: 12 tests pass in the new file; the full suite stays green.

- [ ] **Step 6: Commit**

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp/_v2_servicer.py python/src/rapidmcp/server.py python/tests/test_v2_server.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): serve the stateless v2 service next to the v1 stream"
```

---

### Task 4: Python — client `mode`

**Files:**
- Create: `python/src/rapidmcp/_version.py`
- Modify: `python/src/rapidmcp/__init__.py` (take `__version__` from `_version`)
- Create: `python/src/rapidmcp/_v2_client.py`
- Modify: `python/src/rapidmcp/types.py` (add `_convert_tool_v2`)
- Modify: `python/src/rapidmcp/client.py`
- Modify: `CHANGELOG.md` (Python `[Unreleased]` → `### Added`)
- Test: `python/tests/test_v2_client.py`

**Interfaces:**
- Consumes: `rapidmcp._v2_errors.error_from_rpc`, `mcp_v2_pb2`, `mcp_v2_pb2_grpc.McpStub`, `rapidmcp._v2_servicer.SUPPORTED_VERSIONS`.
- Produces: `Client(target, token=None, tls=None, request_timeout=30.0, mode="legacy")` where `mode` is `"legacy" | "modern" | "auto"`; `client.protocol` → `"v1" | "v2" | None`.
- Produces: `rapidmcp._v2_client._V2Transport(channel, metadata, timeout, elicitation)` with async `discover()`, `list_tools(cursor)`, `list_resources(cursor)`, `list_resource_templates(cursor)`, `list_prompts(cursor)`, `complete(ref_type, ref_name, argument_name, value)`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_client.py`:

```python
"""Client(mode=...) chooses which protocol version it speaks."""

import asyncio

import pytest
from grpc import aio

from rapidmcp import Client, RapidMCP
from rapidmcp._generated import mcp_pb2_grpc
from rapidmcp._servicer import _McpServicer
from rapidmcp.errors import McpError


def _server(**kwargs) -> RapidMCP:
    srv = RapidMCP(name="dual", version="9.9", **kwargs)

    @srv.tool(description="Echo", read_only=True)
    async def echo(text: str) -> str:
        return text

    @srv.tool()
    async def plain() -> str:
        return "x"

    @srv.resource("res://a")
    async def a() -> str:
        return "a"

    @srv.resource_template("res://items/{item_id}")
    async def item(item_id: str) -> str:
        return item_id

    @srv.prompt()
    async def greet(name: str) -> str:
        return f"hi {name}"

    @srv.completion("greet")
    async def complete_greet(argument_name: str, value: str) -> list[str]:
        return [f"{value}lice"]

    return srv


async def test_modern_client_discovers_and_lists_over_v2():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        prompts = await client.list_prompts()
        completion = await client.complete("ref/prompt", "greet", "name", "A")

        assert client.protocol == "v2"
        assert (client.server_info.server_name, client.server_info.server_version) == (
            "dual",
            "9.9",
        )
        assert client.server_info.capabilities.tools
        assert sorted(t.name for t in tools.items) == ["echo", "plain"]
        assert tools.items[0].input_schema["properties"] == {"text": {"type": "string"}}
        assert [r.uri for r in resources.items] == ["res://a"]
        assert [t.uri_template for t in templates.items] == ["res://items/{item_id}"]
        assert [p.name for p in prompts.items] == ["greet"]
        assert completion.values == ["Alice"]
        assert await client.ping()


async def test_modern_client_applies_mcp_defaults_to_unset_annotation_hints():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        tools = {t.name: t for t in (await client.list_tools()).items}

    # No annotations at all: MCP's defaults.
    assert tools["plain"].annotations.destructive_hint is True
    assert tools["plain"].annotations.open_world_hint is True
    assert tools["plain"].annotations.read_only_hint is False
    # Annotated: exactly what the server said.
    assert tools["echo"].annotations.read_only_hint is True
    assert tools["echo"].annotations.destructive_hint is False


async def test_modern_client_follows_pagination_cursors():
    srv = _server(page_size=1)
    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        first = await client.list_tools()
        second = await client.list_tools(cursor=first.next_cursor)

    assert [t.name for t in first.items] == ["echo"]
    assert [t.name for t in second.items] == ["plain"]
    assert second.next_cursor is None


async def test_modern_client_says_which_operations_v2_lacks():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}", mode="modern") as client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("echo", {"text": "x"})

    assert exc.value.code == -32601
    assert "legacy" in exc.value.message


async def test_modern_client_sends_its_token():
    srv = _server(auth=lambda token: token == "s3cret")
    async with srv:
        async with Client(f"localhost:{srv.port}", token="s3cret", mode="modern") as client:
            assert [t.name for t in (await client.list_prompts()).items] == ["greet"]

        with pytest.raises(aio.AioRpcError):
            async with Client(f"localhost:{srv.port}", token="nope", mode="modern"):
                pass


async def test_auto_picks_v2_when_the_server_offers_it():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}", mode="auto") as client:
        assert client.protocol == "v2"


async def test_auto_falls_back_to_v1_against_a_server_without_v2():
    srv = _server()
    grpc_server = aio.server()
    mcp_pb2_grpc.add_McpServicer_to_server(_McpServicer(srv), grpc_server)  # v1 only
    port = grpc_server.add_insecure_port("127.0.0.1:0")
    await grpc_server.start()
    try:
        async with Client(f"127.0.0.1:{port}", mode="auto") as client:
            result = await client.call_tool("echo", {"text": "old server"})
            assert client.protocol == "v1"
            assert result.content[0].text == "old server"
    finally:
        await grpc_server.stop(0)


async def test_auto_fails_promptly_when_nothing_is_listening():
    client = Client("127.0.0.1:1", mode="auto", request_timeout=2)

    with pytest.raises(McpError) as exc:
        await asyncio.wait_for(client.connect(), timeout=10)

    assert exc.value.code in (408, 503)  # refused at once, or no answer within the deadline
    assert not client.is_connected


async def test_legacy_stays_the_default():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}") as client:
        assert client.protocol == "v1"


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="mode"):
        Client("localhost:1", mode="newest")
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_client.py -q
```

Expected: FAIL — `TypeError: Client.__init__() got an unexpected keyword argument 'mode'` (and `AttributeError: ... 'protocol'` for the default-mode test).

- [ ] **Step 3: Move the version lookup out of the package root**

The v2 transport reports the library version in `client_info`, and `rapidmcp/__init__.py` imports `client.py`, so the version must live below both.

`python/src/rapidmcp/_version.py`:

```python
"""The installed package version, importable without importing the package root."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("rapidmcp")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
```

In `python/src/rapidmcp/__init__.py`, replace the `importlib.metadata` block (the two imports and the `try/except` that sets `__version__`) with:

```python
from rapidmcp._version import __version__
```

- [ ] **Step 4: Add the v2 tool converter**

Append to `python/src/rapidmcp/types.py`:

```python
def _convert_tool_v2(p) -> Tool:
    """Like ``_convert_tool`` for a v2 ``Tool``, whose hints may be unset.

    An unset hint takes MCP's default: not read-only, destructive,
    not idempotent, open-world.
    """
    a = p.annotations

    def hint(name: str, default: bool) -> bool:
        return getattr(a, name) if a.HasField(name) else default

    return Tool(
        name=p.name,
        description=p.description,
        input_schema=json.loads(p.input_schema) if p.input_schema else {},
        output_schema=json.loads(p.output_schema) if p.output_schema else None,
        annotations=ToolAnnotationInfo(
            title=a.title,
            read_only_hint=hint("read_only_hint", False),
            destructive_hint=hint("destructive_hint", True),
            idempotent_hint=hint("idempotent_hint", False),
            open_world_hint=hint("open_world_hint", True),
        ),
    )
```

- [ ] **Step 5: Write the v2 transport**

`python/src/rapidmcp/_v2_client.py`:

```python
"""Client transport for the v2 protocol: one stateless RPC per operation."""

from __future__ import annotations

from collections.abc import Callable

import grpc
from grpc import aio

from rapidmcp._generated import mcp_pb2
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc
from rapidmcp._v2_errors import error_from_rpc
from rapidmcp._version import __version__
from rapidmcp.types import (
    CompleteResult,
    ListResult,
    ServerInfo,
    _convert_complete_result,
    _convert_prompt,
    _convert_resource,
    _convert_resource_template,
    _convert_tool_v2,
)

PROTOCOL_VERSION = "2026-07-28"


class _V2Transport:
    def __init__(
        self,
        channel: aio.Channel,
        metadata: list[tuple[str, str]],
        timeout: float,
        supports_elicitation: Callable[[], bool],
    ) -> None:
        self._stub = mcp_v2_pb2_grpc.McpStub(channel)
        self._metadata = metadata
        self._timeout = timeout
        self._supports_elicitation = supports_elicitation

    def _meta(self) -> pb.RequestMeta:
        capabilities = pb.ClientCapabilities()
        if self._supports_elicitation():
            capabilities.elicitation.CopyFrom(pb.ElicitationCapability(form=True))
        return pb.RequestMeta(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=capabilities,
            client_info=pb.Implementation(name="rapidmcp-python", version=__version__),
        )

    async def _call(self, method, request):
        try:
            return await method(request, metadata=self._metadata, timeout=self._timeout)
        except aio.AioRpcError as exc:
            error = error_from_rpc(exc.code(), exc.details(), exc.trailing_metadata())
            if error is None:
                raise
            raise error from None

    async def discover(self) -> ServerInfo:
        result = await self._call(self._stub.Discover, pb.DiscoverRequest(meta=self._meta()))
        caps = result.capabilities
        return ServerInfo(
            server_name=result.meta.server_info.name,
            server_version=result.meta.server_info.version,
            # Same shape as the v1 capabilities, so callers need not care which era this is.
            capabilities=mcp_pb2.ServerCapabilities(
                tools=caps.HasField("tools"),
                tools_list_changed=caps.tools.list_changed,
                resources=caps.HasField("resources"),
                prompts=caps.HasField("prompts"),
            ),
        )

    async def list_tools(self, cursor: str | None) -> ListResult:
        result = await self._call(
            self._stub.ListTools, pb.ListToolsRequest(meta=self._meta(), cursor=cursor or "")
        )
        return ListResult(
            items=[_convert_tool_v2(t) for t in result.tools],
            next_cursor=result.next_cursor or None,
        )

    async def list_resources(self, cursor: str | None) -> ListResult:
        result = await self._call(
            self._stub.ListResources,
            pb.ListResourcesRequest(meta=self._meta(), cursor=cursor or ""),
        )
        return ListResult(
            items=[_convert_resource(r) for r in result.resources],
            next_cursor=result.next_cursor or None,
        )

    async def list_resource_templates(self, cursor: str | None) -> ListResult:
        result = await self._call(
            self._stub.ListResourceTemplates,
            pb.ListResourceTemplatesRequest(meta=self._meta(), cursor=cursor or ""),
        )
        return ListResult(
            items=[_convert_resource_template(t) for t in result.templates],
            next_cursor=result.next_cursor or None,
        )

    async def list_prompts(self, cursor: str | None) -> ListResult:
        result = await self._call(
            self._stub.ListPrompts, pb.ListPromptsRequest(meta=self._meta(), cursor=cursor or "")
        )
        return ListResult(
            items=[_convert_prompt(p) for p in result.prompts],
            next_cursor=result.next_cursor or None,
        )

    async def complete(
        self, ref_type: str, ref_name: str, argument_name: str, value: str
    ) -> CompleteResult:
        result = await self._call(
            self._stub.Complete,
            pb.CompleteRequest(
                meta=self._meta(),
                ref=pb.CompletionRef(type=ref_type, name=ref_name),
                argument=pb.CompletionArg(name=argument_name, value=value),
            ),
        )
        return _convert_complete_result(result)


def is_v2_missing(exc: BaseException) -> bool:
    """True when a failed Discover means "this server does not serve v2"."""
    return isinstance(exc, aio.AioRpcError) and exc.code() is grpc.StatusCode.UNIMPLEMENTED
```

- [ ] **Step 6: Wire `mode` into `Client`**

All edits are in `python/src/rapidmcp/client.py`.

Imports — add:

```python
from rapidmcp._v2_client import _V2Transport, is_v2_missing
from rapidmcp.errors import METHOD_NOT_FOUND
```

(merge `METHOD_NOT_FOUND` into the existing `from rapidmcp.errors import ...` line).

Constructor — add the parameter and two attributes:

```python
    def __init__(
        self,
        target: str,
        token: str | None = None,
        tls: ClientTLSConfig | None = None,
        request_timeout: float = 30.0,
        mode: Literal["legacy", "modern", "auto"] = "legacy",
    ) -> None:
        if mode not in ("legacy", "modern", "auto"):
            raise ValueError(f"mode must be 'legacy', 'modern' or 'auto', not {mode!r}")
        self._mode = mode
        self._v2: _V2Transport | None = None
```

followed by the existing body unchanged.

Add the property next to `is_connected`, and make `is_connected` era-aware:

```python
    @property
    def protocol(self) -> str | None:
        """``"v2"`` or ``"v1"`` once connected, ``None`` before."""
        if self._v2 is not None:
            return "v2"
        return "v1" if self._reader_task is not None else None

    @property
    def is_connected(self) -> bool:
        """True when a live gRPC channel (and, on v1, its reader loop) is active."""
        if self._v2 is not None:
            return self._channel is not None
        return (
            self._channel is not None
            and self._reader_task is not None
            and not self._reader_task.done()
        )
```

Replace `connect()` with:

```python
    async def connect(self) -> None:
        logger.debug("connecting to %s (mode=%s)", self._target, self._mode)
        if self._tls:
            self._channel = grpc_aio.secure_channel(
                self._target, _build_channel_credentials(self._tls)
            )
        else:
            self._channel = grpc_aio.insecure_channel(self._target)
        try:
            if self._mode != "legacy" and await self._connect_v2():
                return
            await self._connect_v1()
        except BaseException:
            await self.close()  # don't leave the channel and reader task behind
            raise

    async def _connect_v2(self) -> bool:
        """Try the v2 service. False means "old server, use v1" (auto mode only)."""
        transport = _V2Transport(
            self._channel,
            self._metadata,
            self._request_timeout,
            supports_elicitation=lambda: self._elicitation_handler is not None,
        )
        try:
            self.server_info = await transport.discover()
        except grpc_aio.AioRpcError as exc:
            if self._mode == "auto" and is_v2_missing(exc):
                logger.debug("%s does not serve v2; falling back to v1", self._target)
                return False
            raise
        self._v2 = transport
        return True

    async def _connect_v1(self) -> None:
        stub = mcp_pb2_grpc.McpStub(self._channel)
        self._write_queue = asyncio.Queue()
        self._stream = stub.Session(self._outbound_iter(), metadata=self._metadata)
        self._reader_task = asyncio.create_task(self._reader_loop())
        await self._initialize()
        logger.debug(
            "connected to %s  server=%s %s",
            self._target,
            self.server_info.server_name if self.server_info else "?",
            self.server_info.server_version if self.server_info else "?",
        )
```

Add a helper below `_request`:

```python
    def _v1_only(self, operation: str) -> None:
        """Raise for operations the v2 protocol does not carry yet."""
        if self._v2 is not None:
            raise McpError(
                METHOD_NOT_FOUND,
                f"{operation} is not available on the v2 protocol yet; use mode='legacy'",
            )
```

Route the public methods. At the top of each of `list_tools`, `list_resources`, `list_prompts`, `list_resource_templates`:

```python
        if self._v2 is not None:
            return await self._v2.list_tools(cursor)
```

(with `list_resources` / `list_prompts` / `list_resource_templates` respectively). At the top of `complete`:

```python
        if self._v2 is not None:
            return await self._v2.complete(ref_type, ref_name, argument_name, value)
```

At the top of `ping`:

```python
        if self._v2 is not None:
            await self._v2.discover()
            return True
```

And as the first line of `call_tool`, `read_resource`, `get_prompt`, `subscribe_resource`, `cancel` and `notify_roots_list_changed`:

```python
        self._v1_only("call_tool")
```

(with each method's own name as the string).

In `close()`, add as the last statement before `self._ref_count = 0`:

```python
        self._v2 = None
```

- [ ] **Step 7: Run the tests to verify they pass**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_client.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
```

Expected: 10 tests pass in the new file; the full suite stays green.

- [ ] **Step 8: Changelog and commit**

Under the Python `### [Unreleased]` → `### Added` list in `CHANGELOG.md`, add:

```markdown
- **Protocol v2 (experimental, phase 1):** servers also answer the stateless `mcp.v2.Mcp` service — `Discover`, the list operations and `Complete` — following MCP 2026-07-28. `Client(mode="modern")` speaks it; `mode="auto"` tries it and falls back to v1; the default stays `"legacy"`. Tool calls, resource reads and prompts are not on v2 yet
```

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp python/tests/test_v2_client.py CHANGELOG.md
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): client mode to speak the v2 protocol, with fallback to v1"
```

---

### Task 5: TypeScript — MCP errors over gRPC statuses

**Files:**
- Modify: `typescript/src/errors.ts`
- Create: `typescript/src/v2/errors.ts`
- Test: `typescript/tests/v2-errors.test.ts`

**Interfaces:**
- Produces: `McpError` gains `readonly data: McpErrorData | null` and a third constructor argument; `ErrorCode.UnsupportedProtocolVersion = -32022`; `export interface McpErrorData { supported?: string[]; requested?: string; requiredCapabilities?: string[] }`.
- Produces (`src/v2/errors.ts`): `statusFor(code: number): Status`, `setErrorTrailers(trailer: Metadata, error: McpError): void`, `toServerError(error: McpError, trailer: Metadata): ServerError`, `errorFromRpc(status: Status, details: string, trailer: Metadata | null): McpError | null`.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-errors.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { Metadata, Status } from "nice-grpc-common";
import { McpError } from "../src/errors.js";
import { errorFromRpc, setErrorTrailers, statusFor } from "../src/v2/errors.js";

describe("MCP errors over gRPC statuses", () => {
  it.each([
    [-32602, Status.INVALID_ARGUMENT],
    [-32601, Status.UNIMPLEMENTED],
    [-32603, Status.INTERNAL],
    [-32021, Status.FAILED_PRECONDITION],
    [-32022, Status.FAILED_PRECONDITION],
    [1234, Status.UNKNOWN],
  ])("maps MCP code %i to its gRPC status", (code, status) => {
    expect(statusFor(code)).toBe(status);
  });

  it("round-trips an error with its exact code and data", () => {
    const sent = new McpError(-32022, "Unsupported protocol version", {
      supported: ["2026-07-28"],
      requested: "1900-01-01",
    });
    const trailer = new Metadata();
    setErrorTrailers(trailer, sent);

    const received = errorFromRpc(statusFor(sent.code), sent.message, trailer);

    expect(received?.code).toBe(-32022);
    expect(received?.message).toBe("Unsupported protocol version");
    expect(received?.data).toEqual({ supported: ["2026-07-28"], requested: "1900-01-01" });
  });

  it("round-trips an error without data as null data", () => {
    const trailer = new Metadata();
    setErrorTrailers(trailer, new McpError(-32602, "bad"));

    expect(errorFromRpc(Status.INVALID_ARGUMENT, "bad", trailer)?.data).toBeNull();
  });

  it("turns transport failures into local client errors", () => {
    expect(errorFromRpc(Status.DEADLINE_EXCEEDED, "deadline", null)?.code).toBe(408);
    expect(errorFromRpc(Status.UNAVAILABLE, "no connection", null)?.code).toBe(503);
  });

  it("leaves other gRPC failures alone", () => {
    expect(errorFromRpc(Status.UNAUTHENTICATED, "Invalid token", null)).toBeNull();
    expect(errorFromRpc(Status.UNIMPLEMENTED, "no such method", new Metadata())).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-errors.test.ts
```

Expected: FAIL — `Failed to resolve import "../src/v2/errors.js"`.

- [ ] **Step 3: Implement**

In `typescript/src/errors.ts`, add `UnsupportedProtocolVersion: -32022,` after `MissingClientCapability` in `ErrorCode`, and replace the `McpError` class with:

```typescript
/** Structured detail some errors carry. */
export interface McpErrorData {
  /** -32022: versions the server does serve. */
  supported?: string[];
  /** -32022: the version the request asked for. */
  requested?: string;
  /** -32021: capabilities the client would have needed. */
  requiredCapabilities?: string[];
}

export class McpError extends Error {
  public readonly code: number;
  public readonly data: McpErrorData | null;

  constructor(code: number, message: string, data: McpErrorData | null = null) {
    super(message);
    this.name = "McpError";
    this.code = code;
    this.data = data;
  }
}
```

`typescript/src/v2/errors.ts`:

```typescript
/**
 * Carry MCP errors over gRPC: a status tooling understands, plus the exact
 * MCP code (and any structured data) in trailing metadata.
 */
import { Metadata, ServerError, Status } from "nice-grpc-common";
import { ErrorData } from "../../generated/mcp_v2.js";
import { ErrorCode, McpError, type McpErrorData } from "../errors.js";

const CODE_KEY = "mcp-error-code";
const DATA_KEY = "mcp-error-data-bin";

const STATUS = new Map<number, Status>([
  [ErrorCode.InvalidParams, Status.INVALID_ARGUMENT],
  [ErrorCode.MethodNotFound, Status.UNIMPLEMENTED],
  [ErrorCode.InternalError, Status.INTERNAL],
  [ErrorCode.MissingClientCapability, Status.FAILED_PRECONDITION],
  [ErrorCode.UnsupportedProtocolVersion, Status.FAILED_PRECONDITION],
]);

export function statusFor(code: number): Status {
  return STATUS.get(code) ?? Status.UNKNOWN;
}

export function setErrorTrailers(trailer: Metadata, error: McpError): void {
  trailer.set(CODE_KEY, String(error.code));
  if (error.data) {
    trailer.set(
      DATA_KEY,
      ErrorData.encode({
        supportedVersions: error.data.supported ?? [],
        requestedVersion: error.data.requested ?? "",
        requiredCapabilities: error.data.requiredCapabilities ?? [],
      }).finish(),
    );
  }
}

/** The error to throw from a v2 handler; also fills the call's trailer. */
export function toServerError(error: McpError, trailer: Metadata): ServerError {
  setErrorTrailers(trailer, error);
  return new ServerError(statusFor(error.code), error.message);
}

function dataFrom(raw: Uint8Array): McpErrorData | null {
  const parsed = ErrorData.decode(raw);
  const data: McpErrorData = {};
  if (parsed.supportedVersions.length > 0) data.supported = parsed.supportedVersions;
  if (parsed.requestedVersion) data.requested = parsed.requestedVersion;
  if (parsed.requiredCapabilities.length > 0) data.requiredCapabilities = parsed.requiredCapabilities;
  return Object.keys(data).length > 0 ? data : null;
}

/**
 * The McpError a failed RPC stands for, or null when it is not one.
 * Null means "rethrow the gRPC error as it is" — an auth failure, or an
 * UNIMPLEMENTED from a server that does not serve v2 at all.
 */
export function errorFromRpc(
  status: Status,
  details: string,
  trailer: Metadata | null,
): McpError | null {
  const code = trailer?.get(CODE_KEY);
  if (code !== undefined) {
    const raw = trailer?.get(DATA_KEY);
    return new McpError(Number(code), details, raw ? dataFrom(raw) : null);
  }
  if (status === Status.DEADLINE_EXCEEDED) {
    return new McpError(ErrorCode.RequestTimeout, details || "Request timeout");
  }
  if (status === Status.UNAVAILABLE) {
    return new McpError(ErrorCode.NotConnected, details || "Server unavailable");
  }
  return null;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```powershell
cd typescript; npx vitest run tests/v2-errors.test.ts; npx vitest run; npx tsc -p tsconfig.build.json --noEmit
```

Expected: 10 new tests pass; the full suite and the build type check stay green.

- [ ] **Step 5: Commit**

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src/errors.ts typescript/src/v2/errors.ts typescript/tests/v2-errors.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): map MCP errors onto gRPC statuses for the v2 protocol"
```

---

### Task 6: TypeScript — the v2 servicer, served next to v1

**Files:**
- Create: `typescript/src/v2/servicer.ts`
- Modify: `typescript/src/server.ts` (`listen`)
- Test: `typescript/tests/v2-server.test.ts`

**Interfaces:**
- Consumes: `toServerError` from `src/v2/errors.ts`; `ToolManager.listTools()`, `ResourceManager.listResources()` / `listResourceTemplates()`, `PromptManager.listPrompts()` / `complete(refType, refName, argumentName, value)`; `paginate` from `src/_utils.ts`.
- Produces: `export const SUPPORTED_VERSIONS = ["2026-07-28"]` and `export class McpV2Servicer` (constructor options `{ name, version, toolManager, resourceManager, promptManager, pageSize? }`) implementing the generated `McpServiceImplementation` of `generated/mcp_v2.ts`.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-server.test.ts`:

```typescript
import { describe, it, expect, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status } from "nice-grpc-common";
import { z } from "zod";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpDefinition, ErrorData, CacheScope, type McpClient } from "../generated/mcp_v2.js";

const META = {
  protocolVersion: "2026-07-28",
  clientCapabilities: { extensions: {} },
  clientInfo: { name: "test", version: "0" },
};

describe("the v2 service", () => {
  let server: RapidMCP;
  let channel: Channel | null = null;
  let port = 0;

  async function start(opts: Partial<RapidMCPOptions> = {}, bare = false): Promise<McpClient> {
    server = new RapidMCP({ name: "v2-server", version: "1.2.3", ...opts });
    if (!bare) {
      server.addTool({
        name: "echo",
        description: "Echo",
        parameters: z.object({ text: z.string() }),
        annotations: { readOnly: true },
        execute: async (args) => args.text,
      });
      server.addTool({ name: "plain", execute: async () => "x" });
      server.addResource({
        uri: "res://a",
        name: "a",
        description: "A",
        load: async () => ({ text: "a" }),
      });
      server.addResourceTemplate({
        uriTemplate: "res://items/{id}",
        name: "item",
        load: async () => ({ text: "i" }),
      });
      server.addPrompt({
        name: "greet",
        description: "Greet",
        arguments: [
          { name: "who", complete: async (v) => ({ values: [`${v}lice`, `${v}da`], total: 2 }) },
        ],
        load: async () => "hi",
      });
    }
    port = await server.listen();
    channel = createChannel(`127.0.0.1:${port}`);
    return createClientFactory().create(McpDefinition, channel);
  }

  afterEach(async () => {
    channel?.close();
    channel = null;
    await server.close();
  });

  /** Run a call expected to fail; return its status, MCP code and trailer. */
  async function failure(call: (onTrailer: (t: Metadata) => void) => Promise<unknown>) {
    let trailer = new Metadata();
    const err = await call((t) => (trailer = t)).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ClientError);
    return {
      status: (err as ClientError).code,
      mcpCode: Number(trailer.get("mcp-error-code")),
      trailer,
    };
  }

  it("reports identity, versions and capabilities on discover", async () => {
    const v2 = await start();

    const result = await v2.discover({ meta: META });

    expect(result.meta?.serverInfo).toEqual({ name: "v2-server", version: "1.2.3" });
    expect(result.supportedVersions).toEqual(["2026-07-28"]);
    expect(result.capabilities?.tools?.listChanged).toBe(true);
    expect(result.capabilities?.resources).toBeDefined();
    expect(result.capabilities?.prompts).toBeDefined();
    expect(result.cache).toEqual({ ttlMs: 0n, scope: CacheScope.CACHE_SCOPE_PRIVATE });
  });

  it("omits capabilities for what is not registered", async () => {
    const v2 = await start({}, true);

    const result = await v2.discover({ meta: META });

    expect(result.capabilities?.tools).toBeUndefined();
    expect(result.capabilities?.resources).toBeUndefined();
    expect(result.capabilities?.prompts).toBeUndefined();
  });

  it("lists tools with schemas and only the hints that were set", async () => {
    const v2 = await start();

    const result = await v2.listTools({ meta: META, cursor: "" });
    const tools = new Map(result.tools.map((t) => [t.name, t]));

    expect([...tools.keys()].sort()).toEqual(["echo", "plain"]);
    expect(JSON.parse(tools.get("echo")!.inputSchema).properties.text.type).toBe("string");
    expect(tools.get("echo")!.annotations?.readOnlyHint).toBe(true);
    expect(tools.get("echo")!.annotations?.destructiveHint).toBeUndefined();
    expect(tools.get("plain")!.annotations).toBeUndefined();
    expect(result.meta?.serverInfo?.name).toBe("v2-server");
  });

  it("lists resources, templates and prompts", async () => {
    const v2 = await start();

    const resources = await v2.listResources({ meta: META, cursor: "" });
    const templates = await v2.listResourceTemplates({ meta: META, cursor: "" });
    const prompts = await v2.listPrompts({ meta: META, cursor: "" });

    expect(resources.resources.map((r) => [r.uri, r.description])).toEqual([["res://a", "A"]]);
    expect(templates.templates.map((t) => t.uriTemplate)).toEqual(["res://items/{id}"]);
    expect(prompts.prompts.map((p) => [p.name, p.arguments.map((a) => a.name)])).toEqual([
      ["greet", ["who"]],
    ]);
  });

  it("returns completion values", async () => {
    const v2 = await start();

    const result = await v2.complete({
      meta: META,
      ref: { type: "ref/prompt", name: "greet" },
      argument: { name: "who", value: "A" },
    });

    expect(result.values).toEqual(["Alice", "Ada"]);
    expect(result.total).toBe(2);
  });

  it("paginates lists and tolerates a garbage cursor", async () => {
    const v2 = await start({ pageSize: 1 });

    const first = await v2.listTools({ meta: META, cursor: "" });
    const second = await v2.listTools({ meta: META, cursor: first.nextCursor });
    const garbage = await v2.listTools({ meta: META, cursor: "not-a-cursor" });

    expect(first.tools.map((t) => t.name)).toEqual(["echo"]);
    expect(second.tools.map((t) => t.name)).toEqual(["plain"]);
    expect(second.nextCursor).toBe("");
    expect(garbage.tools.map((t) => t.name)).toEqual(["echo"]);
  });

  it("rejects a request without meta as invalid params", async () => {
    const v2 = await start();

    const { status, mcpCode } = await failure((onTrailer) => v2.listTools({}, { onTrailer }));

    expect([mcpCode, status]).toEqual([-32602, Status.INVALID_ARGUMENT]);
  });

  it("rejects a request without client capabilities as invalid params", async () => {
    const v2 = await start();

    const { mcpCode } = await failure((onTrailer) =>
      v2.discover({ meta: { protocolVersion: "2026-07-28" } }, { onTrailer }),
    );

    expect(mcpCode).toBe(-32602);
  });

  it("names the supported versions when the requested one is not", async () => {
    const v2 = await start();

    const { status, mcpCode, trailer } = await failure((onTrailer) =>
      v2.discover({ meta: { ...META, protocolVersion: "1900-01-01" } }, { onTrailer }),
    );
    const data = ErrorData.decode(trailer.get("mcp-error-data-bin")!);

    expect([mcpCode, status]).toEqual([-32022, Status.FAILED_PRECONDITION]);
    expect(data.supportedVersions).toEqual(["2026-07-28"]);
    expect(data.requestedVersion).toBe("1900-01-01");
  });

  it("requires the token on v2 calls when the server has auth", async () => {
    const v2 = await start({ auth: (token) => token === "s3cret" });

    const denied = await v2.discover({ meta: META }).then(
      () => null,
      (e: unknown) => e,
    );
    const allowed = await v2.discover(
      { meta: META },
      { metadata: Metadata({ authorization: "Bearer s3cret" }) },
    );

    expect((denied as ClientError).code).toBe(Status.UNAUTHENTICATED);
    expect(allowed.meta?.serverInfo?.name).toBe("v2-server");
  });

  it("keeps serving v1 clients on the same port", async () => {
    await start();
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect();

    const result = await client.callTool("echo", { text: "still v1" });
    await client.close();

    expect(result.content[0].text).toBe("still v1");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-server.test.ts
```

Expected: every v2 test fails with `UNIMPLEMENTED`; "keeps serving v1 clients" passes.

- [ ] **Step 3: Write the servicer**

`typescript/src/v2/servicer.ts`:

```typescript
/**
 * v2 servicer — MCP 2026-07-28 semantics, one RPC per operation.
 *
 * Stateless by construction: every handler reads what it needs from the
 * request's own `meta` and from the server's registries, never from anything
 * an earlier request left behind.
 */
import type { CallContext } from "nice-grpc-common";
import {
  CacheScope,
  type CacheHint,
  type CompleteRequest,
  type CompleteResult,
  type DeepPartial,
  type DiscoverRequest,
  type DiscoverResult,
  type ListPromptsRequest,
  type ListPromptsResult,
  type ListResourceTemplatesRequest,
  type ListResourceTemplatesResult,
  type ListResourcesRequest,
  type ListResourcesResult,
  type ListToolsRequest,
  type ListToolsResult,
  type McpServiceImplementation,
  type RequestMeta,
  type ResultMeta,
} from "../../generated/mcp_v2.js";
import { paginate } from "../_utils.js";
import { ErrorCode, McpError } from "../errors.js";
import type { PromptManager } from "../prompts/prompt-manager.js";
import type { ResourceManager } from "../resources/resource-manager.js";
import type { ToolManager } from "../tools/tool-manager.js";
import { toServerError } from "./errors.js";

export const SUPPORTED_VERSIONS = ["2026-07-28"];

export interface McpV2ServicerOptions {
  name: string;
  version: string;
  toolManager: ToolManager;
  resourceManager: ResourceManager;
  promptManager: PromptManager;
  pageSize?: number;
}

const NO_CACHE: CacheHint = { ttlMs: 0n, scope: CacheScope.CACHE_SCOPE_PRIVATE };

export class McpV2Servicer implements McpServiceImplementation {
  constructor(private readonly _opts: McpV2ServicerOptions) {}

  private _resultMeta(): ResultMeta {
    return { serverInfo: { name: this._opts.name, version: this._opts.version } };
  }

  /** Reject a request whose metadata is missing or names a version we do not serve. */
  private _checkMeta(meta: RequestMeta | undefined, context: CallContext): void {
    if (!meta?.protocolVersion || !meta.clientCapabilities) {
      throw toServerError(
        new McpError(
          ErrorCode.InvalidParams,
          "Request meta must carry protocolVersion and clientCapabilities",
        ),
        context.trailer,
      );
    }
    if (!SUPPORTED_VERSIONS.includes(meta.protocolVersion)) {
      throw toServerError(
        new McpError(ErrorCode.UnsupportedProtocolVersion, "Unsupported protocol version", {
          supported: SUPPORTED_VERSIONS,
          requested: meta.protocolVersion,
        }),
        context.trailer,
      );
    }
  }

  async discover(
    request: DiscoverRequest,
    context: CallContext,
  ): Promise<DeepPartial<DiscoverResult>> {
    this._checkMeta(request.meta, context);
    const { toolManager, resourceManager, promptManager } = this._opts;
    const hasResources =
      resourceManager.listResources().length > 0 ||
      resourceManager.listResourceTemplates().length > 0;
    return {
      meta: this._resultMeta(),
      supportedVersions: SUPPORTED_VERSIONS,
      capabilities: {
        tools: toolManager.listTools().length > 0 ? { listChanged: true } : undefined,
        resources: hasResources ? { listChanged: true, subscribe: true } : undefined,
        prompts: promptManager.listPrompts().length > 0 ? { listChanged: true } : undefined,
      },
      cache: NO_CACHE,
    };
  }

  async listTools(
    request: ListToolsRequest,
    context: CallContext,
  ): Promise<DeepPartial<ListToolsResult>> {
    this._checkMeta(request.meta, context);
    const tools = this._opts.toolManager.listTools().map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
      outputSchema: t.outputSchema,
      // Only the hints the author actually set go on the wire.
      annotations: t.annotations
        ? {
            title: t.annotations.title ?? "",
            readOnlyHint: t.annotations.readOnly,
            destructiveHint: t.annotations.destructive,
            idempotentHint: t.annotations.idempotent,
            openWorldHint: t.annotations.openWorld,
          }
        : undefined,
    }));
    const [page, nextCursor] = paginate(tools, request.cursor, this._opts.pageSize);
    return { meta: this._resultMeta(), tools: page, nextCursor, cache: NO_CACHE };
  }

  async listResources(
    request: ListResourcesRequest,
    context: CallContext,
  ): Promise<DeepPartial<ListResourcesResult>> {
    this._checkMeta(request.meta, context);
    const resources = this._opts.resourceManager.listResources().map((r) => ({
      uri: r.uri,
      name: r.name,
      description: r.description,
      mimeType: r.mimeType,
    }));
    const [page, nextCursor] = paginate(resources, request.cursor, this._opts.pageSize);
    return { meta: this._resultMeta(), resources: page, nextCursor, cache: NO_CACHE };
  }

  async listResourceTemplates(
    request: ListResourceTemplatesRequest,
    context: CallContext,
  ): Promise<DeepPartial<ListResourceTemplatesResult>> {
    this._checkMeta(request.meta, context);
    const templates = this._opts.resourceManager.listResourceTemplates().map((t) => ({
      uriTemplate: t.uriTemplate,
      name: t.name,
      description: t.description,
      mimeType: t.mimeType,
    }));
    const [page, nextCursor] = paginate(templates, request.cursor, this._opts.pageSize);
    return { meta: this._resultMeta(), templates: page, nextCursor, cache: NO_CACHE };
  }

  async listPrompts(
    request: ListPromptsRequest,
    context: CallContext,
  ): Promise<DeepPartial<ListPromptsResult>> {
    this._checkMeta(request.meta, context);
    const prompts = this._opts.promptManager.listPrompts().map((p) => ({
      name: p.name,
      description: p.description,
      arguments: p.arguments.map((a) => ({
        name: a.name,
        description: a.description ?? "",
        required: a.required ?? false,
      })),
    }));
    const [page, nextCursor] = paginate(prompts, request.cursor, this._opts.pageSize);
    return { meta: this._resultMeta(), prompts: page, nextCursor, cache: NO_CACHE };
  }

  async complete(
    request: CompleteRequest,
    context: CallContext,
  ): Promise<DeepPartial<CompleteResult>> {
    this._checkMeta(request.meta, context);
    try {
      const result = await this._opts.promptManager.complete(
        request.ref?.type ?? "",
        request.ref?.name ?? "",
        request.argument?.name ?? "",
        request.argument?.value ?? "",
      );
      return {
        meta: this._resultMeta(),
        values: result.values,
        hasMore: result.hasMore ?? false,
        total: result.total ?? result.values.length,
      };
    } catch (err) {
      console.error("[rapidmcp] completion handler failed:", err);
      throw toServerError(
        new McpError(ErrorCode.InternalError, "Completion handler failed"),
        context.trailer,
      );
    }
  }
}
```

- [ ] **Step 4: Register it next to v1**

In `typescript/src/server.ts`, add the imports:

```typescript
import { McpDefinition as McpV2Definition } from "../generated/mcp_v2.js";
import { McpV2Servicer } from "./v2/servicer.js";
```

and in `listen()`, replace the block from `this._server = createServer();` through the `registrar.add(...)` line with:

```typescript
    const v2Servicer = new McpV2Servicer({
      name: this._name,
      version: this._version,
      toolManager: this._toolManager,
      resourceManager: this._resourceManager,
      promptManager: this._promptManager,
      pageSize: this._pageSize,
    });

    this._server = createServer();
    const registrar = this._auth ? this._server.with(authMiddleware(this._auth)) : this._server;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- DeepPartial union type mismatch
    registrar.add(McpDefinition, servicer as any);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- DeepPartial union type mismatch
    registrar.add(McpV2Definition, v2Servicer as any);
```

- [ ] **Step 5: Run the tests to verify they pass**

```powershell
cd typescript; npx vitest run tests/v2-server.test.ts; npx vitest run; npx tsc -p tsconfig.build.json --noEmit
```

Expected: 11 tests pass in the new file; the full suite and the build type check stay green.

- [ ] **Step 6: Commit**

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src/v2/servicer.ts typescript/src/server.ts typescript/tests/v2-server.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): serve the stateless v2 service next to the v1 stream"
```

---

### Task 7: TypeScript — client `mode`

**Files:**
- Create: `typescript/src/v2/client-transport.ts`
- Modify: `typescript/src/auth.ts` (`ClientOptions.mode`)
- Modify: `typescript/src/types.ts` (add `convertToolV2`)
- Modify: `typescript/src/client.ts`
- Modify: `CHANGELOG.md` (TypeScript `[Unreleased]` → `### Added`)
- Test: `typescript/tests/v2-client.test.ts`

**Interfaces:**
- Consumes: `errorFromRpc` from `src/v2/errors.ts`; the generated `McpDefinition` / `McpClient` of `generated/mcp_v2.ts`.
- Produces: `ClientOptions.mode?: "legacy" | "modern" | "auto"` (default `"legacy"`); `client.protocol: "v1" | "v2" | null`.
- Produces: `class V2Transport` (constructor `(channel: Channel, opts: ClientOptions, timeoutMs: number, supportsElicitation: () => boolean)`) with `discover(): Promise<ServerInfo>`, `listTools(cursor?)`, `listResources(cursor?)`, `listResourceTemplates(cursor?)`, `listPrompts(cursor?)`, `complete(refType, refName, argName, argValue)`; and `isV2Missing(err: unknown): boolean`.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-client.test.ts`:

```typescript
import { describe, it, expect, afterEach } from "vitest";
import { createServer, type Server } from "nice-grpc";
import { z } from "zod";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";
import { McpDefinition as McpV1Definition } from "../generated/mcp.js";
import { McpServicer } from "../src/servicer.js";

describe("Client mode", () => {
  let server: RapidMCP | null = null;
  let client: Client | null = null;
  let v1Only: Server | null = null;

  async function start(opts: Partial<RapidMCPOptions> = {}): Promise<number> {
    server = new RapidMCP({ name: "dual", version: "9.9", ...opts });
    server.addTool({
      name: "echo",
      description: "Echo",
      parameters: z.object({ text: z.string() }),
      annotations: { readOnly: true },
      execute: async (args) => args.text,
    });
    server.addTool({ name: "plain", execute: async () => "x" });
    server.addResource({ uri: "res://a", name: "a", load: async () => ({ text: "a" }) });
    server.addResourceTemplate({
      uriTemplate: "res://items/{id}",
      name: "item",
      load: async () => ({ text: "i" }),
    });
    server.addPrompt({
      name: "greet",
      arguments: [{ name: "who", complete: async (v) => ({ values: [`${v}lice`] }) }],
      load: async () => "hi",
    });
    return server.listen();
  }

  afterEach(async () => {
    await client?.close();
    client = null;
    await server?.close();
    server = null;
    v1Only?.forceShutdown();
    v1Only = null;
  });

  it("discovers and lists over v2 in modern mode", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const tools = await client.listTools();
    const resources = await client.listResources();
    const templates = await client.listResourceTemplates();
    const prompts = await client.listPrompts();
    const completion = await client.complete("ref/prompt", "greet", "who", "A");

    expect(client.protocol).toBe("v2");
    expect(client.isConnected).toBe(true);
    expect(client.serverInfo).toEqual({
      serverName: "dual",
      serverVersion: "9.9",
      capabilities: { tools: true, toolsListChanged: true, resources: true, prompts: true },
    });
    expect(tools.items.map((t) => t.name).sort()).toEqual(["echo", "plain"]);
    expect((tools.items[0].inputSchema.properties as any).text.type).toBe("string");
    expect(resources.items.map((r) => r.uri)).toEqual(["res://a"]);
    expect(templates.items.map((t) => t.uriTemplate)).toEqual(["res://items/{id}"]);
    expect(prompts.items.map((p) => p.name)).toEqual(["greet"]);
    expect(completion.values).toEqual(["Alice"]);
    expect(await client.ping()).toBe(true);
  });

  it("applies MCP defaults to annotation hints the server left unset", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const tools = new Map((await client.listTools()).items.map((t) => [t.name, t]));

    // No annotations at all: MCP's defaults.
    expect(tools.get("plain")!.annotations).toEqual({
      title: "",
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: false,
      openWorldHint: true,
    });
    // Annotated with readOnly only: that hint as given, the rest defaulted.
    expect(tools.get("echo")!.annotations.readOnlyHint).toBe(true);
    expect(tools.get("echo")!.annotations.destructiveHint).toBe(true);
  });

  it("follows pagination cursors in modern mode", async () => {
    const port = await start({ pageSize: 1 });
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const first = await client.listTools();
    const second = await client.listTools(first.nextCursor ?? undefined);

    expect(first.items.map((t) => t.name)).toEqual(["echo"]);
    expect(second.items.map((t) => t.name)).toEqual(["plain"]);
    expect(second.nextCursor).toBeNull();
  });

  it("says which operations v2 does not carry yet", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const err = await client.callTool("echo", { text: "x" }).then(
      () => null,
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(-32601);
    expect((err as McpError).message).toContain("legacy");
  });

  it("sends its token in modern mode", async () => {
    const port = await start({ auth: (token) => token === "s3cret" });
    client = new Client(`127.0.0.1:${port}`, { mode: "modern", token: "s3cret" });
    await client.connect();
    const denied = new Client(`127.0.0.1:${port}`, { mode: "modern", token: "nope" });

    expect((await client.listPrompts()).items.map((p) => p.name)).toEqual(["greet"]);
    await expect(denied.connect()).rejects.toThrow(/UNAUTHENTICATED/);
  });

  it("picks v2 in auto mode when the server offers it", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`, { mode: "auto" });
    await client.connect();

    expect(client.protocol).toBe("v2");
  });

  it("falls back to v1 in auto mode against a server without v2", async () => {
    await start(); // only to build a populated RapidMCP; its own listener is unused
    v1Only = createServer();
    v1Only.add(
      McpV1Definition,
      new McpServicer({
        name: "old",
        version: "0.1",
        toolManager: server!.toolManager,
        resourceManager: server!.resourceManager,
        promptManager: server!.promptManager,
        middlewares: [],
      }) as any,
    );
    const port = await v1Only.listen("127.0.0.1:0");
    client = new Client(`127.0.0.1:${port}`, { mode: "auto" });
    await client.connect();

    const result = await client.callTool("echo", { text: "old server" });

    expect(client.protocol).toBe("v1");
    expect(result.content[0].text).toBe("old server");
  });

  it("fails promptly in auto mode when nothing is listening", async () => {
    client = new Client("127.0.0.1:1", { mode: "auto", requestTimeout: 2000 });
    const start = Date.now();

    const err = await client.connect().then(
      () => null,
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(McpError);
    expect([408, 503]).toContain((err as McpError).code);
    expect(Date.now() - start).toBeLessThan(8000);
    expect(client.isConnected).toBe(false);
  });

  it("keeps legacy as the default", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`);
    await client.connect();

    expect(client.protocol).toBe("v1");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-client.test.ts
```

Expected: the modern/auto tests fail — `client.protocol` is `undefined` and `mode` is ignored (the client speaks v1 regardless); "falls back to v1" and "keeps legacy as the default" fail only on `protocol`.

- [ ] **Step 3: Add the option and the tool converter**

In `typescript/src/auth.ts`, extend `ClientOptions`:

```typescript
export interface ClientOptions {
  token?: string;
  tls?: TlsConfig;
  requestTimeout?: number;
  /**
   * Which protocol version to speak. "legacy" (default) is the v1 stream;
   * "modern" is the stateless v2 service; "auto" tries v2 and falls back to v1.
   */
  mode?: "legacy" | "modern" | "auto";
}
```

Append to `typescript/src/types.ts`:

```typescript
/**
 * Like convertTool for a v2 Tool, whose hints may be unset. An unset hint
 * takes MCP's default: not read-only, destructive, not idempotent, open-world.
 */
export function convertToolV2(p: {
  name: string;
  description: string;
  inputSchema: string;
  outputSchema: string;
  annotations?:
    | {
        title: string;
        readOnlyHint?: boolean | undefined;
        destructiveHint?: boolean | undefined;
        idempotentHint?: boolean | undefined;
        openWorldHint?: boolean | undefined;
      }
    | undefined;
}): Tool {
  const a = p.annotations;
  return {
    name: p.name,
    description: p.description,
    inputSchema: p.inputSchema ? JSON.parse(p.inputSchema) : {},
    outputSchema: p.outputSchema ? JSON.parse(p.outputSchema) : null,
    annotations: {
      title: a?.title ?? "",
      readOnlyHint: a?.readOnlyHint ?? false,
      destructiveHint: a?.destructiveHint ?? true,
      idempotentHint: a?.idempotentHint ?? false,
      openWorldHint: a?.openWorldHint ?? true,
    },
  };
}
```

- [ ] **Step 4: Write the v2 transport**

`typescript/src/v2/client-transport.ts`:

```typescript
/** Client transport for the v2 protocol: one stateless RPC per operation. */
import { createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status, type CallOptions } from "nice-grpc-common";
import { McpDefinition, type McpClient, type RequestMeta } from "../../generated/mcp_v2.js";
import { buildMetadata, type ClientOptions } from "../auth.js";
import { ErrorCode, McpError } from "../errors.js";
import {
  convertCompleteResult,
  convertPrompt,
  convertResource,
  convertResourceTemplate,
  convertToolV2,
  type CompleteResult,
  type ListResult,
  type Prompt,
  type Resource,
  type ResourceTemplate,
  type ServerInfo,
  type Tool,
} from "../types.js";
import { errorFromRpc } from "./errors.js";

export const PROTOCOL_VERSION = "2026-07-28";

/** True when a failed discover means "this server does not serve v2". */
export function isV2Missing(err: unknown): boolean {
  return err instanceof ClientError && err.code === Status.UNIMPLEMENTED;
}

export class V2Transport {
  private _client: McpClient;

  constructor(
    channel: Channel,
    private readonly _opts: ClientOptions,
    private readonly _timeoutMs: number,
    private readonly _supportsElicitation: () => boolean,
  ) {
    this._client = createClientFactory().create(McpDefinition, channel);
  }

  private _meta(): RequestMeta {
    return {
      protocolVersion: PROTOCOL_VERSION,
      clientCapabilities: {
        elicitation: this._supportsElicitation() ? { form: true, url: false } : undefined,
        extensions: {},
      },
      clientInfo: { name: "rapidmcp-typescript", version: "0.3.0" },
    };
  }

  /** Run one RPC with the token, a deadline, and MCP error translation. */
  private async _call<T>(invoke: (options: CallOptions) => Promise<T>): Promise<T> {
    let trailer: Metadata | null = null;
    const options: CallOptions = {
      signal: AbortSignal.timeout(this._timeoutMs),
      onTrailer: (t) => {
        trailer = t;
      },
    };
    if (this._opts.token) options.metadata = buildMetadata(this._opts);
    try {
      return await invoke(options);
    } catch (err) {
      if (err instanceof ClientError) {
        const mapped = errorFromRpc(err.code, err.details, trailer);
        if (mapped) throw mapped;
      } else if (err instanceof Error && (err.name === "AbortError" || err.name === "TimeoutError")) {
        throw new McpError(ErrorCode.RequestTimeout, "Request timeout");
      }
      throw err;
    }
  }

  async discover(): Promise<ServerInfo> {
    const result = await this._call((o) => this._client.discover({ meta: this._meta() }, o));
    const caps = result.capabilities;
    return {
      serverName: result.meta?.serverInfo?.name ?? "",
      serverVersion: result.meta?.serverInfo?.version ?? "",
      capabilities: {
        tools: caps?.tools !== undefined,
        toolsListChanged: caps?.tools?.listChanged ?? false,
        resources: caps?.resources !== undefined,
        prompts: caps?.prompts !== undefined,
      },
    };
  }

  async listTools(cursor?: string): Promise<ListResult<Tool>> {
    const result = await this._call((o) =>
      this._client.listTools({ meta: this._meta(), cursor: cursor ?? "" }, o),
    );
    return { items: result.tools.map(convertToolV2), nextCursor: result.nextCursor || null };
  }

  async listResources(cursor?: string): Promise<ListResult<Resource>> {
    const result = await this._call((o) =>
      this._client.listResources({ meta: this._meta(), cursor: cursor ?? "" }, o),
    );
    return { items: result.resources.map(convertResource), nextCursor: result.nextCursor || null };
  }

  async listResourceTemplates(cursor?: string): Promise<ListResult<ResourceTemplate>> {
    const result = await this._call((o) =>
      this._client.listResourceTemplates({ meta: this._meta(), cursor: cursor ?? "" }, o),
    );
    return {
      items: result.templates.map(convertResourceTemplate),
      nextCursor: result.nextCursor || null,
    };
  }

  async listPrompts(cursor?: string): Promise<ListResult<Prompt>> {
    const result = await this._call((o) =>
      this._client.listPrompts({ meta: this._meta(), cursor: cursor ?? "" }, o),
    );
    return { items: result.prompts.map(convertPrompt), nextCursor: result.nextCursor || null };
  }

  async complete(
    refType: string,
    refName: string,
    argName: string,
    argValue: string,
  ): Promise<CompleteResult> {
    const result = await this._call((o) =>
      this._client.complete(
        {
          meta: this._meta(),
          ref: { type: refType, name: refName },
          argument: { name: argName, value: argValue },
        },
        o,
      ),
    );
    return convertCompleteResult(result);
  }
}
```

- [ ] **Step 5: Wire `mode` into `Client`**

All edits are in `typescript/src/client.ts`.

Imports — add:

```typescript
import { V2Transport, isV2Missing } from "./v2/client-transport.js";
```

Fields — add next to `_connected`:

```typescript
  private _mode: "legacy" | "modern" | "auto";
  private _v2: V2Transport | null = null;
```

Constructor — add as the last line:

```typescript
    this._mode = opts.mode ?? "legacy";
```

Add next to `isConnected`:

```typescript
  /** "v2" or "v1" once connected, null before. */
  get protocol(): "v1" | "v2" | null {
    if (this._v2) return "v2";
    return this._connected ? "v1" : null;
  }
```

In `_doConnect()`, directly after the line `this._channel = createChannel(this._target, credentials);`, insert:

```typescript
    if (this._mode !== "legacy") {
      const transport = new V2Transport(
        this._channel,
        this._opts,
        this._requestTimeout,
        () => this._elicitationHandler !== null,
      );
      try {
        this._serverInfo = await transport.discover();
        this._v2 = transport;
        this._connected = true;
        return;
      } catch (err) {
        if (!(this._mode === "auto" && isV2Missing(err))) {
          this._channel.close();
          this._channel = null;
          throw err;
        }
        // An old server: carry on with the v1 stream on the same channel.
      }
    }
```

Add a helper next to `_assertStreamOpen`:

```typescript
  /** Throw for operations the v2 protocol does not carry yet. */
  private _v1Only(operation: string): void {
    if (this._v2) {
      throw new McpError(
        ErrorCode.MethodNotFound,
        `${operation} is not available on the v2 protocol yet; use mode: "legacy"`,
      );
    }
  }
```

Route the public methods. As the first statement of each:

```typescript
  async listTools(cursor?: string): Promise<ListResult<Tool>> {
    if (this._v2) return this._v2.listTools(cursor);
```

```typescript
  async listResources(cursor?: string): Promise<ListResult<Resource>> {
    if (this._v2) return this._v2.listResources(cursor);
```

```typescript
  async listResourceTemplates(cursor?: string): Promise<ListResult<ResourceTemplate>> {
    if (this._v2) return this._v2.listResourceTemplates(cursor);
```

```typescript
  async listPrompts(cursor?: string): Promise<ListResult<Prompt>> {
    if (this._v2) return this._v2.listPrompts(cursor);
```

```typescript
  async complete(refType: string, refName: string, argName: string, argValue: string): Promise<CompleteResult> {
    if (this._v2) return this._v2.complete(refType, refName, argName, argValue);
```

```typescript
  async ping(): Promise<boolean> {
    if (this._v2) {
      await this._v2.discover();
      return true;
    }
```

And as the first statement of `callTool`, `readResource`, `getPrompt`, `subscribeResource`, `cancel` and `notifyRootsListChanged`:

```typescript
    this._v1Only("callTool");
```

(with each method's own name). In `callTool` it goes before the aborted-signal check.

In `close()`, replace the first line (`if (!this._connected && !this._readerDone) return;`) with:

```typescript
    if (this._v2) {
      this._v2 = null;
      this._channel?.close();
      this._channel = null;
      this._connected = false;
      this._serverInfo = null;
      return;
    }
    if (!this._connected && !this._readerDone) return;
```

- [ ] **Step 6: Run the tests to verify they pass**

```powershell
cd typescript; npx vitest run tests/v2-client.test.ts; npx vitest run; npx tsc -p tsconfig.build.json --noEmit
```

Expected: 9 tests pass in the new file; the full suite and the build type check stay green.

- [ ] **Step 7: Changelog and commit**

Under the TypeScript `### [Unreleased]` → `### Added` list in `CHANGELOG.md`, add:

```markdown
- **Protocol v2 (experimental, phase 1):** servers also answer the stateless `mcp.v2.Mcp` service — `discover`, the list operations and `complete` — following MCP 2026-07-28. `new Client(addr, { mode: "modern" })` speaks it; `mode: "auto"` tries it and falls back to v1; the default stays `"legacy"`. Tool calls, resource reads and prompts are not on v2 yet
```

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src typescript/tests/v2-client.test.ts CHANGELOG.md
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): client mode to speak the v2 protocol, with fallback to v1"
```

---

## After the last task

Run everything once more from a clean shell and record the numbers in the final report:

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
.\.venv\Scripts\python.exe -m ruff check src tests; .\.venv\Scripts\python.exe -m ruff format --check src tests
cd ..\typescript; npx vitest run; npx tsc -p tsconfig.build.json --noEmit
```

Known and unrelated: `npx tsc --noEmit` (the non-build config) reports two type errors in `tests/client.test.ts` and `tests/test-server.ts` that predate this work.

Phases 2–5 of the spec each get their own plan.
