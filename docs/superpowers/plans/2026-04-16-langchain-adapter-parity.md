# LangChain Adapter Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace RapidMCP's `MCPToolkit` with a `RapidMCPClient` that matches `langchain-mcp-adapters`' `MultiServerMCPClient` surface — supporting multi-server aggregation, resources, prompts, and full multimodal content handling in both Python and TypeScript. No backward compatibility is kept: `MCPToolkit` is deleted in both languages.

**Architecture:** Introduce `RapidMCPClient` that accepts a `{server_name: config}` dict and exposes `get_tools`, `get_resources`, `get_prompt`, `session`. Delete the old single-server `MCPToolkit` class and all of its tests. TypeScript mirrors the Python shape.

**Tech Stack:** Python (`rapidmcp`, `langchain-core`, `pydantic`, `pytest`), TypeScript (`@langchain/core`, `vitest`).

**Scope out:** Tool-call interceptors, progress/logging/elicitation callback wiring at the toolkit level — those are a separate plan.

---

## File Structure

**Python — `python/src/rapidmcp/integrations/langchain.py`** (full rewrite)
- `_ServerConfig` — per-server config dataclass (`address`, `token`, `tls`, `allowed_tools`)
- `_json_schema_to_model` — unchanged helper
- `_convert_result` — unchanged helper (already handles multimodal)
- `_read_resource_to_blob` — new: MCP resource content → LangChain `Blob`
- `_get_prompt_to_messages` — new: `GetPromptResult` → list of LangChain `BaseMessage`
- `_make_tool` — unchanged helper
- `RapidMCPClient` — the only public class (multi-server)

**Python — `python/tests/test_integrations_langchain.py`** (new file)
- Unit tests mocking `Client`
- Multi-server config + tool aggregation + `allowed_tools` + resources + prompts + session

**Python — `python/tests/test_integrations_auth.py`** (modify)
- Delete the four `test_mcp_toolkit_*` auth tests
- Add `test_rapidmcp_client_forwards_per_server_token_and_tls`

**Python — `CLAUDE.md`** (modify)
- Update Public API snippet to reference `RapidMCPClient` only
- Add a concise usage section

**TypeScript — `typescript/src/integrations/langchain.ts`** (full rewrite)
- `RapidMCPClient` — the only public class
- `convertResult` — exported; now preserves image/audio/resource content
- Old `MCPToolkit` class is deleted

**TypeScript — `typescript/tests/integrations.langchain.test.ts`** (new file)
- Vitest suite mirroring Python unit tests

---

## Phase 1 — Python: `RapidMCPClient` skeleton + multi-server tools

### Task 1: Scaffold test file and failing smoke test

**Files:**
- Create: `python/tests/test_integrations_langchain.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for the LangChain integration — mock the Client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("langchain_core")


def test_rapidmcp_client_importable():
    from rapidmcp.integrations.langchain import RapidMCPClient  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: FAIL — `ImportError: cannot import name 'RapidMCPClient'`.

- [ ] **Step 3: Add stub class to make the import succeed**

Append to `python/src/rapidmcp/integrations/langchain.py` (leave the old `MCPToolkit` alone for now — it will be deleted in Task 9):

```python
class RapidMCPClient:
    """Multi-server LangChain adapter for RapidMCP gRPC servers.

    Mirrors ``langchain_mcp_adapters.client.MultiServerMCPClient``'s surface
    over gRPC. Accepts a mapping of ``{server_name: {address, token, tls,
    allowed_tools}}`` and aggregates tools/resources/prompts across servers.
    """

    def __init__(self) -> None:
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: PASS (import succeeds).

- [ ] **Step 5: Commit**

```bash
rtk git add python/tests/test_integrations_langchain.py python/src/rapidmcp/integrations/langchain.py
rtk git commit -m "test(langchain): scaffold RapidMCPClient import smoke test"
```

---

### Task 2: `RapidMCPClient.__init__` accepts multi-server dict

**Files:**
- Modify: `python/src/rapidmcp/integrations/langchain.py`
- Modify: `python/tests/test_integrations_langchain.py`

- [ ] **Step 1: Write the failing test**

Append to `python/tests/test_integrations_langchain.py`:

```python
def test_rapidmcp_client_accepts_multi_server_dict():
    from rapidmcp.auth import ClientTLSConfig
    from rapidmcp.integrations.langchain import RapidMCPClient

    tls = ClientTLSConfig(ca="ca.crt")
    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        client = RapidMCPClient(
            {
                "a": {"address": "host-a:50051", "token": "tok-a"},
                "b": {"address": "host-b:50051", "tls": tls},
            }
        )
        assert set(client.servers) == {"a", "b"}
        assert MockClient.call_args_list[0].args == ("host-a:50051",)
        assert MockClient.call_args_list[0].kwargs == {"token": "tok-a", "tls": None}
        assert MockClient.call_args_list[1].args == ("host-b:50051",)
        assert MockClient.call_args_list[1].kwargs == {"token": None, "tls": tls}


def test_rapidmcp_client_rejects_empty_dict():
    from rapidmcp.integrations.langchain import RapidMCPClient

    with pytest.raises(ValueError, match="at least one server"):
        RapidMCPClient({})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: FAIL — `NotImplementedError` / `AttributeError: 'servers'`.

- [ ] **Step 3: Implement the constructor**

Replace the stub `RapidMCPClient` class:

```python
from dataclasses import dataclass


@dataclass
class _ServerConfig:
    address: str
    token: str | None = None
    tls: ClientTLSConfig | None = None
    allowed_tools: frozenset[str] | None = None


