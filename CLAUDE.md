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
uv run pytest -v             # run all 69 tests
uv run pytest tests/test_integration.py -v   # integration only
uv run pytest tests/test_middleware.py -v    # middleware only
uv run ruff check src tests  # lint
uv run ruff format src tests # format
uv run ty check              # type check (non-blocking)
```

## Public API (current)

```python
from mcp_grpc import FasterMCP, Client, Context
from mcp_grpc import Middleware, ToolCallContext, TimingMiddleware, LoggingMiddleware
from mcp_grpc.errors import McpError, ToolError
```

## Project structure

```
proto/mcp.proto                    ← single source of truth for all messages
python/
  src/mcp_grpc/
    server.py                      ← FasterMCP, Context, _McpServicer, decorators
    client.py                      ← Client, ListResult, sampling/elicitation/roots handlers
    middleware.py                  ← Middleware, ToolCallContext, TimingMiddleware, LoggingMiddleware
    session.py                     ← PendingRequests, NotificationRegistry
    errors.py                      ← McpError, ToolError
    testing.py                     ← InProcessChannel for unit tests
    _generated/                    ← protobuf-generated stubs (do not edit)
  tests/
    test_integration.py            ← 59 integration tests (real gRPC loopback)
    test_middleware.py             ← 10 middleware tests (unit + integration)
benchmark/                         ← latency harness vs FastMCP HTTP
```

## Current state (2026-04-11)

- **69 tests passing**
- Full MCP spec parity: tools, resources, resource templates, prompts, completions, pagination, sampling, elicitation, logging, progress, notifications (bidirectional), cancellation, resource subscribe, roots, capability negotiation, ping/pong
- Middleware system: `Middleware` base class, `ToolCallContext`, `functools.partial` chain, `TimingMiddleware`, `LoggingMiddleware`
- Naming: `FasterMCP`, `Client`, `Context` (aligned with FastMCP / official SDK)

## Architecture notes

- **One service, one bidi streaming RPC.** `Session(stream ClientEnvelope) returns (stream ServerEnvelope)` — all messages over one stream.
- **Write-queue servicer.** Concurrent reader/writer tasks per session. Enables server push notifications, mid-handler sampling/elicitation, and concurrent tool execution.
- **Context as explicit DI.** `ctx: Context` is injected only when a tool declares it. Unlike FastMCP which pulls Context from a ContextVar set by the JSON-RPC run loop, FasterMCP's Context is constructed per-call and passed explicitly — so `ToolCallContext.ctx` is `None` for tools that didn't opt in.
- **Middleware chain.** `functools.partial` reversed registration order. First-registered = outermost. Base of chain is `_call_tool_with_dict`.

## Roadmap (next)

| Priority | Feature | Status |
|---|---|---|
| ~~1~~ | Integration test gaps | ✅ Done |
| ~~2~~ | Middleware system | ✅ Done |
| 3 | Server mounting / composition (`main.mount(sub, prefix="x")`) | Next |
| 4 | CLI (`fastermcp run server.py`) | Backlog |
