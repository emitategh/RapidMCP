# LiveKit Integration Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `rapidmcp.integrations.livekit.MCPServerGRPC` into behavioral parity with `livekit.agents.llm.mcp.MCPServerHTTP` on `livekit-agents==1.5.2`, and add a functional test suite that covers the core contract.

**Architecture:** The existing adapter subclasses `MCPServer` but overrides every method the `MCPToolset` consumer calls, bypassing the base's JSON-RPC `ClientSession` machinery (correct — we use gRPC). Five behavioral gaps were identified against the 1.5.2 base class:

1. `tool_result_resolver` kwarg is silently dropped (parity gap with HTTP).
2. Multi-content results are hand-serialized instead of delegating to the configured resolver.
3. Base's `_cache_dirty` / `_lk_tools` cache is bypassed, so `invalidate_cache()` becomes a no-op.
4. `initialize()` is not concurrency-safe (two racing callers both call `Client.connect()`).
5. Error-content joining silently drops non-text parts.

A sixth gap (`MCPTool.meta` forwarding) is deferred — it needs a proto schema change and is out of scope.

Docs still show the deprecated `AgentSession(mcp_servers=[...])` shape; the modern path is `AgentSession(tools=[MCPToolset(id=..., mcp_server=...)])`.

**Tech Stack:** Python 3.10+, `livekit-agents[mcp]>=1.5`, `mcp` (Python MCP SDK, transitive), `pytest-asyncio`, `rapidmcp.testing.InProcessChannel` (in-process loopback for tests).

---

## Out of scope

- **Gap #5 — forwarding `MCPTool.meta`.** The RapidMCP `Tool` DTO has no `meta` field; closing this needs a proto change. File a separate follow-up.
- **TypeScript LiveKit integration.** Does not exist today and is not the subject of this plan.
- **True end-to-end (LiveKit server + room + LLM) smoke test.** Level-1 functional tests only.

---

## File structure

| File | Role | Action |
|---|---|---|
| `python/pyproject.toml` | Dev deps | **Modify** — already modified in working tree (uncommitted); commit as-is, then promote livekit to a dev extra so CI picks it up |
| `python/uv.lock` | Lockfile | **Modify** — already modified; commit alongside pyproject |
| `python/src/rapidmcp/integrations/livekit.py` | Adapter | **Modify** — six focused changes under TDD |
| `python/tests/test_integrations_livekit.py` | New functional test suite | **Create** — 8 tests |
| `README.md` (root) | Docs | **Modify** — LiveKit section uses deprecated `mcp_servers=` shape |
| `python/README.md` | Docs | **Modify** — same |

`python/tests/test_integrations_auth.py` already covers auth-arg forwarding with mocks; do not duplicate there.

---

## Execution split (for subagent dispatch)

Two agents, one can run in parallel with the other because they touch disjoint files.

- **Agent 1 — Prep + Docs.** Phase 0 tasks. Commits the pending `pyproject.toml`/`uv.lock` change, rewrites both README LiveKit sections. Touches: `python/pyproject.toml`, `python/uv.lock`, `README.md`, `python/README.md`.
- **Agent 2 — Code + Tests.** Phase 1 tasks. Does the TDD fixes and the test suite. Touches: `python/src/rapidmcp/integrations/livekit.py`, `python/tests/test_integrations_livekit.py`.

File sets do not overlap, so the two agents can run concurrently on the same branch. If concurrent worktrees are preferred, create two with `superpowers:using-git-worktrees` and merge at the end.

**Both agents commit to a feature branch** — not master directly. Name: `feature/livekit-parity`.

**Pre-flight (once, before dispatching either agent):**
```bash
cd D:/Trabajo/mcp-grpc
git checkout -b feature/livekit-parity
```

---

# Phase 0 — Prep + Docs (Agent 1)

### Task 0.1: Commit the pending livekit dep change

**Files:**
- Modify: `python/pyproject.toml` (already modified)
- Modify: `python/uv.lock` (already modified)

