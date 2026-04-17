# mcp-test

End-to-end test project for [mcp-grpc](../mcp-grpc) — a gRPC-native MCP library.

Two containers:

- **mcp-server** — a `FasterMCP` server that exercises the full MCP spec surface
- **backend** — a FastAPI app that connects to the server via the `MCPToolkit` LangChain adapter and exposes an HTTP API

```
                        Docker network (mcp-net)
┌─────────────┐  gRPC :50051  ┌──────────────────────────────┐
│  mcp-server │ ◄─────────── │  backend (FastAPI + LangChain) │
│  port 50051 │               │  port 8000                    │
└─────────────┘               └──────────────────────────────┘
                                          │
                                     HTTP :8000
                                          │
                                       (you)
```

## Prerequisites

- Docker + Docker Compose
- An Anthropic API key
- `mcp-grpc` wheel built in `../mcp-grpc/python/dist/` — build it once from the mcp-grpc repo:

```powershell
cd ..\mcp-grpc\python
uv build
```

Rebuild whenever you change mcp-grpc source. This project treats it as a library.

## Setup

```powershell
cd mcp-test
echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" > .env
```

## Run

```powershell
docker compose up --build
```

The wheel is installed into both images at build time as a cached Docker layer.

---

## Testing

The easiest way to test interactively is the Swagger UI at **http://localhost:8000/docs**.

For command-line testing on Windows, use `curl.exe` (built into Windows 10/11) and save request bodies to files to avoid PowerShell quoting issues:

```powershell
# Write a body file once, reuse it
'{"message": "your prompt here"}' | Out-File -Encoding utf8 -NoNewline body.json
curl.exe -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "@body.json"
```

---

### Health check

Verifies the backend is running and the gRPC connection to the MCP server is live.

```
GET /health
```

Expected:
```json
{"status": "ok", "mcp_server": "mcp-server:50051"}
```

---

### Tools

Lists all tools registered on the MCP server, including their input schemas.

```
GET /tools
```

Expected: 7 tools — `add`, `echo`, `confirm_action`, `summarize_with_llm`, `long_running_task`, `log_demo`, `fail_tool`.

---

### Resources

#### List resources and templates

```
GET /resources
```

Expected: 2 static resources (`res://server-info`, `res://config`) and 1 template (`res://items/{item_id}`).

#### Read server info (static resource)

```
GET /resources/res://server-info
```

Expected:
```json
{
  "uri": "res://server-info",
  "content": [{"type": "text", "text": "{\"name\": \"mcp-test-server\", \"version\": \"0.1.0\", \"timestamp\": \"...\"}"}]
}
```

#### Read config (static resource)

```
GET /resources/res://config
```

Expected:
```json
{
  "uri": "res://config",
  "content": [{"type": "text", "text": "{\"debug\": true, \"max_retries\": 3, \"timeout_seconds\": 30}"}]
}
```

#### Read item by ID (resource template)

The `{item_id}` segment is extracted from the URI and passed to the handler.

```
GET /resources/res://items/42
GET /resources/res://items/hello
GET /resources/res://items/some-slug
```

Expected for `42`:
```json
{
  "uri": "res://items/42",
  "content": [{"type": "text", "text": "{\"id\": \"42\", \"name\": \"Item 42\", \"status\": \"active\"}"}]
}
```

---

### Prompts

#### List prompts

```
GET /prompts
```

Expected: 1 prompt — `greet` — with arguments `name` (required) and `style` (optional).

#### Render a prompt — default style

```
GET /prompts/greet?name=Alice
```

Expected:
```json
{
  "name": "greet",
  "messages": [{"role": "assistant", "text": "Dear Alice, I hope this message finds you well."}]
}
```

#### Render a prompt — pirate style

```
GET /prompts/greet?name=Alice&style=pirate
```

Expected:
```json
{
  "name": "greet",
  "messages": [{"role": "assistant", "text": "Ahoy, Alice! Shiver me timbers!"}]
}
```

#### All available styles

| `style` | Output |
|---|---|
| `formal` (default) | `Dear {name}, I hope this message finds you well.` |
| `casual` | `Hey {name}! What's up?` |
| `pirate` | `Ahoy, {name}! Shiver me timbers!` |
| `shakespearean` | `Hark! {name}, thou art most welcome.` |

---

### Chat — agent tool calls

The `/chat` endpoint runs a LangChain agent (`claude-sonnet-4-6`) with all MCP tools available. The agent decides which tool to call based on your message.

All examples use the body-file pattern:

```powershell
'{"message": "..."}' | Out-File -Encoding utf8 -NoNewline body.json
curl.exe -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "@body.json"
```

#### Simple math — `add` tool

```
message: "What is 1234 plus 5678?"
```

What happens: agent calls `add(a=1234, b=5678)` → server returns `"6912"`.

Expected response: `"1234 + 5678 = 6912"`

#### Echo — `echo` tool

```
message: "Echo back the phrase: hello world"
```

What happens: agent calls `echo(text="hello world")` → server returns `"hello world"`.

