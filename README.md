# RapidMCP

> **Experimental.** This project is a proof-of-concept exploring gRPC as an MCP transport. The API may change without notice and it is not recommended for production use.

**MCP over native gRPC.** ~17x lower latency than Streamable HTTP.

RapidMCP is a gRPC-native transport for the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP). Instead of JSON-RPC over HTTP, it uses protobuf messages over a persistent bidirectional gRPC stream — the same MCP semantics (tools, resources, prompts, sampling, elicitation), a fundamentally faster wire format.

| | MCP (stdio / Streamable HTTP) | RapidMCP (gRPC) |
|---|---|---|
| Wire format | JSON-RPC over text | Protobuf binary |
| Connection model | HTTP request per call | Persistent bidi stream |
| Type safety | Stringly typed | Fully typed `.proto` |
| Multi-language | Per-SDK JSON-RPC layer | Single `.proto`, generated stubs |
| Latency | ~9ms per call | ~0.5ms per call |

## Installation

### Python

```bash
pip install rapidmcp
```

Optional integrations:

```bash
pip install 'rapidmcp[langchain]'   # LangChain / LangGraph
pip install 'rapidmcp[livekit]'     # livekit-agents
```

### TypeScript / Node.js

```bash
npm install @emitate/rapidmcp
```

See [`typescript/README.md`](typescript/README.md) for full TypeScript documentation.

## Quick start

### Server

```python
from rapidmcp import RapidMCP

server = RapidMCP(name="my-server", version="1.0.0")

@server.tool(description="Echo the input back")
async def echo(text: str) -> str:
    return text

server.run(port=50051)
```

### Client

```python
from rapidmcp import Client

async with Client("localhost:50051") as client:
    result = await client.list_tools()
    tools = result.items  # ListResult with pagination support

    result = await client.call_tool("echo", {"text": "hello"})
    print(result.content[0].text)  # "hello"
```

### LangChain / LangGraph integration

```python
from rapidmcp.integrations.langchain import MCPToolkit
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

async with MCPToolkit("localhost:50051") as toolkit:
    tools = await toolkit.aget_tools()
    agent = create_react_agent(ChatAnthropic(model="claude-sonnet-4-6"), tools)
    result = await agent.ainvoke({"messages": [("user", "Add 17 and 25")]})
```

### LiveKit integration

```python
from rapidmcp.integrations.livekit import MCPServerGRPC
from livekit.agents import AgentSession

session = AgentSession(
    mcp_servers=[MCPServerGRPC(address="mcp-server:50051")],
)
```

### Authentication

```python
from rapidmcp import RapidMCP

# Static token
server = RapidMCP(name="my-server", version="1.0.0", auth=lambda token: token == "secret")

# Async JWT / OAuth2 introspection
async def verify(token: str) -> bool:
    resp = await httpx.AsyncClient().post("https://auth.example.com/introspect", data={"token": token})
    return resp.json().get("active") is True

server = RapidMCP(name="my-server", version="1.0.0", auth=verify)
```

Client sends the token as a Bearer credential:

```python
async with Client("localhost:50051", token="secret") as client:
    ...
```

### TLS / mTLS

```python
from rapidmcp import RapidMCP, TLSConfig, Client, ClientTLSConfig

# Server-only TLS
server = RapidMCP(name="my-server", version="1.0.0", tls=TLSConfig(cert="server.crt", key="server.key"))

# Mutual TLS (mTLS) — requires client certificate
server = RapidMCP(name="my-server", version="1.0.0", tls=TLSConfig(cert="server.crt", key="server.key", ca="ca.crt"))

# Client: verify server with custom CA
async with Client("localhost:50051", tls=ClientTLSConfig(ca="ca.crt")) as client:
    ...

# Client: mTLS (present client certificate)
async with Client("localhost:50051", tls=ClientTLSConfig(ca="ca.crt", cert="client.crt", key="client.key")) as client:
    ...

# Combined: TLS + token auth
async with Client("localhost:50051", tls=ClientTLSConfig(ca="ca.crt"), token="secret") as client:
    ...
```

### Middleware

```python
from rapidmcp import RapidMCP, TimingMiddleware, LoggingMiddleware, TimeoutMiddleware, ValidationMiddleware

server = RapidMCP(name="my-server", version="1.0.0", middleware=[
    TimingMiddleware(),              # logs "echo completed in 0.52ms"
    LoggingMiddleware(),             # logs args before, is_error after
    TimeoutMiddleware(seconds=5.0),  # raises ToolError on timeout
    ValidationMiddleware(),          # validates args against JSON schema
])
```

### Sampling (LLM completion mid-tool)