- [ ] **Step 1: Inspect current working-tree change**

Run: `git -C D:/Trabajo/mcp-grpc status --short`
Expected: ` M python/pyproject.toml` and ` M python/uv.lock` present.

Run: `git -C D:/Trabajo/mcp-grpc diff python/pyproject.toml`
Expected: the `livekit` extra updated from `livekit-agents>=0.8` to `livekit-agents[mcp]>=0.8` (the transitive `mcp` package is required to import `livekit.agents.llm.mcp`).

- [ ] **Step 2: Also promote livekit into the dev extra so CI can run the tests**

Modify `python/pyproject.toml` — inside `[project.optional-dependencies] dev`, add the line:
```toml
    "livekit-agents[mcp]>=0.8",
```
(Keep existing dev entries in place.) Sync:
```bash
cd D:/Trabajo/mcp-grpc/python && uv sync --extra dev --extra livekit
```
Expected: sync runs clean; venv contains `livekit-agents`, `mcp`.

- [ ] **Step 3: Commit**

```bash
cd D:/Trabajo/mcp-grpc
git add python/pyproject.toml python/uv.lock
git commit -m "$(cat <<'EOF'
chore(deps): require livekit-agents[mcp] and add to dev extras

The bare livekit-agents extra previously installed without the optional
'mcp' package; importing rapidmcp.integrations.livekit fails in that
state because livekit.agents.llm.mcp depends on the mcp Python SDK.
Also pull livekit-agents into the dev extra so the new integration
test suite runs in CI without a separate install step.
EOF
)"
```

### Task 0.2: Update root README LiveKit section

**Files:**
- Modify: `README.md` (lines showing the LiveKit snippet)

- [ ] **Step 1: Locate the current snippet**

Current content around line 82–91:
```python
from rapidmcp.integrations.livekit import MCPServerGRPC
from livekit.agents import AgentSession

session = AgentSession(
    mcp_servers=[MCPServerGRPC(address="mcp-server:50051")],
)
```

- [ ] **Step 2: Replace with modern MCPToolset shape**

New content:
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

Use Edit tool with the exact old/new strings above.

- [ ] **Step 3: Verify**

Run: `grep -n 'mcp_servers=\|MCPToolset' D:/Trabajo/mcp-grpc/README.md`
Expected: `mcp_servers=` no longer appears in the LiveKit section; `MCPToolset` appears.

### Task 0.3: Update python/README.md LiveKit section

**Files:**
- Modify: `python/README.md` (LiveKit section starting around line 94)

- [ ] **Step 1: Read the current section**

Run: `sed -n '94,115p' D:/Trabajo/mcp-grpc/python/README.md` and copy the snippet verbatim.

- [ ] **Step 2: Replace with the same MCPToolset shape used in the root README**

Reuse the exact block from Task 0.2, Step 2. Use Edit tool.

- [ ] **Step 3: Verify**

Run: `grep -n 'mcp_servers=' D:/Trabajo/mcp-grpc/python/README.md`
Expected: no matches in the LiveKit section.

### Task 0.4: Commit doc updates

- [ ] **Step 1: Stage and commit**

```bash
cd D:/Trabajo/mcp-grpc
git add README.md python/README.md
git commit -m "$(cat <<'EOF'
docs(livekit): prefer MCPToolset over deprecated mcp_servers=

livekit-agents 1.5.2 warns that passing MCP servers to AgentSession or
Agent via mcp_servers= is deprecated and will be removed. Update both
READMEs to the MCPToolset shape that the library now recommends.
EOF
)"
```

- [ ] **Step 2: Confirm tree clean**

Run: `git -C D:/Trabajo/mcp-grpc status --short`
Expected: no changes.

---

# Phase 1 — Code + Tests (Agent 2)

**TDD throughout.** Each task writes a failing test first, then the implementation, then verifies, then commits.