class RapidMCPClient:
    """Multi-server LangChain adapter for RapidMCP gRPC servers.

    Mirrors ``langchain_mcp_adapters.client.MultiServerMCPClient``'s surface
    over gRPC. Accepts a mapping of ``{server_name: {address, token, tls,
    allowed_tools}}`` and aggregates tools/resources/prompts across servers.

    Example::

        async with RapidMCPClient({
            "docs":  {"address": "docs:50051"},
            "sql":   {"address": "sql:50051",  "token": "..."},
        }) as rc:
            tools = await rc.get_tools()
    """

    def __init__(self, servers: dict[str, dict[str, Any]]) -> None:
        if not servers:
            raise ValueError("RapidMCPClient requires at least one server config")
        self._configs: dict[str, _ServerConfig] = {}
        self._clients: dict[str, Client] = {}
        for name, cfg in servers.items():
            sc = _ServerConfig(
                address=cfg["address"],
                token=cfg.get("token"),
                tls=cfg.get("tls"),
                allowed_tools=(
                    frozenset(cfg["allowed_tools"]) if cfg.get("allowed_tools") else None
                ),
            )
            self._configs[name] = sc
            self._clients[name] = Client(sc.address, token=sc.token, tls=sc.tls)

    @property
    def servers(self) -> list[str]:
        """Names of the configured servers, in insertion order."""
        return list(self._configs)

    def client(self, server_name: str) -> Client:
        """Return the underlying :class:`~rapidmcp.client.Client` for one server."""
        try:
            return self._clients[server_name]
        except KeyError as exc:
            raise KeyError(
                f"Unknown server {server_name!r}. Configured: {sorted(self._configs)}"
            ) from exc
```

Also ensure `from typing import Any` is imported at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
rtk git add python/src/rapidmcp/integrations/langchain.py python/tests/test_integrations_langchain.py
rtk git commit -m "feat(langchain): RapidMCPClient constructor + multi-server config"
```

---

### Task 3: Lifecycle — `connect`, `close`, async context manager

**Files:**
- Modify: `python/src/rapidmcp/integrations/langchain.py`
- Modify: `python/tests/test_integrations_langchain.py`

- [ ] **Step 1: Write the failing test**

Append to `python/tests/test_integrations_langchain.py`:

```python
@pytest.mark.asyncio
async def test_rapidmcp_client_connect_closes_all_servers():
    from rapidmcp.integrations.langchain import RapidMCPClient

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()

        rc = RapidMCPClient(
            {"a": {"address": "a:1"}, "b": {"address": "b:1"}}
        )
        await rc.connect()
        await rc.close()

        assert MockClient.return_value.connect.await_count == 2
        assert MockClient.return_value.close.await_count == 2


@pytest.mark.asyncio
async def test_rapidmcp_client_works_as_async_context_manager():
    from rapidmcp.integrations.langchain import RapidMCPClient

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()

        async with RapidMCPClient({"a": {"address": "a:1"}}) as rc:
            assert rc.servers == ["a"]

        MockClient.return_value.connect.assert_awaited_once()
        MockClient.return_value.close.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py::test_rapidmcp_client_connect_closes_all_servers -v`
Expected: FAIL — `AttributeError: 'RapidMCPClient' object has no attribute 'connect'`.

- [ ] **Step 3: Implement lifecycle methods**

Add to `RapidMCPClient`:

```python
    async def connect(self) -> None:
        """Open gRPC streams to every configured server, concurrently."""
        await asyncio.gather(*(c.connect() for c in self._clients.values()))

    async def close(self) -> None:
        """Close every underlying Client, concurrently. Exceptions surface."""
        await asyncio.gather(*(c.close() for c in self._clients.values()))

    async def __aenter__(self) -> RapidMCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
```

Ensure `import asyncio` is at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
rtk git add python/src/rapidmcp/integrations/langchain.py python/tests/test_integrations_langchain.py
rtk git commit -m "feat(langchain): RapidMCPClient lifecycle + async context manager"
```

---

### Task 4: `get_tools()` aggregates from all servers, honours `allowed_tools`

**Files:**
- Modify: `python/src/rapidmcp/integrations/langchain.py`
- Modify: `python/tests/test_integrations_langchain.py`

- [ ] **Step 1: Write the failing test**

Append to `python/tests/test_integrations_langchain.py`:

```python
@pytest.mark.asyncio
async def test_rapidmcp_client_get_tools_aggregates_servers():
    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ListResult, Tool

    def make_list(names):
        return ListResult(
            items=[Tool(name=n, description="", input_schema={"type": "object"}) for n in names],
            next_cursor=None,
        )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        instances = []

        def make_instance(*a, **kw):
            inst = AsyncMock()
            inst.connect = AsyncMock()
            inst.close = AsyncMock()
            inst.list_tools = AsyncMock(
                return_value=make_list(["alpha"] if not instances else ["beta", "gamma"])
            )
            instances.append(inst)
            return inst

        MockClient.side_effect = make_instance

        rc = RapidMCPClient({"a": {"address": "a:1"}, "b": {"address": "b:1"}})
        tools = await rc.get_tools()

        assert sorted(t.name for t in tools) == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_rapidmcp_client_get_tools_respects_allowed_tools_per_server():
    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ListResult, Tool

    listing = ListResult(
        items=[
            Tool(name="keep", description="", input_schema={"type": "object"}),
            Tool(name="drop", description="", input_schema={"type": "object"}),
        ],
        next_cursor=None,
    )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.list_tools = AsyncMock(return_value=listing)

        rc = RapidMCPClient({"a": {"address": "a:1", "allowed_tools": ["keep"]}})
        tools = await rc.get_tools()
        assert [t.name for t in tools] == ["keep"]


