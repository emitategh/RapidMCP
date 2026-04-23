# RapidMCP

> Experimental. This project is a proof-of-concept exploring gRPC as an MCP transport.

gRPC-native [MCP (Model Context Protocol)](https://modelcontextprotocol.io) library for Python. Uses protobuf over a persistent bidirectional gRPC stream instead of JSON-RPC over HTTP — **~17x lower latency** than FastMCP Streamable HTTP on local connections.

```
pip install rapidmcp
```

## Why gRPC?

Standard MCP transports (SSE, Streamable HTTP) open a new HTTP connection per tool call. RapidMCP keeps one persistent bidirectional stream open for the entire session — tool calls, sampling, elicitation, notifications, and progress all flow over the same connection.

## Quick start

### Server

```python
from rapidmcp import RapidMCP, Context

server = RapidMCP(name="my-server", version="1.0.0")

@server.tool(description="Add two numbers")
async def add(a: float, b: float) -> str:
    return str(a + b)

@server.tool(description="Confirm an action before running it")
async def confirm_delete(path: str, ctx: Context) -> str:
    from rapidmcp import BoolField
    result = await ctx.elicit(
        message=f"Delete {path}?",
        fields={"confirm": BoolField(title="Confirm deletion")},
    )
    if result.accepted and result.data.get("confirm"):
        return f"Deleted {path}"
    return "Cancelled"

@server.resource(uri="res://config", description="Server config")
async def config() -> str:
    return '{"version": "1.0.0"}'

@server.resource_template("res://files/{path}", description="Read a file")
async def read_file(path: str) -> str:
    return open(path).read()

@server.prompt(description="Generate a greeting")
async def greet(name: str, style: str = "formal") -> str:
    return f"Hello, {name}!" if style == "casual" else f"Dear {name},"

if __name__ == "__main__":
    server.run(port=50051)
```

### Client

```python
import asyncio
from rapidmcp import Client

async def main():
    async with Client("localhost:50051") as client:
        # Tools
        result = await client.call_tool("add", {"a": 1, "b": 2})
        print(result.content[0].text)  # "3.0"

        # Resources
        tools = await client.list_tools()
        resource = await client.read_resource("res://config")

        # Prompts
        prompt = await client.get_prompt("greet", {"name": "Alice"})

asyncio.run(main())
```

### LangChain integration

`RapidMCPClient` mirrors the `MultiServerMCPClient` shape from
`langchain-mcp-adapters` — one client fans out across any number of
RapidMCP gRPC servers and aggregates tools, prompts, and resources.

```python
from rapidmcp.integrations.langchain import RapidMCPClient
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

async with RapidMCPClient({
    "docs": {"address": "localhost:50051"},
    "sql":  {"address": "localhost:50052", "token": "...", "allowed_tools": ["query"]},
}) as rc:
    tools  = await rc.get_tools()                                   # aggregated across servers
    prompt = await rc.get_prompt("docs", "summarise", arguments={"topic": "grpc"})
    blobs  = await rc.get_resources("docs", uris=["file:///readme.md"])
    async with rc.session("sql") as sess:
        await sess.ping()

    agent  = create_react_agent(ChatAnthropic(model="claude-sonnet-4-6"), tools)
    result = await agent.ainvoke({"messages": [("user", "Add 17 and 25")]})
```

```
pip install 'rapidmcp[langchain]'
```

### LiveKit integration

```python
from rapidmcp.integrations.livekit import MCPServerGRPC
from livekit.agents import AgentSession, mcp

session = AgentSession(
    tools=[
        mcp.MCPToolset(
            id="grpc-tools",
            mcp_server=MCPServerGRPC(address="mcp-server:50051"),
        ),
    ],
)
```

```
pip install 'rapidmcp[livekit]'
```

## Authentication

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

Client:

```python
async with Client("localhost:50051", token="secret") as client:
    ...
```

## TLS / mTLS

```python
from rapidmcp import RapidMCP, TLSConfig, Client, ClientTLSConfig

# Server-only TLS
server = RapidMCP(name="my-server", version="1.0.0", tls=TLSConfig(cert="server.crt", key="server.key"))

# Mutual TLS
server = RapidMCP(name="my-server", version="1.0.0", tls=TLSConfig(cert="server.crt", key="server.key", ca="ca.crt"))

# Client with custom CA
async with Client("localhost:50051", tls=ClientTLSConfig(ca="ca.crt")) as client: ...

# Client mTLS
async with Client("localhost:50051", tls=ClientTLSConfig(ca="ca.crt", cert="client.crt", key="client.key")) as client: ...

# TLS + token
async with Client("localhost:50051", tls=ClientTLSConfig(ca="ca.crt"), token="secret") as client: ...
```

## Full MCP spec coverage

| Feature | API |
|---|---|
| Tools (list, call) | `@server.tool()`, `ToolError` |
| Resources (list, read, subscribe) | `@server.resource()`, `@server.resource_template()` |
| Resource templates | `@server.resource_template("res://items/{id}")` |
| Prompts (list, get) | `@server.prompt()` |
| Completions | `@server.completion("prompt_name")` |
| Pagination | cursor-based on all list endpoints |
| Sampling | `ctx.sample()` — server requests LLM completion from client |
| Elicitation | `ctx.elicit()` — server requests user input from client |
| Roots | `ctx.list_roots()` |
| Logging | `ctx.info/debug/warning/error()` |
| Progress | `ctx.report_progress(current, total)` |
| Notifications | bidirectional push, resource subscribe |
| Cancellation | in-flight request cancellation |
| Capability negotiation | automatic on session handshake |
| Ping / Pong | `client.ping()` |
| Middleware | `TimingMiddleware`, `LoggingMiddleware`, `TimeoutMiddleware`, `ValidationMiddleware` |
| Server composition | `server.mount(sub, prefix="x")` |
| CLI | `rapidmcp run server.py` |
| Token authentication | `auth=` callable (sync or async), bearer token |
| TLS / mTLS | `TLSConfig`, `ClientTLSConfig` |
| LangChain / LangGraph | `rapidmcp.integrations.langchain.RapidMCPClient` (multi-server) |
| LiveKit | `rapidmcp.integrations.livekit.MCPServerGRPC` |

## Middleware

```python
from rapidmcp import RapidMCP, TimingMiddleware, LoggingMiddleware, TimeoutMiddleware

server = RapidMCP(
    name="my-server",
    version="1.0.0",
    middleware=[
        TimingMiddleware(),
        LoggingMiddleware(),
        TimeoutMiddleware(default_timeout=30.0),
    ],
)
```

## CLI

```bash
rapidmcp run server.py           # run server.py:server (auto-detected)
rapidmcp run server.py:my_app   # explicit app name
rapidmcp version
```

## Requirements

- Python 3.10+
- `grpcio >= 1.57.0`
- `protobuf >= 4.24.0`

## License

MIT