Test discipline: the test file is `python/tests/test_integrations_livekit.py`. All tests are async (inherit from `asyncio_mode = "auto"` configured in `pyproject.toml`). They use `rapidmcp.testing.InProcessChannel` to spin up a real RapidMCP server wired to a real `Client` without sockets — the same pattern existing tests use (e.g. `python/tests/test_server.py:465`).

### Task 1.1: Scaffold the test file + first baseline test

**Files:**
- Create: `python/tests/test_integrations_livekit.py`

- [ ] **Step 1: Write the failing baseline test**

Create `python/tests/test_integrations_livekit.py` with this content:

```python
"""Functional tests for MCPServerGRPC — LiveKit integration."""
from __future__ import annotations

import pytest

pytest.importorskip("livekit.agents.llm.mcp")

from rapidmcp import RapidMCP
from rapidmcp.integrations.livekit import MCPServerGRPC
from rapidmcp.testing import InProcessChannel


def _make_server() -> RapidMCP:
    app = RapidMCP(name="test", version="0.0.1")

    @app.tool()
    async def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @app.tool()
    async def echo(text: str) -> str:
        """Echo text back."""
        return text

    return app


async def test_list_tools_and_call_tool() -> None:
    server = _make_server()
    async with InProcessChannel(server) as chan:
        grpc = MCPServerGRPC.__new__(MCPServerGRPC)
        # Bypass normal __init__ so we can inject the in-process Client.
        from rapidmcp.integrations.livekit import MCPServer
        MCPServer.__init__(grpc, client_session_timeout_seconds=30)
        grpc._address = "in-process"
        grpc._grpc_client = chan.client
        grpc._allowed_tools = None
        grpc._connected = True  # InProcessChannel handles connect

        tools = await grpc.list_tools()
        names = sorted(t.name for t in tools)
        assert names == ["add", "echo"]

        add_tool = next(t for t in tools if t.name == "add")
        result = await add_tool.fnc(raw_arguments={"a": 17, "b": 25})
        assert result == "42" or result == 42 or "42" in str(result)
```

Note: the `result == "42" or ...` tolerance accounts for the default resolver returning a JSON-serialized text part (`'"42"'`) vs. a bare int. Tighten in Task 1.6 after the resolver is wired correctly.

- [ ] **Step 2: Run to verify it fails for the right reason**

```bash
cd D:/Trabajo/mcp-grpc/python && uv run pytest tests/test_integrations_livekit.py -v
```

Expected: test runs and PASSES already (the current adapter handles list_tools + call_tool for text). Good — this locks in current behavior as a baseline. If it fails, stop and debug before continuing.

Actually expected: PASS. This baseline guards subsequent refactors.

- [ ] **Step 3: Factor the setup into a fixture**

Replace the inline instantiation with a reusable fixture:

```python
import pytest_asyncio
import contextlib

@contextlib.asynccontextmanager
async def _grpc_adapter_for(server: RapidMCP, **kwargs):
    """Yield an MCPServerGRPC wired to an in-process RapidMCP server."""
    async with InProcessChannel(server) as chan:
        adapter = MCPServerGRPC.__new__(MCPServerGRPC)
        from rapidmcp.integrations.livekit import MCPServer
        MCPServer.__init__(
            adapter,
            client_session_timeout_seconds=kwargs.pop("timeout", 30),
            tool_result_resolver=kwargs.pop("tool_result_resolver", None),
        )
        adapter._address = "in-process"
        adapter._grpc_client = chan.client
        adapter._allowed_tools = kwargs.pop("allowed_tools", None)
        adapter._connected = True
        try:
            yield adapter
        finally:
            adapter._connected = False
```

Rewrite `test_list_tools_and_call_tool` to use it:

```python
async def test_list_tools_and_call_tool() -> None:
    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:
        tools = await grpc.list_tools()
        names = sorted(t.name for t in tools)
        assert names == ["add", "echo"]

        add_tool = next(t for t in tools if t.name == "add")
        result = await add_tool.fnc(raw_arguments={"a": 17, "b": 25})
        assert "42" in str(result)
```

