# FasterMCP — Project Context

## What this is

gRPC-native MCP (Model Context Protocol) library. Instead of JSON-RPC over HTTP, uses protobuf over a persistent bidirectional gRPC stream. ~17x lower latency than FastMCP Streamable HTTP.

## Tech stack

- Python 3.10+, `grpcio`, `protobuf`
- `uv` — package manager and venv (never bare `pip install`)
- `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`)
- `ruff` — linter + formatter
- `ty` — type checker (non-blocking / warn-only)

## Key commands

```bash
cd python

uv sync --extra dev          # install all deps
uv run pytest -v             # run all 167 tests
uv run pytest tests/test_integration.py -v   # integration only
uv run pytest tests/test_middleware.py -v    # middleware only
uv run pytest tests/test_mounting.py -v      # mounting only
uv run ruff check src tests  # lint
uv run ruff format src tests # format
uv run ty check              # type check (non-blocking)
```

## Public API (current)

```python
from mcp_grpc import FasterMCP, Client, Context
from mcp_grpc import Middleware, ToolCallContext, TimingMiddleware, LoggingMiddleware
from mcp_grpc import TimeoutMiddleware, ValidationMiddleware, ToolAnnotations
from mcp_grpc import Audio, Image
from mcp_grpc import (
    BoolField, ElicitationField, ElicitationResult,
    EnumField, FloatField, IntField, StringField, build_elicitation_schema,
)
from mcp_grpc.errors import McpError, ToolError
from mcp_grpc.integrations.livekit import MCPToolsetGRPC  # livekit-agents Toolset adapter
```

## Project structure

```
proto/mcp.proto                    ← single source of truth for all messages
python/
  src/mcp_grpc/
    server.py                      ← FasterMCP, mount(), decorators
    _servicer.py                   ← _McpServicer (gRPC session handler)
    client.py                      ← Client, ListResult, sampling/elicitation/roots handlers
    context.py                     ← Context (explicit DI per tool call)
    middleware.py                  ← Middleware, ToolCallContext, TimingMiddleware,
                                     LoggingMiddleware, TimeoutMiddleware, ValidationMiddleware
    session.py                     ← PendingRequests, NotificationRegistry
    errors.py                      ← McpError, ToolError
    content.py                     ← Audio, Image content helpers
    elicitation.py                 ← ElicitationField types, build_elicitation_schema
    cli.py                         ← `fastermcp run server.py` CLI entry point
    testing.py                     ← InProcessChannel for unit tests
    tools/                         ← ToolManager, ToolAnnotations, Tool
    resources/                     ← ResourceManager, Resource
    prompts/                       ← PromptManager, Prompt
    integrations/
      livekit.py                   ← MCPServerGRPC adapter for livekit-agents
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
benchmark/                         ← latency harness vs FastMCP HTTP
```

## Current state (2026-04-11)

- **167 tests passing**
- Full MCP spec parity: tools, resources, resource templates, prompts, completions, pagination, sampling, elicitation, logging, progress, notifications (bidirectional), cancellation, resource subscribe, roots, capability negotiation, ping/pong
- Middleware system: `Middleware` base class, `ToolCallContext`, `functools.partial` chain, built-ins: `TimingMiddleware`, `LoggingMiddleware`, `TimeoutMiddleware`, `ValidationMiddleware`
- Server composition: `main.mount(sub, prefix="x")` — merges tools/resources/prompts with prefix
- CLI: `fastermcp run server.py` / `fastermcp run server.py:my_app` / `fastermcp version`
- LiveKit integration: `MCPServerGRPC` adapter for `livekit-agents`
- Rich elicitation helpers: typed field builders (`BoolField`, `IntField`, `StringField`, `EnumField`, `FloatField`)
- Content helpers: `Audio`, `Image` for binary content in tool responses

## Architecture notes

- **One service, one bidi streaming RPC.** `Session(stream ClientEnvelope) returns (stream ServerEnvelope)` — all messages over one stream.
- **Write-queue servicer.** Concurrent reader/writer tasks per session. Enables server push notifications, mid-handler sampling/elicitation, and concurrent tool execution.
- **Context as explicit DI.** `ctx: Context` is injected only when a tool declares it. Unlike FastMCP which pulls Context from a ContextVar set by the JSON-RPC run loop, FasterMCP's Context is constructed per-call and passed explicitly — so `ToolCallContext.ctx` is `None` for tools that didn't opt in.
- **Middleware chain.** `functools.partial` reversed registration order. First-registered = outermost. Base of chain is `_call_tool_with_dict`.
- **Manager packages.** `tools/`, `resources/`, `prompts/` are each a sub-package with a `Manager` class and a domain object. `_McpServicer` delegates to them; `server.py` registers decorators through them.

## Roadmap (next)

| Priority | Feature | Status |
|---|---|---|
| ~~1~~ | Integration test gaps | ✅ Done |
| ~~2~~ | Middleware system | ✅ Done |
| ~~3~~ | Server mounting / composition | ✅ Done |
| ~~4~~ | CLI (`fastermcp run server.py`) | ✅ Done |
| 5 | PyPI package / versioning | Next |
| 6 | Multi-language client stubs (TypeScript, Go) | Backlog |
