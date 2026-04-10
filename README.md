# FasterMCP

**MCP over native gRPC.** 17x lower latency than Streamable HTTP.

FasterMCP is a gRPC-native transport for the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP). Instead of JSON-RPC over HTTP, it uses protobuf messages over a persistent bidirectional gRPC stream — the same MCP semantics (tools, resources, prompts), a fundamentally faster wire format.

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
from mcp_grpc import McpServer

server = McpServer(name="my-server", version="1.0.0")

@server.tool(description="Echo the input back")
async def echo(text: str) -> str:
    return text

server.run(port=50051)
```

### Client

```python
from mcp_grpc import McpClient

async with McpClient("localhost:50051") as client:
    tools = await client.list_tools()
    result = await client.call_tool("echo", {"text": "hello"})
    print(result.content[0].text)  # "hello"
```

### LiveKit agent integration

```python
from mcp_grpc import McpClient

mcp = McpClient("localhost:50051")
await mcp.connect()
mcp_tools = await mcp.as_function_tools()  # → list[function_tool]
```

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

23 tests covering: client operations, server registration, session management, and full gRPC integration over loopback.

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
├── proto/mcp.proto              ← Protocol definition (single source of truth)
├── python/
│   ├── src/mcp_grpc/
│   │   ├── server.py            ← McpServer: decorator API, gRPC servicer
│   │   ├── client.py            ← McpClient: connect, discover, call tools
│   │   ├── session.py           ← Pending request correlation
│   │   ├── errors.py            ← McpError
│   │   └── testing.py           ← InProcessChannel for unit tests
│   └── tests/                   ← 23 tests (unit + integration)
├── benchmark/
│   ├── run_benchmark.py         ← Latency harness
│   ├── grpc_server.py           ← FasterMCP echo server (gRPC)
│   └── fastmcp_server.py        ← FastMCP echo server (Streamable HTTP)
└── docs/superpowers/
    ├── specs/                   ← Design specs
    └── plans/                   ← Implementation plans
```

## Design

- **One service, one bidi streaming RPC.** `Session(stream ClientEnvelope) returns (stream ServerEnvelope)` carries all messages — mirroring MCP's duplex channel.
- **Protobuf envelopes with `oneof`.** Each envelope carries a `request_id` and one message type. The SDK handles correlation transparently.
- **`input_schema` and `arguments` as JSON strings.** Tool schemas are JSON Schema objects; encoding them as proto messages would be brittle. String serialization preserves full flexibility.
- **Notifications carry `request_id = 0`.** Fire-and-forget; no response expected.

See [design spec](docs/superpowers/specs/2026-04-10-mcp-grpc-design.md) for the full protocol definition.

## Status

**Python POC: complete and smoke-tested.** The library is integrated into a [LiveKit](https://livekit.io/) voice agent, with tools callable end-to-end: LLM → McpClient → gRPC → McpServer → response.

Next steps: TypeScript SDK, production hardening (TLS/mTLS, sampling/elicitation).
