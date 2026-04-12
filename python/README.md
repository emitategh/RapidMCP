# FasterMCP

> **Experimental.** This project is a proof-of-concept exploring gRPC as an MCP transport. The API may change without notice and it is not recommended for production use.

gRPC-native [MCP (Model Context Protocol)](https://modelcontextprotocol.io) library for Python. Uses protobuf over a persistent bidirectional gRPC stream instead of JSON-RPC over HTTP — **~17x lower latency** than FastMCP Streamable HTTP on local connections.

```
pip install fastermcp
```

## Why gRPC?

Standard MCP transports (SSE, Streamable HTTP) open a new HTTP connection per tool call. FasterMCP keeps one persistent bidirectional stream open for the entire session — tool calls, sampling, elicitation, notifications, and progress all flow over the same connection.

## Quick start

### Server

```python
from fastermcp import FasterMCP, Context

server = FasterMCP(name="my-server", version="1.0.0")

@server.tool(description="Add two numbers")
async def add(a: float, b: float) -> str:
    return str(a + b)

@server.tool(description="Confirm an action before running it")
async def confirm_delete(path: str, ctx: Context) -> str:
    from fastermcp import BoolField
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
from fastermcp import Client

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

```python
from fastermcp.integrations.langchain import MCPToolkit
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

async with MCPToolkit("localhost:50051") as toolkit:
    tools = await toolkit.aget_tools()
    agent = create_react_agent(ChatAnthropic(model="claude-sonnet-4-6"), tools)
    result = await agent.ainvoke({"messages": [("user", "Add 17 and 25")]})
```

```
pip install 'fastermcp[langchain]'
```

### LiveKit integration

```python
from livekit.agents.llm.mcp import MCPToolset
from fastermcp.integrations.livekit import MCPServerGRPC

session = AgentSession(
    tools=[MCPToolset(id="tools", mcp_server=MCPServerGRPC(address="localhost:50051"))],
)
```

```
pip install 'fastermcp[livekit]'
```

## Full MCP spec coverage

| Feature | API |
|---|---|
| Tools | `@server.tool()`, `ctx.report_progress()`, `ToolError` |
| Resources | `@server.resource()`, `@server.resource_template()` |
| Prompts | `@server.prompt()`, `@server.completion()` |
| Sampling | `ctx.sample()` — server requests LLM completion from client |
| Elicitation | `ctx.elicit()` — server requests user input from client |
| Logging | `ctx.info/debug/warning/error()` |
| Notifications | bidirectional push, resource subscribe |
| Middleware | `TimingMiddleware`, `LoggingMiddleware`, `TimeoutMiddleware`, `ValidationMiddleware` |
| Pagination | cursor-based on all list endpoints |
| Cancellation | in-flight request cancellation |
| Server composition | `server.mount(sub, prefix="x")` |
| CLI | `fastermcp run server.py` |

## Middleware

```python
from fastermcp import FasterMCP, TimingMiddleware, LoggingMiddleware, TimeoutMiddleware

server = FasterMCP(
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
fastermcp run server.py           # run server.py:server (auto-detected)
fastermcp run server.py:my_app   # explicit app name
fastermcp version
```

## Requirements

- Python 3.10+
- `grpcio >= 1.57.0`
- `protobuf >= 4.24.0`

## License

MIT