- [ ] **Step 4: Run — still PASS**

```bash
cd D:/Trabajo/mcp-grpc/python && uv run pytest tests/test_integrations_livekit.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd D:/Trabajo/mcp-grpc
git add python/tests/test_integrations_livekit.py
git commit -m "$(cat <<'EOF'
test(livekit): baseline functional coverage for MCPServerGRPC

Wires MCPServerGRPC to an in-process RapidMCP server via
InProcessChannel and verifies list_tools + a basic call_tool
round-trip. Establishes a fixture the rest of the suite will reuse.
EOF
)"
```

### Task 1.2: Cache-hit — honor `_cache_dirty`

**Files:**
- Modify: `python/src/rapidmcp/integrations/livekit.py` (method `list_tools`)
- Modify: `python/tests/test_integrations_livekit.py` (add test)

- [ ] **Step 1: Add failing cache-reuse test**

Append to the test file:

```python
async def test_list_tools_reuses_cache_until_invalidated() -> None:
    """list_tools should not re-hit the server when the cache is clean."""
    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:
        first = await grpc.list_tools()

        # Swap the underlying client for a sentinel — if the adapter hits it,
        # we'll know the cache wasn't used.
        class _Explode:
            async def list_tools(self):
                raise AssertionError("cache was bypassed — list_tools called a second time")
        grpc._grpc_client = _Explode()

        second = await grpc.list_tools()
        assert second is first  # same list object returned from cache

        # After invalidate, a fresh call must re-query — which now blows up.
        grpc.invalidate_cache()
        with pytest.raises(AssertionError, match="cache was bypassed"):
            await grpc.list_tools()
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd D:/Trabajo/mcp-grpc/python && uv run pytest tests/test_integrations_livekit.py::test_list_tools_reuses_cache_until_invalidated -v
```

Expected: FAIL with `AssertionError: cache was bypassed`. Current `list_tools` always rebuilds, so the second call hits the sentinel and raises. That raise is what the *third* call (after invalidate) wants to see — the second call should have been a cache hit.

- [ ] **Step 3: Implement the cache-hit fast path**

Edit `python/src/rapidmcp/integrations/livekit.py`, method `list_tools`. Add the cache check at the top:

```python
    async def list_tools(self) -> list[MCPTool]:
        if not self._cache_dirty and self._lk_tools is not None:
            return self._lk_tools

        result = await self._grpc_client.list_tools()
        # ... (rest unchanged — still ends with self._lk_tools = tools; self._cache_dirty = False)
```

`self._cache_dirty` and `self._lk_tools` are already initialized by `super().__init__`, which we call.

- [ ] **Step 4: Run to verify PASS**

```bash
cd D:/Trabajo/mcp-grpc/python && uv run pytest tests/test_integrations_livekit.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd D:/Trabajo/mcp-grpc
git add python/src/rapidmcp/integrations/livekit.py python/tests/test_integrations_livekit.py
git commit -m "fix(livekit): honor _cache_dirty in list_tools (MCPToolset compat)"
```

### Task 1.3: Re-entrant `initialize()` is race-safe

**Files:**
- Modify: `python/src/rapidmcp/integrations/livekit.py` (method `initialize`, `__init__`)
- Modify: `python/tests/test_integrations_livekit.py`

- [ ] **Step 1: Failing concurrency test**

Append:

```python
import asyncio

async def test_initialize_is_concurrency_safe() -> None:
    """Two concurrent initialize() calls must not both call Client.connect()."""
    server = _make_server()
    async with InProcessChannel(server) as chan:
        adapter = MCPServerGRPC.__new__(MCPServerGRPC)
        from rapidmcp.integrations.livekit import MCPServer
        MCPServer.__init__(adapter, client_session_timeout_seconds=30)
        adapter._address = "in-process"

        # Count connect() calls via a wrapper.
        real = chan.client
        calls = 0
        orig_connect = real.connect
        async def counting_connect():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)  # widen the race window
            return await orig_connect()
        real.connect = counting_connect  # type: ignore[method-assign]

        adapter._grpc_client = real
        adapter._allowed_tools = None
        adapter._connected = False

        await asyncio.gather(adapter.initialize(), adapter.initialize())
        assert calls == 1, f"expected one connect() call, got {calls}"
        assert adapter.initialized is True
```