@pytest.mark.asyncio
async def test_rapidmcp_client_get_tools_single_server_filter():
    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ListResult, Tool

    def listing(names):
        return ListResult(
            items=[Tool(name=n, description="", input_schema={"type": "object"}) for n in names],
            next_cursor=None,
        )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        instances = []

        def make_instance(*a, **kw):
            inst = AsyncMock()
            inst.connect = AsyncMock()
            inst.close = AsyncMock()
            inst.list_tools = AsyncMock(
                return_value=listing(["x"] if not instances else ["y"])
            )
            instances.append(inst)
            return inst

        MockClient.side_effect = make_instance

        rc = RapidMCPClient({"a": {"address": "a:1"}, "b": {"address": "b:1"}})
        tools = await rc.get_tools(server_name="a")
        assert [t.name for t in tools] == ["x"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: FAIL — `AttributeError: 'RapidMCPClient' object has no attribute 'get_tools'`.

- [ ] **Step 3: Implement `get_tools`**

Add to `RapidMCPClient`:

```python
    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        """Fetch tools from one or all servers.

        Args:
            server_name: If given, only return tools from that server. Otherwise
                tools from every configured server are aggregated.

        Tool-name collisions across servers are NOT deduplicated — configure
        ``allowed_tools`` per server if you expect overlap.
        """
        names = [server_name] if server_name is not None else list(self._configs)
        for n in names:
            if n not in self._configs:
                raise KeyError(f"Unknown server {n!r}")

        results = await asyncio.gather(
            *(self._list_all_tools(n) for n in names)
        )

        lc_tools: list[BaseTool] = []
        for name, mcp_tools in zip(names, results):
            allowed = self._configs[name].allowed_tools
            client = self._clients[name]
            for mcp_tool in mcp_tools:
                if allowed is not None and mcp_tool.name not in allowed:
                    continue
                lc_tools.append(_make_tool(client, mcp_tool))
        return lc_tools

    async def _list_all_tools(self, server_name: str) -> list[Tool]:
        client = self._clients[server_name]
        items: list[Tool] = []
        cursor: str | None = None
        while True:
            result = await client.list_tools(cursor=cursor)
            items.extend(result.items)
            if not result.next_cursor:
                return items
            cursor = result.next_cursor
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: PASS (all tests green).

- [ ] **Step 5: Commit**

```bash
rtk git add python/src/rapidmcp/integrations/langchain.py python/tests/test_integrations_langchain.py
rtk git commit -m "feat(langchain): RapidMCPClient.get_tools with per-server filter"
```

---

## Phase 2 — Python: resources + prompts + session

### Task 5: `get_resources` returns LangChain `Blob` objects

**Files:**
- Modify: `python/src/rapidmcp/integrations/langchain.py`
- Modify: `python/tests/test_integrations_langchain.py`

- [ ] **Step 1: Write the failing test**

Append to `python/tests/test_integrations_langchain.py`:

```python
@pytest.mark.asyncio
async def test_rapidmcp_client_get_resources_reads_listed_uris():
    from langchain_core.document_loaders.blob_loaders import Blob

    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import (
        ContentItem,
        ListResult,
        ReadResourceResult,
        Resource,
    )

    listing = ListResult(
        items=[
            Resource(uri="file:///a.txt", name="a", mime_type="text/plain"),
            Resource(uri="file:///b.bin", name="b", mime_type="application/octet-stream"),
        ],
        next_cursor=None,
    )

    def fake_read(uri: str) -> ReadResourceResult:
        if uri == "file:///a.txt":
            return ReadResourceResult(
                content=[ContentItem(type="text", text="hello", mime_type="text/plain")]
            )
        return ReadResourceResult(
            content=[ContentItem(
                type="resource", data=b"\x00\x01\x02", mime_type="application/octet-stream"
            )]
        )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.list_resources = AsyncMock(return_value=listing)
        MockClient.return_value.read_resource = AsyncMock(side_effect=fake_read)

        rc = RapidMCPClient({"a": {"address": "a:1"}})
        blobs = await rc.get_resources("a")

        assert len(blobs) == 2
        assert all(isinstance(b, Blob) for b in blobs)
        assert blobs[0].as_string() == "hello"
        assert blobs[0].mimetype == "text/plain"
        assert blobs[0].metadata == {"uri": "file:///a.txt"}
        assert blobs[1].as_bytes() == b"\x00\x01\x02"


@pytest.mark.asyncio
async def test_rapidmcp_client_get_resources_filters_by_uris():
    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ContentItem, ReadResourceResult

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.read_resource = AsyncMock(
            return_value=ReadResourceResult(content=[ContentItem(type="text", text="x")])
        )

        rc = RapidMCPClient({"a": {"address": "a:1"}})
        blobs = await rc.get_resources("a", uris=["file:///one"])

        assert len(blobs) == 1
        MockClient.return_value.list_resources.assert_not_called()
        MockClient.return_value.read_resource.assert_awaited_once_with("file:///one")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py::test_rapidmcp_client_get_resources_reads_listed_uris -v`
Expected: FAIL — `AttributeError: ... 'get_resources'`.

- [ ] **Step 3: Implement `get_resources` + `_read_resource_to_blob`**

Add to the module-level imports (within the existing `try:` block that already imports from `langchain_core`):

```python
    from langchain_core.document_loaders.blob_loaders import Blob
```

Add this helper near the other `_convert_*` helpers:

```python
def _read_resource_to_blob(uri: str, result: ReadResourceResult) -> Blob:
    """Flatten a ``ReadResourceResult`` into a single LangChain ``Blob``.

    Multiple content items are rare. Text items are concatenated; binary
    items are appended; the first non-empty mime_type wins.
    """
    text_parts: list[str] = []
    binary: bytes | None = None
    mime: str = ""

    for c in result.content:
        if c.mime_type and not mime:
            mime = c.mime_type
        if c.type == "text":
            text_parts.append(c.text)
        elif c.data:
            binary = (binary or b"") + c.data

    if binary is not None:
        return Blob(data=binary, mimetype=mime or "application/octet-stream",
                    metadata={"uri": uri})
    return Blob(data="".join(text_parts).encode(), mimetype=mime or "text/plain",
                metadata={"uri": uri})
```

And this method on `RapidMCPClient`:

```python
    async def get_resources(
        self,
        server_name: str,
        *,
        uris: list[str] | None = None,
    ) -> list[Blob]:
        """Read resources from one server as LangChain ``Blob`` objects.

        Args:
            server_name: Which configured server to read from.
            uris: If given, read exactly these URIs. Otherwise, list every
                resource the server exposes (with pagination) and read them all.
        """
        client = self.client(server_name)
        if uris is None:
            uris = []
            cursor: str | None = None
            while True:
                listing = await client.list_resources(cursor=cursor)
                uris.extend(r.uri for r in listing.items)
                if not listing.next_cursor:
                    break
                cursor = listing.next_cursor

        reads = await asyncio.gather(*(client.read_resource(u) for u in uris))
        return [_read_resource_to_blob(u, r) for u, r in zip(uris, reads)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add python/src/rapidmcp/integrations/langchain.py python/tests/test_integrations_langchain.py
rtk git commit -m "feat(langchain): RapidMCPClient.get_resources → LangChain Blobs"
```

---

### Task 6: `get_prompt` returns LangChain `BaseMessage` list

**Files:**
- Modify: `python/src/rapidmcp/integrations/langchain.py`
- Modify: `python/tests/test_integrations_langchain.py`

- [ ] **Step 1: Write the failing test**

Append to `python/tests/test_integrations_langchain.py`:

```python
@pytest.mark.asyncio
async def test_rapidmcp_client_get_prompt_returns_messages():
    from langchain_core.messages import AIMessage, HumanMessage

    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ContentItem, GetPromptResult, PromptMessage

    result = GetPromptResult(
        messages=[
            PromptMessage(role="user", content=ContentItem(type="text", text="hi")),
            PromptMessage(role="assistant", content=ContentItem(type="text", text="hello")),
        ]
    )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.get_prompt = AsyncMock(return_value=result)

        rc = RapidMCPClient({"a": {"address": "a:1"}})
        msgs = await rc.get_prompt("a", "greet", arguments={"name": "Ada"})

        assert len(msgs) == 2
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].content == "hi"
        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].content == "hello"
        MockClient.return_value.get_prompt.assert_awaited_once_with("greet", {"name": "Ada"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py::test_rapidmcp_client_get_prompt_returns_messages -v`
Expected: FAIL — `AttributeError: 'get_prompt'`.

- [ ] **Step 3: Implement `_get_prompt_to_messages` + `get_prompt`**

Add to the module-level imports (within the existing `try:` block):

```python
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
```

Add this helper:

```python
_ROLE_TO_MESSAGE: dict[str, type[BaseMessage]] = {
    "user": HumanMessage,
    "human": HumanMessage,
    "assistant": AIMessage,
    "ai": AIMessage,
    "system": SystemMessage,
}


def _get_prompt_to_messages(result: GetPromptResult) -> list[BaseMessage]:
    """Convert an MCP ``GetPromptResult`` to a list of LangChain messages.

    Non-text content items are serialised as ``[image: <mime>]`` etc. —
    LangChain's agent loop feeds these verbatim into the LLM.
    """
    out: list[BaseMessage] = []
    for pm in result.messages:
        cls = _ROLE_TO_MESSAGE.get(pm.role, HumanMessage)
        c = pm.content
        if c.type == "text":
            body: str = c.text
        elif c.type == "image":
            body = f"[image: {c.mime_type}, {len(c.data)} bytes]"
        elif c.type == "audio":
            body = f"[audio: {c.mime_type}, {len(c.data)} bytes]"
        else:
            body = f"[resource: {c.uri}]"
        out.append(cls(content=body))
    return out
```

And this method on `RapidMCPClient`:

```python
    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        *,
        arguments: dict[str, str] | None = None,
    ) -> list[BaseMessage]:
        """Fetch and render a prompt from one server as LangChain messages."""
        result = await self.client(server_name).get_prompt(prompt_name, arguments or {})
        return _get_prompt_to_messages(result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add python/src/rapidmcp/integrations/langchain.py python/tests/test_integrations_langchain.py
rtk git commit -m "feat(langchain): RapidMCPClient.get_prompt → LangChain messages"
```

---

### Task 7: `session()` context manager exposes raw Client

**Files:**
- Modify: `python/src/rapidmcp/integrations/langchain.py`
- Modify: `python/tests/test_integrations_langchain.py`

- [ ] **Step 1: Write the failing test**

Append to `python/tests/test_integrations_langchain.py`:

```python
@pytest.mark.asyncio
async def test_rapidmcp_client_session_yields_underlying_client():
    from rapidmcp.integrations.langchain import RapidMCPClient

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.ping = AsyncMock(return_value=True)

        rc = RapidMCPClient({"a": {"address": "a:1"}})
        async with rc.session("a") as sess:
            await sess.ping()

        MockClient.return_value.ping.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py::test_rapidmcp_client_session_yields_underlying_client -v`
Expected: FAIL — `'session'` attribute missing.

- [ ] **Step 3: Implement `session`**

Add to `RapidMCPClient`:

```python
    @contextlib.asynccontextmanager
    async def session(self, server_name: str) -> AsyncIterator[Client]:
        """Async context manager yielding the raw :class:`Client` for one server.

        Useful for features not wrapped by this adapter — ``ping``, resource
        subscription, sampling handlers, completion, etc. Lifecycle is owned
        by the :class:`RapidMCPClient`; this does not open or close the stream.
        """
        yield self.client(server_name)
```

Ensure the top of the file has: `import contextlib` and `from typing import Any, AsyncIterator`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python && uv run pytest tests/test_integrations_langchain.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add python/src/rapidmcp/integrations/langchain.py python/tests/test_integrations_langchain.py
rtk git commit -m "feat(langchain): RapidMCPClient.session context manager"
```

---

## Phase 3 — Python: remove legacy `MCPToolkit`, finalize exports

### Task 8: Add RapidMCPClient forwarding tests to `test_integrations_auth.py`

**Files:**
- Modify: `python/tests/test_integrations_auth.py`

- [ ] **Step 1: Append the new forwarding tests**

Append to `python/tests/test_integrations_auth.py` (the old `test_mcp_toolkit_*` tests stay for now — they are deleted in Task 9):

```python
# ---------------------------------------------------------------------------
# LangChain — RapidMCPClient multi-server
# ---------------------------------------------------------------------------


def test_rapidmcp_client_forwards_per_server_token_and_tls():
    try:
        from rapidmcp.integrations.langchain import RapidMCPClient
    except ImportError:
        pytest.skip("langchain-core not installed")

    tls = ClientTLSConfig(ca="ca.crt")
    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        RapidMCPClient(
            {
                "a": {"address": "host-a:50051", "token": "tok-a"},
                "b": {"address": "host-b:50051", "tls": tls},
            }
        )
        assert MockClient.call_args_list[0].args == ("host-a:50051",)
        assert MockClient.call_args_list[0].kwargs == {"token": "tok-a", "tls": None}
        assert MockClient.call_args_list[1].args == ("host-b:50051",)
        assert MockClient.call_args_list[1].kwargs == {"token": None, "tls": tls}
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `cd python && uv run pytest tests/test_integrations_auth.py -v`
Expected: PASS — both the old `test_mcp_toolkit_*` tests (they still work because the class is still present) and the new `test_rapidmcp_client_forwards_per_server_token_and_tls`.

- [ ] **Step 3: Commit**

```bash
rtk git add python/tests/test_integrations_auth.py
rtk git commit -m "test(langchain): verify RapidMCPClient forwards auth per server"
```

---

### Task 9: Delete `MCPToolkit` class, its auth tests, and update exports

**Files:**
- Modify: `python/src/rapidmcp/integrations/langchain.py`
- Modify: `python/tests/test_integrations_auth.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Delete the `MCPToolkit` class**

Open `python/src/rapidmcp/integrations/langchain.py`. Delete the entire `class MCPToolkit:` block (from the `class MCPToolkit:` line through the end of its `__repr__`). Nothing else in the file references it.

Also update the module docstring at the top — replace any lingering mention of "MCPToolkit" with "RapidMCPClient" and delete the old usage example.

- [ ] **Step 2: Delete the old MCPToolkit auth tests**

Open `python/tests/test_integrations_auth.py`. Delete these four tests entirely:
- `test_mcp_toolkit_forwards_token`
- `test_mcp_toolkit_forwards_tls`
- `test_mcp_toolkit_forwards_token_and_tls`
- `test_mcp_toolkit_no_auth_unchanged`

Also delete the `# LangChain` section header immediately above them (the new `# LangChain — RapidMCPClient multi-server` header from Task 8 is sufficient).

- [ ] **Step 3: Update `CLAUDE.md` public API snippet**

Open `D:\Trabajo\mcp-grpc\CLAUDE.md`. Replace:

```
from rapidmcp.integrations.langchain import RapidMCPToolkit  # LangChain adapter
```

with:

```
from rapidmcp.integrations.langchain import RapidMCPClient  # LangChain adapter
```

- [ ] **Step 4: Run the full Python suite**

Run: `cd python && uv run pytest -v`
Expected: all prior tests + new `test_integrations_langchain.py` green. No reference to `MCPToolkit` remains.

- [ ] **Step 5: Lint**

Run: `cd python && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean. If format fails, re-run `uv run ruff format src tests` and include the formatting in the commit.

- [ ] **Step 6: Commit**

```bash
rtk git add python/src/rapidmcp/integrations/langchain.py python/tests/test_integrations_auth.py CLAUDE.md
rtk git commit -m "refactor(langchain): remove legacy MCPToolkit in favor of RapidMCPClient"
```

---

## Phase 4 — TypeScript: `convertResult` preserves multimodal content

### Task 10: TS — multimodal `convertResult`, exported for testing

**Files:**
- Modify: `typescript/src/integrations/langchain.ts`
- Create: `typescript/tests/integrations.langchain.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect, vi } from "vitest";
import type { CallToolResult } from "../src/types.js";
import { convertResult } from "../src/integrations/langchain.js";

describe("langchain convertResult", () => {
  it("preserves image content as image_url block", () => {
    const result: CallToolResult = {
      isError: false,
      content: [
        {
          type: "image",
          data: new Uint8Array([1, 2, 3]),
          mimeType: "image/png",
          text: "",
          uri: "",
        },
      ],
    };

    const [content, artifact] = convertResult(result);
    expect(Array.isArray(content)).toBe(true);
    expect((content as Array<{ type: string }>)[0].type).toBe("image_url");
    expect(artifact).toHaveLength(1);
    expect((artifact as Array<{ type: string }>)[0].type).toBe("image");
  });

  it("collapses a single text block to a plain string", () => {
    const result: CallToolResult = {
      isError: false,
      content: [
        { type: "text", text: "hi", data: new Uint8Array(), mimeType: "", uri: "" },
      ],
    };
    const [content, artifact] = convertResult(result);
    expect(content).toBe("hi");
    expect(artifact).toBeNull();
  });

  it("formats errors as Error: <msg>", () => {
    const result: CallToolResult = {
      isError: true,
      content: [
        { type: "text", text: "boom", data: new Uint8Array(), mimeType: "", uri: "" },
      ],
    };
    const [content, artifact] = convertResult(result);
    expect(content).toBe("Error: boom");
    expect(artifact).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd typescript && npx vitest run tests/integrations.langchain.test.ts`
Expected: FAIL — `convertResult` is not exported; the current private function drops image data.

- [ ] **Step 3: Rewrite `convertResult` and export it**

In `typescript/src/integrations/langchain.ts`, replace the existing private `convertResult` with an exported version that preserves multimodal content:

```typescript
// What goes into ToolMessage.content — shown to the LLM.
export type ContentBlock = { type: string; [k: string]: unknown };
export type ToolContent = string | ContentBlock[];
export type ToolArtifact =
  | Array<{ type: "image" | "audio"; mime_type: string; data: string }>
  | null;

/**
 * Convert a CallToolResult to a [content, artifact] tuple matching
 * LangChain's `response_format: "content_and_artifact"` convention.
 *
 * Errors become `Error: <msg>` (never thrown — let the LLM see the failure).
 * Single-text results collapse to a plain string to avoid unnecessary
 * multi-modal wrapping.
 */
export function convertResult(result: CallToolResult): [ToolContent, ToolArtifact] {
  if (result.isError) {
    const text = result.content
      .map((c) => c.text)
      .filter(Boolean)
      .join(" ");
    return [`Error: ${text || "Tool returned an error with no message"}`, null];
  }
  if (result.content.length === 0) return ["", null];

  const blocks: ContentBlock[] = [];
  const artifacts: NonNullable<ToolArtifact> = [];

  const toBase64 = (bytes: Uint8Array): string =>
    Buffer.from(bytes).toString("base64");

  for (const c of result.content) {
    if (c.type === "text") {
      blocks.push({ type: "text", text: c.text });
    } else if (c.type === "image" && c.data && c.data.length > 0) {
      const b64 = toBase64(c.data);
      blocks.push({
        type: "image_url",
        image_url: { url: `data:${c.mimeType};base64,${b64}` },
      });
      artifacts.push({ type: "image", mime_type: c.mimeType, data: b64 });
    } else if (c.type === "audio" && c.data && c.data.length > 0) {
      const b64 = toBase64(c.data);
      blocks.push({
        type: "text",
        text: `[audio: ${c.mimeType}, ${c.data.length} bytes]`,
      });
      artifacts.push({ type: "audio", mime_type: c.mimeType, data: b64 });
    } else if (c.type === "resource") {
      blocks.push({ type: "text", text: `[resource: ${c.uri}]` });
    }
  }

  const artifact: ToolArtifact = artifacts.length > 0 ? artifacts : null;

  if (blocks.length === 1 && blocks[0].type === "text") {
    return [blocks[0].text as string, artifact];
  }
  return [blocks, artifact];
}
```

Leave the existing `MCPToolkit.getTools` callsite pointing at this new function — its call `convertResult(callResult)` will now receive a tuple; change that callsite to take only the content: `const [content] = convertResult(callResult); return content;`. (The whole `MCPToolkit` class is deleted in Task 11; this temporary shim just keeps the file compilable for this one task.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd typescript && npx vitest run tests/integrations.langchain.test.ts && npx tsc --noEmit`
Expected: 3 new tests PASS; TS compiles.

- [ ] **Step 5: Commit**

```bash
rtk git add typescript/src/integrations/langchain.ts typescript/tests/integrations.langchain.test.ts
rtk git commit -m "feat(langchain-ts): convertResult preserves image/audio/resource content"
```

---

## Phase 5 — TypeScript: `RapidMCPClient`

### Task 11: TS — create `RapidMCPClient` with `getTools`; delete old `MCPToolkit`

**Files:**
- Modify: `typescript/src/integrations/langchain.ts`
- Modify: `typescript/tests/integrations.langchain.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `typescript/tests/integrations.langchain.test.ts`:

```typescript
import { RapidMCPClient } from "../src/integrations/langchain.js";

describe("RapidMCPClient (TS)", () => {
  it("aggregates tools across servers", async () => {
    const rc = new RapidMCPClient({
      a: { address: "a:1" },
      b: { address: "b:1" },
    });

    const fakeA = {
      connect: vi.fn().mockResolvedValue(undefined),
      close: vi.fn().mockResolvedValue(undefined),
      listTools: vi.fn().mockResolvedValue({
        items: [{ name: "alpha", description: "", inputSchema: { type: "object" } }],
        nextCursor: null,
      }),
    };
    const fakeB = {
      connect: vi.fn().mockResolvedValue(undefined),
      close: vi.fn().mockResolvedValue(undefined),
      listTools: vi.fn().mockResolvedValue({
        items: [{ name: "beta", description: "", inputSchema: { type: "object" } }],
        nextCursor: null,
      }),
    };
    // @ts-expect-error — swap private clients for test
    rc._clients = new Map([
      ["a", fakeA],
      ["b", fakeB],
    ]);

    const tools = await rc.getTools();
    expect(tools.map((t: { name: string }) => t.name).sort()).toEqual(["alpha", "beta"]);
  });

  it("rejects empty config", () => {
    expect(() => new RapidMCPClient({})).toThrow(/at least one server/);
  });

  it("respects allowedTools per server", async () => {
    const rc = new RapidMCPClient({
      a: { address: "a:1", allowedTools: ["keep"] },
    });
    // @ts-expect-error
    rc._clients = new Map([
      [
        "a",
        {
          connect: vi.fn(),
          close: vi.fn(),
          listTools: vi.fn().mockResolvedValue({
            items: [
              { name: "keep", description: "", inputSchema: { type: "object" } },
              { name: "drop", description: "", inputSchema: { type: "object" } },
            ],
            nextCursor: null,
          }),
        },
      ],
    ]);

    const tools = await rc.getTools();
    expect(tools.map((t: { name: string }) => t.name)).toEqual(["keep"]);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd typescript && npx vitest run tests/integrations.langchain.test.ts`
Expected: FAIL — `RapidMCPClient` not exported.

- [ ] **Step 3: Rewrite `langchain.ts`**

Delete the entire `MCPToolkit` class (and its docstring). Replace the file body below the helpers with:

```typescript
// ---------------------------------------------------------------------------
// RapidMCPClient — multi-server LangChain adapter
// ---------------------------------------------------------------------------

export interface ServerConfig extends ClientOptions {
  address: string;
  allowedTools?: readonly string[];
}

export class RapidMCPClient {
  private _clients: Map<string, Client> = new Map();
  private _allowed: Map<string, Set<string> | null> = new Map();

  /**
   * @param servers Map of server name → config. Each server opens its own
   *                gRPC stream. `allowedTools` filters per server.
   */
  constructor(servers: Record<string, ServerConfig>) {
    const names = Object.keys(servers);
    if (names.length === 0) {
      throw new Error("RapidMCPClient requires at least one server config");
    }
    for (const name of names) {
      const { address, allowedTools, ...rest } = servers[name];
      this._clients.set(name, new Client(address, rest));
      this._allowed.set(name, allowedTools ? new Set(allowedTools) : null);
    }
  }

  get servers(): string[] {
    return [...this._clients.keys()];
  }

  client(serverName: string): Client {
    const c = this._clients.get(serverName);
    if (!c)
      throw new Error(
        `Unknown server ${JSON.stringify(serverName)}. Configured: ${this.servers.join(", ")}`,
      );
    return c;
  }

  async connect(): Promise<void> {
    await Promise.all([...this._clients.values()].map((c) => c.connect()));
  }

  async close(): Promise<void> {
    await Promise.all([...this._clients.values()].map((c) => c.close()));
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async getTools(opts: { serverName?: string } = {}): Promise<any[]> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let DSTool: any;
    try {
      ({ DynamicStructuredTool: DSTool } = await import("@langchain/core/tools"));
    } catch {
      throw new Error(
        "@langchain/core is required for RapidMCPClient.getTools().\n" +
          "Install it with: npm install @langchain/core",
      );
    }

    const names = opts.serverName ? [opts.serverName] : this.servers;
    for (const n of names) {
      if (!this._clients.has(n)) throw new Error(`Unknown server ${JSON.stringify(n)}`);
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const all: any[] = [];
    for (const name of names) {
      const client = this._clients.get(name)!;
      const allowed = this._allowed.get(name) ?? null;
      let cursor: string | undefined;
      while (true) {
        const result = await client.listTools(cursor);
        for (const mcpTool of result.items) {
          if (allowed && !allowed.has(mcpTool.name)) continue;
          const schema = jsonSchemaToZod(mcpTool.inputSchema);
          const toolName = mcpTool.name;
          all.push(
            new DSTool({
              name: toolName,
              description: mcpTool.description ?? "",
              schema,
              func: async (args: Record<string, unknown>) => {
                const callResult = await client.callTool(toolName, args);
                const [content] = convertResult(callResult);
                return content;
              },
            }),
          );
        }
        if (!result.nextCursor) break;
        cursor = result.nextCursor;
      }
    }
    return all;
  }
}
```

Note: `getTools` returns `any[]` because `DynamicStructuredTool` is a dynamic import and may not be resolvable at compile time if the user hasn't installed `@langchain/core`. Downstream LangChain agents accept the returned objects via duck typing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd typescript && npx tsc --noEmit && npx vitest run`
Expected: all prior TS tests + new integration tests green.

- [ ] **Step 5: Commit**

```bash
rtk git add typescript/src/integrations/langchain.ts typescript/tests/integrations.langchain.test.ts
rtk git commit -m "refactor(langchain-ts): replace MCPToolkit with RapidMCPClient"
```

---

### Task 12: TS — `RapidMCPClient.getResources` and `.getPrompt`

**Files:**
- Modify: `typescript/src/integrations/langchain.ts`
- Modify: `typescript/tests/integrations.langchain.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `typescript/tests/integrations.langchain.test.ts`:

```typescript
describe("RapidMCPClient.getResources", () => {
  it("reads supplied URIs and returns blob-like objects with metadata", async () => {
    const rc = new RapidMCPClient({ a: { address: "a:1" } });
    const fake = {
      readResource: vi.fn().mockImplementation(async (uri: string) => {
        if (uri === "file:///t.txt") {
          return {
            content: [
              { type: "text", text: "hi", mimeType: "text/plain",
                data: new Uint8Array(), uri: "" },
            ],
          };
        }
        return {
          content: [
            { type: "resource", text: "", mimeType: "application/octet-stream",
              data: new Uint8Array([9, 9]), uri },
          ],
        };
      }),
    };
    // @ts-expect-error
    rc._clients = new Map([["a", fake]]);

    const blobs = await rc.getResources("a", { uris: ["file:///t.txt", "file:///bin"] });
    expect(blobs).toHaveLength(2);
    expect(blobs[0].mimeType).toBe("text/plain");
    expect(blobs[0].asString()).toBe("hi");
    expect(blobs[0].metadata).toEqual({ uri: "file:///t.txt" });
    expect(blobs[1].asBytes()).toEqual(new Uint8Array([9, 9]));
  });
});

describe("RapidMCPClient.getPrompt", () => {
  it("returns role-tagged messages", async () => {
    const rc = new RapidMCPClient({ a: { address: "a:1" } });
    const fake = {
      getPrompt: vi.fn().mockResolvedValue({
        messages: [
          { role: "user", content: { type: "text", text: "hi",
            mimeType: "", data: new Uint8Array(), uri: "" } },
          { role: "assistant", content: { type: "text", text: "hello",
            mimeType: "", data: new Uint8Array(), uri: "" } },
        ],
      }),
    };
    // @ts-expect-error
    rc._clients = new Map([["a", fake]]);

    const msgs = await rc.getPrompt("a", "greet", { name: "Ada" });
    expect(msgs).toEqual([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]);
    expect(fake.getPrompt).toHaveBeenCalledWith("greet", { name: "Ada" });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd typescript && npx vitest run tests/integrations.langchain.test.ts`
Expected: FAIL — methods missing.

- [ ] **Step 3: Implement `getResources` + `getPrompt`**

Append to `RapidMCPClient` in `typescript/src/integrations/langchain.ts`:

```typescript
  async getResources(
    serverName: string,
    opts: { uris?: string[] } = {},
  ): Promise<Array<{
    data: Uint8Array | string;
    mimeType: string;
    metadata: { uri: string };
    asString(): string;
    asBytes(): Uint8Array;
  }>> {
    const client = this.client(serverName);

    let uris: string[];
    if (opts.uris) {
      uris = opts.uris;
    } else {
      uris = [];
      let cursor: string | undefined;
      while (true) {
        const listing = await client.listResources(cursor);
        uris.push(...listing.items.map((r) => r.uri));
        if (!listing.nextCursor) break;
        cursor = listing.nextCursor;
      }
    }

    const reads = await Promise.all(uris.map((u) => client.readResource(u)));
    return reads.map((result, i) => {
      const textParts: string[] = [];
      let binary: Uint8Array | null = null;
      let mime = "";
      for (const c of result.content) {
        if (c.mimeType && !mime) mime = c.mimeType;
        if (c.type === "text") {
          textParts.push(c.text);
        } else if (c.data && c.data.length > 0) {
          binary = binary
            ? new Uint8Array([...binary, ...c.data])
            : new Uint8Array(c.data);
        }
      }
      const uri = uris[i];
      if (binary) {
        return {
          data: binary,
          mimeType: mime || "application/octet-stream",
          metadata: { uri },
          asString: () => new TextDecoder().decode(binary!),
          asBytes: () => binary!,
        };
      }
      const text = textParts.join("");
      return {
        data: text,
        mimeType: mime || "text/plain",
        metadata: { uri },
        asString: () => text,
        asBytes: () => new TextEncoder().encode(text),
      };
    });
  }

  async getPrompt(
    serverName: string,
    promptName: string,
    args: Record<string, string> = {},
  ): Promise<Array<{ role: string; content: string }>> {
    const result = await this.client(serverName).getPrompt(promptName, args);
    return result.messages.map((pm) => {
      const c = pm.content;
      let body: string;
      if (c.type === "text") body = c.text;
      else if (c.type === "image") body = `[image: ${c.mimeType}, ${c.data.length} bytes]`;
      else if (c.type === "audio") body = `[audio: ${c.mimeType}, ${c.data.length} bytes]`;
      else body = `[resource: ${c.uri}]`;
      return { role: pm.role, content: body };
    });
  }
```

Note: TS returns a duck-typed `{ asString, asBytes, metadata }` record rather than LangChain's JS `Blob` class — the JS `Blob` shape differs from Python's and is awkward to construct server-side. Consumers that need `Document`-style objects can wrap this.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd typescript && npx vitest run tests/integrations.langchain.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add typescript/src/integrations/langchain.ts typescript/tests/integrations.langchain.test.ts
rtk git commit -m "feat(langchain-ts): RapidMCPClient.getResources + getPrompt"
```

---

### Task 13: Full cross-language suite + type-check green

**Files:**
- No code changes — verification only.

- [ ] **Step 1: Run the full TypeScript suite**

Run: `cd typescript && npx tsc --noEmit && npx vitest run`
Expected: all 103 prior tests plus the new integration tests green; no TS errors.

- [ ] **Step 2: Run the full Python suite**

Run: `cd python && uv run pytest -v`
Expected: all 234 prior tests (minus the four deleted `test_mcp_toolkit_*` tests) plus the new integration tests green.

- [ ] **Step 3: Lint Python**

Run: `cd python && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean. If format needs fixing, run `uv run ruff format src tests` and include the changes.

- [ ] **Step 4: Commit any lint autofixes**

```bash
rtk git status
# if ruff produced fixes:
rtk git add -u
rtk git commit -m "style: ruff autofix"
```

---

## Phase 6 — docs

### Task 14: Add LangChain usage section to CLAUDE.md

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\CLAUDE.md`

- [ ] **Step 1: Append a `## LangChain integration` section below the existing Public API block**

```markdown
## LangChain integration

Use `RapidMCPClient` to wire one or more RapidMCP gRPC servers into any
LangChain agent. Shape mirrors `langchain-mcp-adapters.MultiServerMCPClient`:

```python
from rapidmcp.integrations.langchain import RapidMCPClient

async with RapidMCPClient({
    "docs": {"address": "docs:50051"},
    "sql":  {"address": "sql:50051", "token": "...", "allowed_tools": ["query"]},
}) as rc:
    tools     = await rc.get_tools()                                   # aggregated across servers
    prompt    = await rc.get_prompt("docs", "summarise", arguments={"topic": "grpc"})
    blobs     = await rc.get_resources("docs", uris=["file:///readme.md"])
    async with rc.session("sql") as sess:
        await sess.ping()
```

TypeScript mirrors this shape — `RapidMCPClient` from `rapidmcp/integrations/langchain`.
```

- [ ] **Step 2: Commit**

```bash
rtk git add CLAUDE.md
rtk git commit -m "docs: LangChain integration usage for RapidMCPClient"
```

---

## Final Verification

### Task 15: End-to-end smoke against a live server (optional but recommended)

**Files:**
- No new files — ephemeral sanity check.

- [ ] **Step 1: Start a local test server** — e.g. `cd python && uv run python -m rapidmcp.cli run <any_example_server.py>`.

- [ ] **Step 2: From a scratch `ipython` or `.py` file**, run `RapidMCPClient` against the live server. Confirm `get_tools()` returns the expected tool names and that invoking one tool end-to-end works.

- [ ] **Step 3: Stop the server, note the result in the PR description. No commit — scratch file is not tracked.**

---

## Summary of changes

- **Python** — new `RapidMCPClient` (multi-server) matching `MultiServerMCPClient`'s surface; legacy `MCPToolkit` deleted; resource/prompt adapters added; ~10 new unit tests.
- **TypeScript** — new `RapidMCPClient`; legacy `MCPToolkit` deleted; `convertResult` now preserves image/audio/resource content; new vitest suite.
- **Out of scope** (future plan) — tool-call interceptors, progress/logging/elicitation callbacks wired through the toolkit surface.