#### Elicitation — `confirm_action` tool

```
message: "Use confirm_action to confirm deleting old backups"
```

What happens:
1. Agent calls `confirm_action(action="deleting old backups")`
2. Server calls `ctx.elicit()` mid-tool, asking the client to confirm
3. Backend mock handler immediately responds `accept + confirm=true`
4. Server resumes and returns `"Action confirmed and executed: deleting old backups"`

Expected response: the agent reports the action was confirmed and executed.

> The elicitation roundtrip is visible in the mcp-server logs:
> `DEBUG fastermcp.server — session … ← elicitation rid=N`

#### Sampling — `summarize_with_llm` tool

```
message: "Summarize this text using summarize_with_llm: The quick brown fox jumps over the lazy dog near the riverbank at sunset while birds sing overhead"
```

What happens:
1. Agent calls `summarize_with_llm(text="...")`
2. Server calls `ctx.sample()` — sends the text to the client asking for a summary
3. Backend mock handler returns `"Mock summary: The quick brown fox..."`
4. Server returns the mock summary to the agent

Expected response: the agent relays the mock summary.

#### Progress reporting — `long_running_task` tool

```
message: "Run a long_running_task with 5 steps"
```

What happens: server loops 5 times, calling `ctx.report_progress(i, 5)` and sleeping 0.2s each iteration. Takes ~1 second.

Expected response: `"Completed 5 steps"`

> Watch mcp-server logs to see timing middleware output after the tool finishes.

#### Logging — `log_demo` tool

```
message: "Run the log_demo tool"
```

What happens: server emits `ctx.debug`, `ctx.info`, `ctx.warning`, `ctx.error` — all four log levels sent to the client over the gRPC stream.

Expected response: `"All four log levels emitted"`

> Watch the backend logs to see all four log level messages arrive from the server.

#### ToolError — `fail_tool`

```
message: "Call fail_tool and tell me what happened"
```

What happens:
1. Agent calls `fail_tool()`
2. Server raises `ToolError("This tool always fails on purpose")`
3. MCP returns `is_error=True` result — this is a valid MCP response, not an exception
4. Adapter converts it to `"Error: This tool always fails on purpose"` and returns it to the agent
5. Agent reads the error and responds accordingly

Expected response: the agent explains that the tool failed with the given message.

> **Key distinction:** `ToolError` is a protocol-level error result (`is_error=True`), not a crash. The agent sees it as tool output and can reason about it.

---

## Watching logs

Run logs for both containers side by side to see the full request flow:

```powershell
docker compose logs -f
```

Or per service:

```powershell
docker compose logs -f mcp-server
docker compose logs -f backend
```

On a `/chat` call you'll see:
- **backend**: LangGraph agent steps, tool invocations
- **mcp-server**: `LoggingMiddleware` (`tool=X is_error=Y`) and `TimingMiddleware` (`X completed in N ms`) for every tool dispatch

---

## MCP spec coverage

| Feature | Where | How |
|---|---|---|
| Simple tools | `add`, `echo` | Basic input → output, `read_only=True` annotation |
| Elicitation | `confirm_action` | `ctx.elicit()` with `BoolField`; mock client always accepts |
| Sampling | `summarize_with_llm` | `ctx.sample()` — server asks client LLM; mock returns canned text |
| Progress | `long_running_task` | `ctx.report_progress(i, total)` in a loop |
| Logging | `log_demo` | `ctx.debug/info/warning/error()` — all four levels |
| ToolError | `fail_tool` | `raise ToolError(...)` → `is_error=True` result |
| Static resources | `res://server-info`, `res://config` | `@server.resource(uri)` |
| Resource templates | `res://items/{item_id}` | `@server.resource_template(uri_template)` |
| Prompts | `greet` | `@server.prompt()` with required + optional args |
| Completions | `greet` → `style` | `@server.completion("greet")` prefix-matches style values |
| Middleware | all tools | `TimingMiddleware` + `LoggingMiddleware` on every call |

---

## Architecture notes

**Elicitation and sampling handlers must be registered before `connect()`.**
The gRPC handshake sends `ClientCapabilities` during `initialize`, locking in whether sampling/elicitation are supported. The backend lifespan registers mock handlers before calling `toolkit.client.connect()`.

**`MCPToolkit` wraps tools only.**
The LangChain adapter exposes tools to the agent. Resources, prompts, and completions are accessed directly via `toolkit.client` in the REST endpoints.

**mcp-grpc is treated as a library.**
Both Dockerfiles set their build context to `..` so they can `COPY mcp-grpc/python/dist/` and install the pre-built wheel via `uv pip install --system`. The test project never builds mcp-grpc itself.

---

## Project structure

```
mcp-test/
├── docker-compose.yml
├── .env                     # ANTHROPIC_API_KEY (git-ignored)
├── .gitignore
├── mcp-server/
│   ├── Dockerfile
│   └── server.py            # All tools, resources, prompts in one file
└── backend/
    ├── Dockerfile
    └── app.py               # FastAPI + LangChain agent + REST endpoints
```