- [ ] **Step 2: Run to verify FAIL**

Expected: `AssertionError: expected one connect() call, got 2`.

- [ ] **Step 3: Implement the guard**

Edit `livekit.py`. In `__init__`, add:

```python
        self._init_lock = asyncio.Lock()
```

Rewrite `initialize`:

```python
    async def initialize(self) -> None:
        if self._connected:
            return
        async with self._init_lock:
            if self._connected:
                return
            await self._grpc_client.connect()
            self._connected = True
            logger.info("MCPServerGRPC connected to %s", self._address)
```

- [ ] **Step 4: Run — PASS**

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add python/src/rapidmcp/integrations/livekit.py python/tests/test_integrations_livekit.py
git commit -m "fix(livekit): guard initialize() with a lock against concurrent callers"
```

### Task 1.4: Error-content stringification covers non-text parts

**Files:**
- Modify: `python/src/rapidmcp/integrations/livekit.py` (inside `_call` closure)
- Modify: `python/tests/test_integrations_livekit.py`

- [ ] **Step 1: Failing test — ToolError with mixed content**

Append to test file:

```python
async def test_tool_error_message_includes_non_text_parts() -> None:
    """When a tool's error payload includes non-text content, the ToolError
    message must still convey that something was there (matches base class
    behavior which calls str(part))."""
    from rapidmcp.errors import ToolError as RapidToolError
    from rapidmcp.content import Image

    app = RapidMCP(name="t", version="0")

    @app.tool()
    async def boom() -> str:
        raise RapidToolError("pre-text", content=[Image(data=b"\x00\x01", mime_type="image/png")])

    from livekit.agents.llm.mcp import ToolError as LKToolError

    async with _grpc_adapter_for(app) as grpc:
        (tool,) = await grpc.list_tools()
        with pytest.raises(LKToolError) as exc_info:
            await tool.fnc(raw_arguments={})
        msg = str(exc_info.value)
        assert "pre-text" in msg
        # The image part should surface as *something* — not be silently dropped.
        assert msg.strip() != "pre-text"
```

Note: if `rapidmcp.errors.ToolError` does not support a `content=` kwarg, adjust the test to construct a `CallToolResult` with `is_error=True` and multiple parts via a tool that returns the result directly. Verify the correct constructor before writing the test.

- [ ] **Step 2: Verify the ToolError content kwarg**

Run:
```bash
uv run python -c "from rapidmcp.errors import ToolError; import inspect; print(inspect.signature(ToolError.__init__))"
```
If the signature does not include a content param, rewrite the test to have `boom()` return a manual non-error payload that mimics the shape. (The goal is a `CallToolResult(is_error=True, content=[TextPart, ImagePart])` on the wire.) Keep the assertion intent.

- [ ] **Step 3: Run to verify FAIL**

Current code: `"\n".join(c.text for c in tool_result.content if c.text)` — the image part has empty `.text`, so it gets filtered, and the final message is exactly `"pre-text"`. That trips the `msg.strip() != "pre-text"` assertion.

Expected: FAIL.

- [ ] **Step 4: Fix the stringification**

Edit `livekit.py`, inside the `_call` closure:

```python
if tool_result.is_error:
    parts: list[str] = []
    for c in tool_result.content:
        if c.type == "text" and c.text:
            parts.append(c.text)
        elif c.type in ("image", "audio"):
            parts.append(f"[{c.type}: {c.mime_type}, {len(c.data)} bytes]")
        elif c.type == "resource":
            parts.append(f"[resource: {c.uri}]")
    raise ToolError("\n".join(parts) if parts else f"Tool '{_n}' failed without a message")
