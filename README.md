# FasterMCP (MCP gRPC native)

**MCP over native gRPC.** 17x lower latency than Streamable HTTP.

FasterMCP is a gRPC-native transport for the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP). Instead of JSON-RPC over HTTP, it uses protobuf messages over a persistent bidirectional gRPC stream — the same MCP semantics (tools, resources, prompts, sampling, elicitation), a fundamentally faster wire format.

| | MCP (stdio / Streamable HTTP) | FasterMCP (gRPC) |
|---|---|---|
| Wire format | JSON-RPC over text | Protobuf binary |
| Connection model | HTTP request per call | Persistent bidi stream |
| Type safety | Stringly typed | Fully typed `.proto` |
| Multi-language | Per-SDK JSON-RPC layer | Single `.proto`, generated stubs |
| Latency | ~9ms per call | ~0.5ms per call |

## Quick start

### Server

```python
from mcp_grpc import FasterMCP

server = FasterMCP(name="my-server", version="1.0.0")

@server.tool(description="Echo the input back")
async def echo(text: str) -> str:
    return text

server.run(port=50051)
```

### Client

```python
from mcp_grpc import Client

async with Client("localhost:50051") as client:
    result = await client.list_tools()
    tools = result.items  # ListResult with pagination support

    result = await client.call_tool("echo", {"text": "hello"})
    print(result.content[0].text)  # "hello"
```

### Middleware

Intercept tool calls before and after execution:

```python
from mcp_grpc import FasterMCP, Middleware, ToolCallContext, TimingMiddleware, LoggingMiddleware
from mcp_grpc._generated import mcp_pb2
import time

# Use built-ins
server = FasterMCP(name="my-server", version="1.0.0", middleware=[
    TimingMiddleware(),    # logs "echo completed in 0.52ms"
    LoggingMiddleware(),   # logs args before, is_error after
])

# Or write your own
class RateLimitMiddleware(Middleware):
    def __init__(self, max_per_second: float = 10.0):
        self._min_interval = 1.0 / max_per_second
        self._last = 0.0

    async def on_tool_call(self, tool_ctx: ToolCallContext, call_next) -> mcp_pb2.CallToolResponse:
        now = time.monotonic()
        if now - self._last < self._min_interval:
            return mcp_pb2.CallToolResponse(
                content=[mcp_pb2.ContentItem(type="text", text="rate limit exceeded")],
                is_error=True,
            )
        self._last = now
        return await call_next(tool_ctx)

server.add_middleware(RateLimitMiddleware(max_per_second=5.0))
```

### Sampling (LLM completion mid-tool)

Tools can request LLM completions from the client using `Context`:

```python
from mcp_grpc import FasterMCP, Context
from mcp_grpc._generated import mcp_pb2

server = FasterMCP(name="my-server", version="1.0.0")

@server.tool(description="Summarize text using LLM")
async def summarize(text: str, ctx: Context) -> str:
    result = await ctx.sample(
        messages=[mcp_pb2.SamplingMessage(
            role="user",
            content=mcp_pb2.ContentItem(type="text", text=f"Summarize: {text}"),
        )],
        max_tokens=200,
    )
    return result.content.text
```

The client registers a handler to provide the LLM:

```python
async def my_sampling_handler(request):
    # Call your LLM here with request.messages
    return mcp_pb2.SamplingResponse(
        role="assistant",
        content=mcp_pb2.ContentItem(type="text", text="..."),
        model="gpt-4", stop_reason="end",
    )

client = Client("localhost:50051")
client.set_sampling_handler(my_sampling_handler)
await client.connect()
```

### Elicitation (user input mid-tool)

Tools can ask the user for input:

```python
@server.tool(description="Deploy to production")
async def deploy(service: str, ctx: Context) -> str:
    response = await ctx.elicit(
        message=f"Deploy {service} to prod?",
        schema='{"type": "object", "properties": {"confirm": {"type": "boolean"}}}',
    )
    if response.action == "accept":
        return f"Deployed {service}"
    return "Cancelled"
```

### Logging and progress from tools

```python
@server.tool(description="Long-running job")
async def process(data: str, ctx: Context) -> str:
    await ctx.info("starting", extra={"input": data})
    await ctx.report_progress(progress=50, total=100)
    await ctx.info("done")
    return data
```

### Resource templates

```python
@server.resource_template(
    uri_template="file:///{path}",
    description="Read a file by path",
)
async def read_file(path: str) -> str:
    return open(path).read()
```

### Completions

```python
@server.completion("my-prompt")
async def complete_language(argument_name: str, value: str) -> list[str]:
    options = ["english", "spanish", "french", "german"]
    return [o for o in options if o.startswith(value)]
```

### Notifications