```python
from rapidmcp import RapidMCP, Context

server = RapidMCP(name="my-server", version="1.0.0")

@server.tool(description="Summarize text using LLM")
async def summarize(text: str, ctx: Context) -> str:
    result = await ctx.sample(
        messages=[{"role": "user", "content": f"Summarize: {text}"}],
        max_tokens=200,
    )
    return result.content[0].text
```

### Elicitation (user input mid-tool)

```python
from rapidmcp import RapidMCP, Context, BoolField

server = RapidMCP(name="my-server", version="1.0.0")

@server.tool(description="Deploy to production")
async def deploy(service: str, ctx: Context) -> str:
    result = await ctx.elicit(
        message=f"Deploy {service} to prod?",
        fields={"confirm": BoolField(title="Are you sure?")},
    )
    if result.accepted and result.data.get("confirm"):
        return f"Deployed {service}"
    return "Cancelled"
```

Available field types: `BoolField`, `StringField`, `IntField`, `FloatField`, `EnumField`.

### Server composition (mounting)

```python
from rapidmcp import RapidMCP

users_server = RapidMCP("Users", "1.0")

@users_server.tool(description="Get a user by ID")
async def get_user(id: int) -> str:
    return f"user:{id}"

main = RapidMCP("Main", "1.0")
main.mount(users_server, prefix="users")
# Tool becomes: "users_get_user"

main.run(port=50051)
```

### CLI

```bash
rapidmcp run server.py           # auto-discovers `server`, `app`, or `mcp` object
rapidmcp run server.py:my_app    # explicit object name
rapidmcp run server.py --port 8080
rapidmcp version
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
| Middleware (`on_tool_call` chain) | ✅ |
| Server mounting / composition | ✅ |
| CLI (`rapidmcp run server.py`) | ✅ |
| Token authentication (`auth=` callable, sync or async) | ✅ |
| TLS / mTLS (`TLSConfig`, `ClientTLSConfig`) | ✅ |
| LangChain / LangGraph integration | ✅ |
| LiveKit integration | ✅ |

## Benchmark: RapidMCP vs FastMCP (Streamable HTTP)

A latency benchmark comparing RapidMCP (gRPC) against [FastMCP](https://gofastmcp.com/) (Streamable HTTP). Both servers run the same `echo` tool — the difference is purely transport overhead.

```bash
cd benchmark
uv sync
uv run python run_benchmark.py
```

### Results (Windows 11, loopback, 1000 sequential calls)

```
Transport             p50      p95      p99      min      max     mean    stdev
-------------------------------------------------------------------------------
RapidMCP (gRPC)    0.55ms    0.70ms    0.81ms    0.42ms    1.18ms    0.58ms    0.09ms
FastMCP (HTTP)      9.68ms   14.06ms   18.22ms    7.59ms   35.72ms   10.40ms    3.20ms
```

**RapidMCP is ~17x faster at p50 and ~22x faster at p99.**

## Project structure

```
mcp-grpc/
├── proto/mcp.proto              ← Protocol definition (single source of truth)
├── python/
│   ├── src/rapidmcp/
│   │   ├── server.py            ← RapidMCP, mount(), decorators
│   │   ├── _servicer.py         ← gRPC session handler
│   │   ├── client.py            ← Client, sampling/elicitation/roots handlers
│   │   ├── context.py           ← Context (injected per tool call)
│   │   ├── middleware.py        ← Middleware chain + built-ins
│   │   ├── elicitation.py       ← BoolField, IntField, StringField, …
│   │   ├── cli.py               ← `rapidmcp run` entry point
│   │   ├── tools/               ← ToolManager, ToolAnnotations
│   │   ├── resources/           ← ResourceManager, URI template matching
│   │   ├── prompts/             ← PromptManager
│   │   └── integrations/
│   │       ├── langchain.py     ← MCPToolkit for LangChain / LangGraph
│   │       └── livekit.py       ← MCPServerGRPC for livekit-agents
│   └── tests/                   ← 230 tests (unit + integration + Docker TLS)
└── benchmark/                   ← Latency harness vs FastMCP HTTP
```

## Design

- **One service, one bidi streaming RPC.** `Session(stream ClientEnvelope) returns (stream ServerEnvelope)` carries all messages over a single persistent connection.
- **Write-queue servicer.** Concurrent reader/writer tasks per session. Enables server push notifications, mid-handler sampling/elicitation, and concurrent tool execution.
- **Context as explicit DI.** Tool handlers that declare `ctx: Context` get capabilities injected per-call — no ContextVar magic.
- **Middleware chain.** `functools.partial` reversed-registration chain. First-registered middleware is outermost.
- **Protobuf envelopes with `oneof`.** Each envelope carries a `request_id` and one message type. Correlation is handled transparently via `PendingRequests`.

## Development

```bash
cd python
uv sync --extra dev    # install all deps
uv run pytest -v       # run 230 tests
uv run ruff check      # lint
uv run ruff format     # format
```

## License

MIT