```

- [ ] **Step 5: Run — PASS**

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add python/src/rapidmcp/integrations/livekit.py python/tests/test_integrations_livekit.py
git commit -m "fix(livekit): surface non-text parts in ToolError messages"
```

### Task 1.5: Forward `tool_result_resolver`

**Files:**
- Modify: `python/src/rapidmcp/integrations/livekit.py` (constructor + `_call` closure + new helper `_to_mcp_call_result`)
- Modify: `python/tests/test_integrations_livekit.py`

This is the central change. It requires converting our `rapidmcp.types.CallToolResult` to `mcp.types.CallToolResult` so the resolver callback receives what it expects (same type `MCPServerHTTP` delivers).

- [ ] **Step 1: Failing test — custom resolver is invoked**

Append:

```python
async def test_custom_tool_result_resolver_is_invoked() -> None:
    from livekit.agents.llm.mcp import MCPToolResultContext
    import mcp.types as mcp_types

    server = _make_server()
    seen: list[MCPToolResultContext] = []

    def resolver(ctx: MCPToolResultContext) -> str:
        seen.append(ctx)
        return "resolver-said-this"

    async with _grpc_adapter_for(server, tool_result_resolver=resolver) as grpc:
        (echo_tool,) = [t for t in await grpc.list_tools() if t.name == "echo"]
        out = await echo_tool.fnc(raw_arguments={"text": "hi"})
        assert out == "resolver-said-this"
        assert len(seen) == 1
        ctx = seen[0]
        assert ctx.tool_name == "echo"
        assert ctx.arguments == {"text": "hi"}
        assert isinstance(ctx.result, mcp_types.CallToolResult)
        assert not ctx.result.isError
        assert len(ctx.result.content) == 1
        assert ctx.result.content[0].type == "text"
        assert ctx.result.content[0].text == "hi"
```

- [ ] **Step 2: Run to verify FAIL**

Expected: FAIL — the resolver is never called; `seen` stays empty.

- [ ] **Step 3: Expose the constructor kwarg**

Edit `MCPServerGRPC.__init__` signature:

```python
    def __init__(
        self,
        address: str,
        *,
        token: str | None = None,
        tls: ClientTLSConfig | None = None,
        allowed_tools: list[str] | None = None,
        client_session_timeout_seconds: float = 30,
        tool_result_resolver: "MCPToolResultResolver | None" = None,
    ) -> None:
        super().__init__(
            client_session_timeout_seconds=client_session_timeout_seconds,
            tool_result_resolver=tool_result_resolver,
        )
        # ... rest unchanged
```

Add the import at the top of the file:
```python
from livekit.agents.llm.mcp import MCPServer, MCPTool, MCPToolResultContext, MCPToolResultResolver
```

- [ ] **Step 4: Add the result-conversion helper**

Add a module-level helper in `livekit.py` (not a method):

```python
def _to_mcp_call_result(res: "RapidCallToolResult") -> "MCPCallToolResult":
    """Convert rapidmcp.types.CallToolResult to mcp.types.CallToolResult so
    that a user-supplied MCPToolResultResolver receives the same type it
    would from MCPServerHTTP."""
    import base64
    import mcp.types as mcp_types

    parts: list[Any] = []
    for c in res.content:
        if c.type == "text":
            parts.append(mcp_types.TextContent(type="text", text=c.text))
        elif c.type == "image":
            parts.append(mcp_types.ImageContent(
                type="image",
                data=base64.b64encode(c.data).decode(),
                mimeType=c.mime_type,
            ))
        elif c.type == "audio":
            parts.append(mcp_types.AudioContent(
                type="audio",
                data=base64.b64encode(c.data).decode(),
                mimeType=c.mime_type,
            ))
        elif c.type == "resource":
            parts.append(mcp_types.ResourceLink(
                type="resource_link",
                uri=c.uri,
                name=c.uri.rsplit("/", 1)[-1] or c.uri,
            ))
    return mcp_types.CallToolResult(content=parts, isError=res.is_error)
```