```python
# Server emits
server.notify_tools_list_changed()
server.notify_resource_updated("res://my-resource")

# Client receives
client.on_notification("tools_list_changed", my_callback)
client.on_notification("resource_updated", my_handler)

# Client emits
await client.notify_roots_list_changed()

# Server receives
server.on_roots_list_changed(my_handler)
```

## Full MCP feature support

| Feature | Status |
|---|---|
| Tools (list, call) | ✅ |
| Resources (list, read, subscribe) | ✅ |
| Resource templates | ✅ |
| Prompts (list, get) | ✅ |
| Completions | ✅ |
| Pagination (all list methods) | ✅ |
| Sampling (`ctx.sample()`) | ✅ |
| Elicitation (`ctx.elicit()`) | ✅ |
| Roots (`ctx.list_roots()`) | ✅ |
| Notifications (bidirectional) | ✅ |
| Logging (`ctx.info/debug/warning/error`) | ✅ |
| Progress (`ctx.report_progress`) | ✅ |
| Cancellation | ✅ |
| Capability negotiation | ✅ |
| Ping/Pong | ✅ |
| **Middleware** (`on_tool_call` chain) | ✅ |

## Installation

```bash
cd python
uv sync --extra dev
```

## Tests

```bash
cd python
uv run pytest tests/ -v
```

69 tests covering: tool context injection, sampling/elicitation round-trips, resource templates, completions, pagination, notifications, cancellation, resource subscribe, roots, and the full middleware chain (intercept, argument mutation, chain ordering, built-in TimingMiddleware/LoggingMiddleware).

## Benchmark: FasterMCP vs FastMCP (Streamable HTTP)

A latency benchmark comparing FasterMCP (gRPC) against [FastMCP](https://gofastmcp.com/) (Streamable HTTP). Both servers run the same `echo` tool — the difference is purely transport overhead.

### Run it

```bash
cd benchmark
uv sync
uv run python run_benchmark.py
```

Options:

```
-n, --calls N    Number of measured calls per transport (default: 1000)
```

### Results (Windows 11, loopback, 1000 sequential calls)

```
Transport             p50      p95      p99      min      max     mean    stdev
-------------------------------------------------------------------------------
FasterMCP (gRPC)    0.55ms    0.70ms    0.81ms    0.42ms    1.18ms    0.58ms    0.09ms
FastMCP (HTTP)      9.68ms   14.06ms   18.22ms    7.59ms   35.72ms   10.40ms    3.20ms
```

**FasterMCP is ~17x faster at p50 and ~22x faster at p99.** The gRPC binary transport over a persistent bidi stream eliminates HTTP connection overhead and JSON encoding on every call.

## Project structure

```
FasterMCP/
├── proto/mcp.proto              <- Protocol definition (single source of truth)
├── python/
│   ├── src/mcp_grpc/
│   │   ├── server.py            <- FasterMCP, Context, _McpServicer, decorators
│   │   ├── client.py            <- Client, ListResult, sampling/elicitation/roots handlers
│   │   ├── middleware.py        <- Middleware, ToolCallContext, TimingMiddleware, LoggingMiddleware
│   │   ├── session.py           <- PendingRequests, NotificationRegistry
│   │   ├── errors.py            <- McpError, ToolError
│   │   └── testing.py           <- InProcessChannel for unit tests
│   └── tests/                   <- 69 tests (unit + integration)
├── benchmark/
│   ├── run_benchmark.py         <- Latency harness
│   ├── grpc_server.py           <- FasterMCP echo server (gRPC)
│   └── fastmcp_server.py        <- FastMCP echo server (Streamable HTTP)
└── docs/superpowers/
    ├── specs/                   <- Design specs
    └── plans/                   <- Implementation plans
```

## Design

- **One service, one bidi streaming RPC.** `Session(stream ClientEnvelope) returns (stream ServerEnvelope)` carries all messages — mirroring MCP's duplex channel.
- **Write-queue servicer.** Concurrent reader/writer tasks per session. Enables notifications (server push), sampling/elicitation (mid-handler server-to-client requests), and concurrent tool execution.
- **Context as explicit DI.** Tool handlers that declare `ctx: Context` get sampling/elicitation/logging capabilities injected per-call. Unlike FastMCP, Context is not pulled from a ContextVar — it's constructed explicitly and DI-injected, so middleware receives `ctx=None` for tools that didn't opt in.
- **Middleware chain.** `functools.partial` reversed-registration chain wired into `_dispatch_tool`. First-registered middleware is outermost.
- **Protobuf envelopes with `oneof`.** Each envelope carries a `request_id` and one message type. The SDK handles correlation transparently via `PendingRequests` (same pattern as the official MCP SDK's `BaseSession`).

See [design spec](docs/superpowers/specs/2026-04-10-mcp-grpc-design.md) for the full protocol definition.

## Status

**Python SDK: feature-complete with full MCP spec parity + middleware.**

Next: server mounting/composition (`main.mount(sub, prefix="x")`), CLI (`fastermcp run server.py`).
