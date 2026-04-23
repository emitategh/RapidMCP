# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Python (`rapidmcp`)

### [0.4.0] - 2026-04-20

### Added
- **`RapidMCPClient` multi-server LangChain adapter** — mirrors the `MultiServerMCPClient` shape from `langchain-mcp-adapters`. One client fans out across any number of RapidMCP gRPC servers and aggregates tools, prompts, and resources:
  ```python
  from rapidmcp.integrations.langchain import RapidMCPClient
  async with RapidMCPClient({
      "docs": {"address": "docs:50051"},
      "sql":  {"address": "sql:50051", "token": "...", "allowed_tools": ["query"]},
  }) as rc:
      tools = await rc.get_tools()
      prompt = await rc.get_prompt("docs", "summarise", arguments={"topic": "grpc"})
      blobs = await rc.get_resources("docs", uris=["file:///readme.md"])
  ```
- **LiveKit integration parity with `MCPServerHTTP`** (`livekit-agents` 1.5.2):
  - `tool_result_resolver` kwarg on `MCPServerGRPC.__init__` — forwarded to the base class; custom resolvers receive an `MCPToolResultContext` with a real `mcp.types.CallToolResult`
  - New `_to_mcp_call_result` helper converts `rapidmcp.types.CallToolResult` → `mcp.types.CallToolResult`, covering `TextContent`, `ImageContent`, `AudioContent`, `EmbeddedResource` (blob + text), and `ResourceLink`
  - Unknown content types surface as a `TextContent` placeholder and a `WARNING` log — never silently dropped
- 15 functional tests for the LiveKit adapter (`tests/test_integrations_livekit.py`): cache-hit, init race, error stringification, custom resolver, multi-content, embedded resources (blob + text), unknown content type, empty-content resolver, `allowed_tools` filter, `client_streams()` contract, pre-init/post-aclose guards
- `livekit-agents[mcp]>=0.8` now required by the `livekit` extra; also added to the `dev` extra so the test suite runs in CI

### Changed
- **BREAKING**: The legacy `MCPToolkit` LangChain adapter has been removed in favor of `RapidMCPClient`. Migration:
  ```python
  # before
  toolkit = MCPToolkit(address="localhost:50051")
  tools = await toolkit.get_tools()
  # after
  async with RapidMCPClient({"my_server": {"address": "localhost:50051"}}) as rc:
      tools = await rc.get_tools()
  ```
- `MCPServerGRPC.list_tools` now honors `_cache_dirty` / `_lk_tools` so `MCPToolset.invalidate_cache()` actually refreshes
- `MCPServerGRPC.initialize()` is concurrency-safe (guarded by `asyncio.Lock` with double-checked `_connected`)
- `MCPServerGRPC.aclose()` is idempotent (short-circuits when already disconnected)
- `client_streams()` raises `NotImplementedError` synchronously (was deferred to `__aenter__` via `@asynccontextmanager`)

### Fixed
- `MCPServerGRPC` error messages now surface non-text content as `[image: mime, N bytes]` / `[audio: ...]` / `[resource: uri]` placeholders — previously silently dropped
- `MCPServerGRPC.list_tools` raises `RuntimeError("isn't initialized")` when called before `initialize()`, matching the base-class contract
- `MCPServerGRPC` tool invocations raise `ToolError("internal service is unavailable …")` when `_connected` is `False` (post-`aclose`), matching the base-class contract
- `list_tools` info log now filters names by `allowed_tools` so the reported count and names agree

### [0.3.2] - 2026-04-14

### Changed
- Version bump for PyPI release

### [0.3.1] - 2026-04-13

### Added
- PyPI README feature table expanded to cover all 22 MCP spec features: Completions, Roots, Progress, Capability negotiation, Ping/Pong, LangChain and LiveKit integrations

### Fixed
- `license` field updated to SPDX string format (`"MIT"`) — removes setuptools deprecation warning
- Removed redundant `License :: OSI Approved :: MIT License` classifier (superseded by SPDX)

### Changed
- Added `MANIFEST.in` to exclude `tests/` and `benchmark/` from the sdist (`.tar.gz`)

### [0.3.0] - 2026-04-12

### Added
- Docker-based TLS test suite: TLS happy path, TLS rejection (wrong CA, insecure client), mTLS (valid client cert, missing client cert), and combined TLS + token auth
- PKI fixture for generating test certificates in Docker test helpers
- `cryptography` dev dependency for TLS test certificate generation

### [0.2.0] - 2026-04-11

### Added
- **Authentication**: bearer token auth via `token=` param on `RapidMCP` server; `_AuthInterceptor` validates `Authorization: Bearer <token>` headers
- **TLS / mTLS**: `TLSConfig` on the server side; `ClientTLSConfig` on the client side; both exported from `rapidmcp` public API
- `token=` and `tls=` params on `Client` for secure connections
- `token=` and `tls=` params on `MCPServerGRPC` (LiveKit) and `MCPToolkit` (LangChain) integrations
- Resource template matching and client lifecycle fixes
- `Client` now returns parsed Python types instead of raw protobuf objects

### Changed
- Package renamed from `mcp_grpc` → `fastermcp` → **`rapidmcp`** (final name)
- `MCPServerGRPC` refactored to match the `livekit-agents` `MCPServer` interface

