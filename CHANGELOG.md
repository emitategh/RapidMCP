# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-04-13

### Added
- PyPI README feature table expanded to cover all 22 MCP spec features: Completions, Roots, Progress, Capability negotiation, Ping/Pong, LangChain and LiveKit integrations

### Fixed
- `license` field updated to SPDX string format (`"MIT"`) — removes setuptools deprecation warning
- Removed redundant `License :: OSI Approved :: MIT License` classifier (superseded by SPDX)

### Changed
- Added `MANIFEST.in` to exclude `tests/` and `benchmark/` from the sdist (`.tar.gz`)

## [0.3.0] - 2026-04-12

### Added
- Docker-based TLS test suite: TLS happy path, TLS rejection (wrong CA, insecure client), mTLS (valid client cert, missing client cert), and combined TLS + token auth
- PKI fixture for generating test certificates in Docker test helpers
- `cryptography` dev dependency for TLS test certificate generation

## [0.2.0] - 2026-04-11

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

## [0.1.0] - 2026-04-10

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

[0.3.1]: https://github.com/emitategh/FasterMcp/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/emitategh/FasterMcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/emitategh/FasterMcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/emitategh/FasterMcp/releases/tag/v0.1.0
