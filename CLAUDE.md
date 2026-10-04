# RapidMCP — Project Context

## What this is

gRPC-native MCP (Model Context Protocol) library. Instead of JSON-RPC over HTTP, uses protobuf over a persistent bidirectional gRPC stream. ~17x lower latency than FastMCP Streamable HTTP.

## Tech stack

- Python 3.10+, `grpcio`, `protobuf`
- `uv` — package manager and venv (never bare `pip install`)
- `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`)
- `ruff` — linter + formatter
- `ty` — type checker (non-blocking / warn-only)
- TypeScript / Node.js (`@grpc/grpc-js`, `@bufbuild/protobuf`)
- `vitest` — TypeScript test runner

## Key commands

```bash
# Python
cd python
uv sync --extra dev          # install all deps
uv run pytest -v             # run all tests (Docker-backed files need Docker)
uv run pytest tests/test_integration.py -v   # integration only
uv run pytest tests/test_middleware.py -v    # middleware only
uv run pytest tests/test_mounting.py -v      # mounting only
uv run ruff check src tests  # lint
uv run ruff format src tests # format
uv run ty check              # type check (non-blocking)

# TypeScript
cd typescript
npm install                  # install deps
npx vitest run               # run all tests
npx tsc -p tsconfig.build.json --noEmit   # type check the library
```

## Public API (current)

```python
from rapidmcp import RapidMCP, Client, Context
from rapidmcp import Middleware, ToolCallContext, TimingMiddleware, LoggingMiddleware
from rapidmcp import TimeoutMiddleware, ValidationMiddleware, ToolAnnotations
from rapidmcp import Audio, Image
from rapidmcp import (
    BoolField, ElicitationField, ElicitationResult,
    EnumField, FloatField, IntField, StringField, build_elicitation_schema,
)
from rapidmcp.errors import McpError, ToolError
from rapidmcp.integrations.livekit import MCPServerGRPC  # livekit-agents MCPServer adapter
from rapidmcp.integrations.langchain import RapidMCPClient  # LangChain adapter
```

## LangChain integration

Use `RapidMCPClient` to wire one or more RapidMCP gRPC servers into any
LangChain agent. Shape mirrors `langchain-mcp-adapters.MultiServerMCPClient`:

```python
from rapidmcp.integrations.langchain import RapidMCPClient

async with RapidMCPClient({
    "docs": {"address": "docs:50051"},
    "sql":  {"address": "sql:50051", "token": "...", "allowed_tools": ["query"]},
}) as rc:
    tools     = await rc.get_tools()                                   # aggregated across servers
    prompt    = await rc.get_prompt("docs", "summarise", arguments={"topic": "grpc"})
    blobs     = await rc.get_resources("docs", uris=["file:///readme.md"])
    async with rc.session("sql") as sess:
        await sess.ping()
```

TypeScript mirrors this shape — `RapidMCPClient` from `rapidmcp/integrations/langchain`.

## Project structure

```
proto/mcp.proto                    ← v1: one bidi session stream (frozen, fixes only)
proto/mcp_v2.proto                 ← v2: stateless, one RPC per operation (MCP 2026-07-28)
python/
  src/rapidmcp/
    server.py                      ← RapidMCP, mount(), decorators
    _servicer.py                   ← _McpServicer (v1 session handler)
    _v2_servicer.py                ← _McpV2Servicer (v2 RPCs), with _v2_context.py, _v2_state.py,
                                     _v2_listen.py, _v2_errors.py; _v2_client.py is the client transport
    icons.py                       ← Icon
    client.py                      ← Client, ListResult, sampling/elicitation/roots handlers
    context.py                     ← Context (explicit DI per tool call)
    middleware.py                  ← Middleware, ToolCallContext, TimingMiddleware,
                                     LoggingMiddleware, TimeoutMiddleware, ValidationMiddleware
    session.py                     ← PendingRequests, NotificationRegistry
    errors.py                      ← McpError, ToolError
    content.py                     ← Audio, Image content helpers
    elicitation.py                 ← ElicitationField types, build_elicitation_schema
    cli.py                         ← `rapidmcp run server.py` CLI entry point
    testing.py                     ← InProcessChannel for unit tests
    auth.py                        ← TLS/mTLS auth helpers
    tools/                         ← ToolManager, ToolAnnotations, Tool
    resources/                     ← ResourceManager, Resource
    prompts/                       ← PromptManager, Prompt
    integrations/
      livekit.py                   ← MCPServerGRPC adapter for livekit-agents
      langchain.py                 ← LangChain tool adapter
    _generated/                    ← protobuf-generated stubs (do not edit)
  tests/
    test_integration.py            ← integration tests (real gRPC loopback)
    test_middleware.py             ← middleware tests (unit + integration)
    test_mounting.py               ← server composition tests
    test_cli.py                    ← CLI tests
    test_sampling.py               ← sampling round-trip tests
    test_elicitation.py            ← elicitation tests
    test_content.py                ← Audio/Image content tests
    test_client.py                 ← client tests
    test_server.py                 ← server unit tests
    test_session.py                ← session/pending request tests
    test_auth.py                   ← TLS/mTLS auth tests
    test_stress.py                 ← stress/load tests
    test_tls_docker.py             ← TLS tests (requires Docker)
    test_uri_template.py           ← URI template tests
    test_integrations_auth.py      ← integration auth tests
typescript/
  src/
    server.ts                      ← RapidMCP server
    client.ts                      ← Client
    servicer.ts                    ← v1 session handler
    v2/                            ← v2 servicer, context, state, listeners, errors, client transport
    icons.ts                       ← Icon
    context.ts                     ← Context (explicit DI)
    middleware.ts                  ← Middleware chain
    session.ts                     ← PendingRequests
    auth.ts                        ← TLS/mTLS auth helpers
    types.ts                       ← shared types
    errors.ts                      ← McpError, ToolError
    index.ts                       ← public API exports
    tools/                         ← ToolManager
    resources/                     ← ResourceManager
    prompts/                       ← PromptManager
    integrations/
      langchain.ts                 ← LangChain tool adapter
  tests/                           ← vitest suites (v2-*.test.ts cover the v2 protocol)
benchmark/                         ← latency harness vs FastMCP HTTP
```