### Fixed
- Token guard uses `is not None` check (previously falsy tokens could bypass auth)
- `auth.py`: case-insensitive `Bearer` prefix, `isawaitable`, removed field defaults
- gRPC deadlock replaced reader/writer tasks with `asyncio.wait` race loop

### [0.1.0] - 2026-04-10

### Added
- Core gRPC-native MCP transport: single bidirectional streaming RPC (`Session`) over protobuf — ~17x lower latency than FastMCP Streamable HTTP
- Full MCP spec parity: tools, resources, resource templates, prompts, completions, pagination, sampling, elicitation, logging, progress, notifications (bidirectional), cancellation, resource subscribe, roots, capability negotiation, ping/pong
- **Middleware system**: `Middleware` base class, `ToolCallContext`, `functools.partial` chain; built-ins: `TimingMiddleware`, `LoggingMiddleware`, `TimeoutMiddleware`, `ValidationMiddleware`
- **Server composition**: `main.mount(sub, prefix="x")` merges tools/resources/prompts with optional prefix
- **CLI**: `rapidmcp run server.py` / `rapidmcp run server.py:my_app` / `rapidmcp version`
- **LiveKit integration**: `MCPServerGRPC` adapter for `livekit-agents`
- **Elicitation helpers**: `BoolField`, `IntField`, `StringField`, `EnumField`, `FloatField`, `build_elicitation_schema`
- **Content helpers**: `Audio`, `Image` for binary content in tool responses
- `Context` as explicit dependency injection per tool call
- `InProcessChannel` for unit testing without a running server
- `McpError` / `ToolError` error types

---

## TypeScript (`@emitate/rapidmcp`)

### [ts-0.2.0] - 2026-04-20

### Added
- **`RapidMCPClient` multi-server LangChain adapter** — mirrors the `MultiServerMCPClient` shape from `@langchain/mcp-adapters`. One client fans out across any number of RapidMCP gRPC servers and aggregates tools, prompts, and resources:
  ```typescript
  import { RapidMCPClient } from "@emitate/rapidmcp/integrations/langchain";
  const rc = new RapidMCPClient({
    docs: { address: "localhost:50051" },
    sql:  { address: "localhost:50052", token: "...", allowedTools: ["query"] },
  });
  await rc.connect();
  const tools = await rc.getTools();
  ```
- `getResources(serverName, { uris? })` — read one or more resources, returns each as `{ data, mimeType, metadata, asString(), asBytes() }`
- `getPrompt(serverName, promptName, args)` — render a prompt to a LangChain-style message list

### Changed
- **BREAKING**: The legacy `MCPToolkit` single-server adapter has been **removed** in favor of `RapidMCPClient`. Migration:
  ```typescript
  // before
  const toolkit = new MCPToolkit({ address: "localhost:50051" });
  const tools = await toolkit.getTools();
  // after
  const rc = new RapidMCPClient({ myServer: { address: "localhost:50051" } });
  await rc.connect();
  const tools = await rc.getTools();
  ```

### [ts-0.1.4] - 2026-04-15

### Fixed
- Replaced `(schema as any).toJSONSchema()` cast with Zod v4 standalone `toJSONSchema()` function in `ToolManager`

### Removed
- Unused `zod-to-json-schema` direct dependency (still available transitively via `@langchain/core`)

### [ts-0.1.3] - 2026-04-14

### Fixed
- Added `@bufbuild/protobuf` as direct dependency

### [ts-0.1.2] - 2026-04-14

### Changed
- Version bump, barrel export updates

### [ts-0.1.1] - 2026-04-14

### Added
- Server integration tests
- Updated barrel exports

### [ts-0.1.0] - 2026-04-13

### Added
- TypeScript server with full feature parity: tools, resources, prompts, middleware, context, auth, session management
- `Client` with sampling, elicitation, and roots support
- `ToolManager`, `ResourceManager`, `PromptManager`
- TLS/mTLS auth helpers
- **LangChain integration**: `MCPToolkit` adapter
- 103 tests passing

---

<!-- Python links -->
[0.4.0]: https://github.com/emitategh/RapidMCP/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/emitategh/RapidMCP/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/emitategh/RapidMCP/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/emitategh/RapidMCP/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/emitategh/RapidMCP/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/emitategh/RapidMCP/releases/tag/v0.1.0

<!-- TypeScript links -->
[ts-0.2.0]: https://github.com/emitategh/RapidMCP/compare/ts-v0.1.4...ts-v0.2.0
[ts-0.1.4]: https://github.com/emitategh/RapidMCP/compare/ts-v0.1.3...ts-v0.1.4
[ts-0.1.3]: https://github.com/emitategh/RapidMCP/compare/ts-v0.1.2...ts-v0.1.3
[ts-0.1.2]: https://github.com/emitategh/RapidMCP/compare/ts-v0.1.1...ts-v0.1.2
[ts-0.1.1]: https://github.com/emitategh/RapidMCP/compare/ts-v0.1.0...ts-v0.1.1
[ts-0.1.0]: https://github.com/emitategh/RapidMCP/releases/tag/ts-v0.1.0