Add imports at the top of the file:
```python
from rapidmcp.types import CallToolResult as RapidCallToolResult
from mcp.types import CallToolResult as MCPCallToolResult
```

- [ ] **Step 5: Rewrite the `_call` closure to delegate to the resolver**

Inside `list_tools`, replace the body of `_call` below the `is_error` branch:

```python
            async def _call(raw_arguments: dict[str, Any], _n: str = _name) -> Any:
                tool_result = await self._grpc_client.call_tool(_n, raw_arguments)
                if tool_result.is_error:
                    parts: list[str] = []
                    for c in tool_result.content:
                        if c.type == "text" and c.text:
                            parts.append(c.text)
                        elif c.type in ("image", "audio"):
                            parts.append(f"[{c.type}: {c.mime_type}, {len(c.data)} bytes]")
                        elif c.type == "resource":
                            parts.append(f"[resource: {c.uri}]")
                    raise ToolError(
                        "\n".join(parts) if parts else f"Tool '{_n}' failed without a message"
                    )

                mcp_result = _to_mcp_call_result(tool_result)
                ctx = MCPToolResultContext(
                    tool_name=_n, arguments=raw_arguments, result=mcp_result
                )
                resolved = self._tool_result_resolver(ctx)
                if asyncio.iscoroutine(resolved):
                    resolved = await resolved
                return resolved
```

Add `import asyncio` to the top of the file (required for `iscoroutine`).

- [ ] **Step 6: Run all tests — PASS**

```bash
cd D:/Trabajo/mcp-grpc/python && uv run pytest tests/test_integrations_livekit.py -v
```

Expected: 5 passed. Critically, `test_list_tools_and_call_tool` still passes because the *default* resolver (inherited from the base) returns `str(content[0].model_dump_json())` for the single-text case — that string contains `"42"` so the `"42" in str(result)` assertion holds.

- [ ] **Step 7: Commit**

```bash
git add python/src/rapidmcp/integrations/livekit.py python/tests/test_integrations_livekit.py
git commit -m "$(cat <<'EOF'
feat(livekit): forward tool_result_resolver and convert results to mcp.types

Closes the parity gap with MCPServerHTTP: accept the
tool_result_resolver constructor kwarg, convert rapidmcp CallToolResult
to mcp.types.CallToolResult, and invoke the resolver with a proper
MCPToolResultContext. Also routes the error path through the same
resolver-free stringification.
EOF
)"
```

### Task 1.6: Multi-content responses flow through the resolver

**Files:**
- Modify: `python/tests/test_integrations_livekit.py`

(Code for this was already written in Task 1.5 — the old hand-rolled JSON path was replaced wholesale by `_tool_result_resolver(ctx)`. This task just proves it with a test.)

- [ ] **Step 1: Failing test — multi-content uses the default resolver**

Append:

```python
async def test_multi_content_uses_default_resolver() -> None:
    """With no custom resolver, a multi-content response is JSON-serialized
    by the library's default resolver (not our old hand-rolled shape)."""
    import json as _json
    from rapidmcp.content import Image

    app = RapidMCP(name="t", version="0")

    @app.tool()
    async def mixed():
        return ["hello", Image(data=b"\xff\xd8", mime_type="image/jpeg")]

    async with _grpc_adapter_for(app) as grpc:
        (tool,) = await grpc.list_tools()
        out = await tool.fnc(raw_arguments={})
        assert isinstance(out, str)
        parsed = _json.loads(out)
        assert isinstance(parsed, list)
        assert any(p.get("type") == "text" and p.get("text") == "hello" for p in parsed)
        assert any(p.get("type") == "image" for p in parsed)
```

- [ ] **Step 2: Run — should PASS without further code change**

```bash
uv run pytest tests/test_integrations_livekit.py::test_multi_content_uses_default_resolver -v
```

Expected: PASS.

If it fails: investigate. The expected failure mode would be incorrect type mapping in `_to_mcp_call_result`; fix there.

- [ ] **Step 3: Run the whole suite to confirm nothing regressed**