## Current state (2026-10-04)

- **465 Python tests + 320 TypeScript tests passing** (Docker-backed files excluded)
- Two protocols on one port. v1 (`mcp.Mcp/Session`) is the original bidi stream and is frozen. v2 (`mcp.v2.Mcp`) follows MCP 2026-07-28: stateless, per-request metadata, one RPC per operation, streaming calls for progress/logs, input-required rounds for elicitation (signed `request_state`), one opt-in `Listen` stream for notifications, cache hints, icons, trace context, structured tool results
- `Client(mode="legacy" | "modern" | "auto")`; default `legacy`. Sampling and roots are v1 only
- Design: `docs/superpowers/specs/2026-10-01-proto-v2-stateless-design.md`; release note: `docs/breaking-changes-next-release.md`
- Regenerate v2 stubs only: `python generate.py mcp_v2.proto` and `npm run generate -- --path ../proto/mcp_v2.proto`. Never regenerate v1
- Cross-language tests: `python/tests/test_interop_typescript.py`, `typescript/tests/interop-python.test.ts`
- Middleware, server composition (`mount`), CLI, LiveKit and LangChain integrations, TLS/mTLS and token auth, elicitation field helpers, `Audio` / `Image` content — as before, in both languages

## Architecture notes

- **v1: one service, one bidi streaming RPC.** `Session(stream ClientEnvelope) returns (stream ServerEnvelope)` — all messages over one stream.
- **v2: stateless.** Every handler reads what it needs from the request's own `meta` and the registries. MCP errors are a gRPC status plus `mcp-error-code` / `mcp-error-data-bin` trailers. On v2 a tool that elicits is re-run from the top with the answers.
- **Write-queue servicer.** Concurrent reader/writer tasks per session. Enables server push notifications, mid-handler sampling/elicitation, and concurrent tool execution.
- **Context as explicit DI.** `ctx: Context` is injected only when a tool declares it. Unlike FastMCP which pulls Context from a ContextVar set by the JSON-RPC run loop, RapidMCP's Context is constructed per-call and passed explicitly — so `ToolCallContext.ctx` is `None` for tools that didn't opt in.
- **Middleware chain.** `functools.partial` reversed registration order. First-registered = outermost. Base of chain is `_call_tool_with_dict`.
- **Manager packages.** `tools/`, `resources/`, `prompts/` are each a sub-package with a `Manager` class and a domain object. `_McpServicer` delegates to them; `server.py` registers decorators through them.

## Roadmap (next)

| Priority | Feature | Status |
|---|---|---|
| ~~1~~ | Integration test gaps | ✅ Done |
| ~~2~~ | Middleware system | ✅ Done |
| ~~3~~ | Server mounting / composition | ✅ Done |
| ~~4~~ | CLI (`rapidmcp run server.py`) | ✅ Done |
| ~~5~~ | TypeScript server | ✅ Done |
| 6 | PyPI package / versioning | Next |
| 7 | Go client stubs | Backlog |