Expected: 6 passed.

- [ ] **Step 4: Commit**

```bash
git add python/tests/test_integrations_livekit.py
git commit -m "test(livekit): verify multi-content results go through the resolver"
```

### Task 1.7: `client_streams()` stub cleanup

**Files:**
- Modify: `python/src/rapidmcp/integrations/livekit.py`
- Modify: `python/tests/test_integrations_livekit.py`

- [ ] **Step 1: Add a contract test**

Append to test file:

```python
async def test_client_streams_raises_not_implemented() -> None:
    """MCPServerGRPC uses gRPC transport; the base-class JSON-RPC path
    must never be entered. Calling client_streams() must raise cleanly."""
    server = _make_server()
    async with _grpc_adapter_for(server) as grpc:
        with pytest.raises(NotImplementedError, match="gRPC transport"):
            grpc.client_streams()
```

Note: we do NOT `await` anything. The current impl is an `@asynccontextmanager` generator that raises on `__aenter__`; this test calls it directly and expects the exception synchronously. That's the behavior we want after the cleanup — dropping the decorator.

- [ ] **Step 2: Run — expect FAIL (raises only on __aenter__, not on call)**

Expected: FAIL — the generator wrapper defers the raise to enter time.

- [ ] **Step 3: Simplify `client_streams`**

Edit `livekit.py`. Remove the `@contextlib.asynccontextmanager` decorator and the dead `yield`:

```python
    def client_streams(self):  # type: ignore[override]
        # MCPServerGRPC bypasses the JSON-RPC ClientSession path entirely —
        # initialize() and list_tools() talk to the gRPC Client directly.
        raise NotImplementedError("MCPServerGRPC uses gRPC transport, not client_streams")
```

Also remove the now-unused `import contextlib` if nothing else in the module uses it (check with grep first).

- [ ] **Step 4: Run — PASS**

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add python/src/rapidmcp/integrations/livekit.py python/tests/test_integrations_livekit.py
git commit -m "chore(livekit): simplify client_streams stub (raise on call, no dead yield)"
```

### Task 1.8: Final verification

- [ ] **Step 1: Run the entire Python test suite**

```bash
cd D:/Trabajo/mcp-grpc/python && uv run pytest -v 2>&1 | tail -20
```

Expected: previous test count + 7 new livekit tests all passing. If any existing test regressed, investigate before proceeding.

- [ ] **Step 2: Run ruff**

```bash
cd D:/Trabajo/mcp-grpc/python && uv run ruff check src/rapidmcp/integrations/livekit.py tests/test_integrations_livekit.py && uv run ruff format src/rapidmcp/integrations/livekit.py tests/test_integrations_livekit.py
```

Expected: clean. If format made changes, commit them:

```bash
git add python/src/rapidmcp/integrations/livekit.py python/tests/test_integrations_livekit.py
git commit -m "style: ruff format livekit integration"
```

- [ ] **Step 3: Merge back to master**

Once Agent 1 has also merged its commits, merge-squash the `feature/livekit-parity` branch into `master`, or leave it as a PR-ready branch for review. Coordinate with the user before pushing.

---

## Self-review checklist

- [x] Every task lists exact file paths.
- [x] Every code step shows the actual code, not a description.
- [x] Every test has actual assertions and `pytest` invocation with expected outcome.
- [x] Every method signature referenced later is defined in an earlier task (`_grpc_adapter_for`, `_to_mcp_call_result`, `MCPToolResultContext`).
- [x] Cache fix (Task 1.2) doesn't conflict with resolver fix (Task 1.5) — both touch `list_tools` / `_call`, applied in order.
- [x] Error-content fix (Task 1.4) is superseded by the resolver wiring (Task 1.5), but Task 1.5 preserves the Task 1.4 behavior for the error path.
- [x] Gap #5 (meta) explicitly documented as out of scope.
- [x] Phase 0 and Phase 1 touch disjoint files, enabling parallel subagent dispatch.
