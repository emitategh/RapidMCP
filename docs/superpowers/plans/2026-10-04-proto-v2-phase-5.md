# Proto v2, Phase 5 Implementation Plan — cache hints, trace context, icons, interop

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the v2 protocol: configurable cache hints, trace-context propagation, icons, a fast path for calls that asked for no events, and tests that run each language's client against the other language's server.

**Architecture:** Three small additions ride on what phases 1–4 built: cache hints are server settings stamped on results that already have the field; trace context is three gRPC metadata keys passed from a client-side provider to `ctx.trace_context`; icons are an optional repeated field on listed items. Cross-language tests start the other implementation's server as a subprocess and drive it with the local client.

**Tech Stack:** Python 3.10+ (`grpcio` aio, `pytest`), TypeScript (`nice-grpc`, `vitest`, `vite-node` for the subprocess server).

**Spec:** `docs/superpowers/specs/2026-10-01-proto-v2-stateless-design.md` — "Cache hints", "Per-request metadata" (trace context), and the 2026-10-04 addendum (trace context, icons).

## Global Constraints

- Cache hints default to `ttl_ms = 0`, scope `private`. Python: `RapidMCP(cache_ttl=<seconds>, cache_scope="private"|"public")`. TypeScript: `cacheTtlMs`, `cacheScope`. A negative TTL or an unknown scope is rejected when the server is constructed.
- Trace context is exactly the metadata keys `traceparent`, `tracestate`, `baggage`. Other keys from the provider are not sent; other metadata is not exposed as trace context.
- An icon's `src` must start with `https://` or `data:` (case-insensitive); anything else is rejected at registration. Icons are sent on v2 only.
- The server's own icons are sent with `Discover` only, not on every result.
- `proto/mcp.proto` and the v1 stubs are not changed.
- Cross-language tests skip, with a reason, when the other toolchain is not installed.
- The client `mode` default stays `"legacy"`.
- Python: project venv; `ruff format` and `ruff check` before each commit. Git: `-c safe.directory=D:/Trabajo/mcp-grpc`; no co-author or tool attribution in commit messages.

## Review Focus

1. **An icon with a `javascript:` or `http:` source** — rejected at registration, never listed.
2. **A trace-context provider that returns extra keys** — only the three trace keys are sent.
3. **A v1 client listing the same server** — sees no cache hints or icons and is otherwise unaffected.
4. **A call that asked for no progress or logs** — takes the fast path and still reports errors and cancellation correctly.
5. **A client of one language against a server of the other** — every operation, including an input round and a notification.

---

### Task 1: Proto — icons

**Files:**
- Modify: `proto/mcp_v2.proto`; regenerate the v2 stubs in both languages
- Modify: `python/tests/test_v2_proto.py`

- [ ] **Step 1: Failing test**

Append to `python/tests/test_v2_proto.py`:

```python


def test_listed_items_and_the_server_can_carry_icons():
    from rapidmcp._generated import mcp_v2_pb2 as pb

    icon = pb.Icon(src="https://example.com/i.png", mime_type="image/png", sizes=["48x48"], theme="dark")

    for message in (pb.Tool, pb.Resource, pb.ResourceTemplate, pb.Prompt, pb.Implementation):
        assert list(message(icons=[icon]).icons) == [icon]
```

Run: `cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_proto.py -q`. Expected: FAIL — `AttributeError: ... has no attribute 'Icon'`.

- [ ] **Step 2: Extend the proto**

Add the message after `message Implementation { ... }`:

```proto

// A visual identifier for a tool, resource, prompt or implementation.
message Icon {
  string src             = 1;  // https: URL or data: URI
  string mime_type       = 2;
  repeated string sizes  = 3;  // e.g. "48x48", "any"
  string theme           = 4;  // "light" | "dark" | ""
}
```

and one field to each of five messages:

```proto
  repeated Icon icons = 3;
```

in `Implementation`;

```proto
  repeated Icon icons = 6;
```

in `Tool`;

```proto
  repeated Icon icons = 5;
```

in `Resource` and in `ResourceTemplate`;

```proto
  repeated Icon icons = 4;
```

in `Prompt`.

- [ ] **Step 3: Regenerate v2 only, verify, commit**

```powershell
cd python; .\.venv\Scripts\python.exe generate.py mcp_v2.proto
cd ..\typescript; npm run generate -- --path ../proto/mcp_v2.proto
cd ..\python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_proto.py -q
git -c safe.directory=D:/Trabajo/mcp-grpc add proto python/src/rapidmcp/_generated python/tests/test_v2_proto.py typescript/generated/mcp_v2.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(proto): icons on v2 tools, resources, prompts and the server"
```

---

### Task 2: Python server — cache hints, icons, trace context, fast path

**Files:**
- Create: `python/src/rapidmcp/icons.py`
- Modify: `python/src/rapidmcp/__init__.py` (export `Icon`)
- Modify: `python/src/rapidmcp/tools/tool.py`, `tools/tool_manager.py`, `resources/resource.py`, `resources/manager.py`, `prompts/prompt.py`, `prompts/manager.py`, `server.py`
- Modify: `python/src/rapidmcp/context.py`, `_v2_context.py`, `_v2_servicer.py`
- Test: `python/tests/test_v2_extras_server.py`

**Interfaces:**
- Produces: `rapidmcp.Icon(src, mime_type="", sizes=(), theme="")`; `rapidmcp.icons._checked_icons(icons) -> list[Icon]`; `icons=` on `@server.tool`, `@server.resource`, `@server.resource_template`, `@server.prompt` and `RapidMCP(...)`; `Registered*.icons`; `RapidMCP(cache_ttl: float = 0.0, cache_scope: str = "private")`; `server.icons`, `server._cache_ttl`, `server._cache_scope`; `ctx.trace_context: Mapping[str, str]` (empty on v1); `_V2Context(meta, emit, answers=None, trace_context=None)`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_extras_server.py`:

```python
"""Cache hints, icons and trace context on the v2 service."""

import json

import pytest
from grpc import aio

from rapidmcp import Client, Context, Icon, RapidMCP
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc

META = pb.RequestMeta(protocol_version="2026-07-28", client_capabilities=pb.ClientCapabilities())
ICON = Icon(src="https://example.com/i.png", mime_type="image/png", sizes=("48x48",), theme="dark")
WIRE_ICON = pb.Icon(
    src="https://example.com/i.png", mime_type="image/png", sizes=["48x48"], theme="dark"
)


def _server(**kwargs) -> RapidMCP:
    srv = RapidMCP(name="extras", version="1.0", **kwargs)

    @srv.tool(icons=[ICON])
    async def echo(text: str) -> str:
        return text

    @srv.tool()
    async def trace(ctx: Context) -> dict:
        return dict(ctx.trace_context)

    @srv.resource("res://a", icons=[ICON])
    async def a() -> str:
        return "a"

    @srv.resource_template("res://items/{item_id}", icons=[ICON])
    async def item(item_id: str) -> str:
        return item_id

    @srv.prompt(icons=[ICON])
    async def greet(name: str) -> str:
        return f"hi {name}"

    return srv


async def _everything(srv: RapidMCP) -> dict:
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)
        read = [
            e
            async for e in stub.ReadResource(pb.ReadResourceRequest(meta=META, uri="res://a"))
        ]
        return {
            "discover": await stub.Discover(pb.DiscoverRequest(meta=META)),
            "tools": await stub.ListTools(pb.ListToolsRequest(meta=META)),
            "resources": await stub.ListResources(pb.ListResourcesRequest(meta=META)),
            "templates": await stub.ListResourceTemplates(pb.ListResourceTemplatesRequest(meta=META)),
            "prompts": await stub.ListPrompts(pb.ListPromptsRequest(meta=META)),
            "read": read[-1].complete,
        }


async def test_cache_hints_default_to_immediately_stale_and_private():
    results = await _everything(_server())

    assert {
        name: (r.cache.ttl_ms, r.cache.scope) for name, r in results.items()
    } == dict.fromkeys(results, (0, pb.CACHE_SCOPE_PRIVATE))


async def test_configured_cache_hints_are_stamped_on_every_cacheable_result():
    results = await _everything(_server(cache_ttl=60, cache_scope="public"))

    assert {
        name: (r.cache.ttl_ms, r.cache.scope) for name, r in results.items()
    } == dict.fromkeys(results, (60_000, pb.CACHE_SCOPE_PUBLIC))


def test_bad_cache_settings_are_rejected_at_construction():
    with pytest.raises(ValueError, match="cache_ttl"):
        RapidMCP(name="x", version="1", cache_ttl=-1)
    with pytest.raises(ValueError, match="cache_scope"):
        RapidMCP(name="x", version="1", cache_scope="everyone")


async def test_icons_are_listed_with_their_items():
    results = await _everything(_server(icons=[ICON]))

    echo = next(t for t in results["tools"].tools if t.name == "echo")
    plain = next(t for t in results["tools"].tools if t.name == "trace")
    assert list(echo.icons) == [WIRE_ICON]
    assert list(plain.icons) == []
    assert list(results["resources"].resources[0].icons) == [WIRE_ICON]
    assert list(results["templates"].templates[0].icons) == [WIRE_ICON]
    assert list(results["prompts"].prompts[0].icons) == [WIRE_ICON]


async def test_server_icons_travel_with_discover_only():
    results = await _everything(_server(icons=[ICON]))

    assert list(results["discover"].meta.server_info.icons) == [WIRE_ICON]
    assert list(results["tools"].meta.server_info.icons) == []


@pytest.mark.parametrize("src", ["javascript:alert(1)", "http://example.com/i.png", "file:///i.png", ""])
def test_icon_sources_other_than_https_or_data_are_rejected(src):
    srv = RapidMCP(name="x", version="1")

    with pytest.raises(ValueError, match="https: or data:"):

        @srv.tool(icons=[Icon(src=src)])
        async def bad() -> str:
            return "x"

    assert srv.list_registered_tools() == []


def test_data_uri_icons_are_accepted():
    srv = RapidMCP(name="x", version="1", icons=[Icon(src="DATA:image/png;base64,AAAA")])

    assert srv.icons[0].src == "DATA:image/png;base64,AAAA"


async def test_trace_context_reaches_the_tool_and_nothing_else_does():
    srv = _server()
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        stub = mcp_v2_pb2_grpc.McpStub(channel)
        metadata = [
            ("traceparent", "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01"),
            ("tracestate", "vendor=1"),
            ("baggage", "user=ada"),
            ("x-other", "ignored"),
        ]
        events = [
            e
            async for e in stub.CallTool(
                pb.CallToolRequest(meta=META, name="trace", arguments="{}"), metadata=metadata
            )
        ]

    assert json.loads(events[-1].complete.structured_content) == {
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
        "tracestate": "vendor=1",
        "baggage": "user=ada",
    }


async def test_trace_context_is_empty_on_v1():
    srv = _server()
    async with srv, Client(f"localhost:{srv.port}") as client:
        result = await client.call_tool("trace")

    assert json.loads(result.content[0].text) == {}
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_extras_server.py -q
```

Expected: collection error — `ImportError: cannot import name 'Icon' from 'rapidmcp'`.

- [ ] **Step 3: Icons**

`python/src/rapidmcp/icons.py`:

```python
"""Icons: visual identifiers for tools, resources, prompts and the server."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Icon:
    """An icon a client may show next to an item.

    ``src`` is an ``https:`` URL or a ``data:`` URI. ``sizes`` are strings such
    as ``"48x48"`` or ``"any"``; ``theme`` is ``"light"``, ``"dark"`` or empty.
    """

    src: str
    mime_type: str = ""
    sizes: tuple[str, ...] = ()
    theme: str = ""


def _checked_icons(icons: Iterable[Icon] | None) -> list[Icon]:
    """*icons* as a list, refusing sources a client must not be asked to load."""
    result = list(icons or [])
    for icon in result:
        if not icon.src.lower().startswith(("https://", "data:")):
            raise ValueError(f"Icon src must be an https: or data: URI, got {icon.src!r}")
    return result
```

Export it: in `python/src/rapidmcp/__init__.py` add `from rapidmcp.icons import Icon` (after the `rapidmcp.errors` import) and `"Icon",` to `__all__` (ruff will sort it).

Give the four registration records an `icons` field. In `python/src/rapidmcp/tools/tool.py` (`RegisteredTool`), `resources/resource.py` (`RegisteredResource`, `RegisteredResourceTemplate`) and `prompts/prompt.py` (`RegisteredPrompt`), add as the last field:

```python
    icons: list = field(default_factory=list)
```

and `field` to each file's `dataclasses` import.

Accept `icons` where items are registered. In `tools/tool_manager.py`, add the parameter `icons: Iterable[Icon] | None = None,` after `title: str = "",`, the argument `icons=_checked_icons(icons),` to the `RegisteredTool(...)` call, and the imports `from collections.abc import Callable, Iterable` and `from rapidmcp.icons import Icon, _checked_icons`. Validate before the decorator runs so a bad icon fails at the `@server.tool(...)` line: make the first statement of `tool()`'s body

```python
        checked_icons = _checked_icons(icons)
```

and pass `icons=checked_icons` instead.

In `resources/manager.py`, both `resource` and `resource_template` gain `icons: Iterable[Icon] | None = None,` after `mime_type`, the first statement `checked_icons = _checked_icons(icons)`, and `icons=checked_icons,` in their `Registered...(...)` calls; same imports.

In `prompts/manager.py`, `prompt` becomes `def prompt(self, *, description: str | None = None, icons: Iterable[Icon] | None = None)`, with `checked_icons = _checked_icons(icons)` first and `icons=checked_icons,` in `RegisteredPrompt(...)`; same imports.

In `server.py`, thread `icons` through the four public decorators (`tool`, `resource`, `resource_template`, `prompt`) to their managers, add `from rapidmcp.icons import Icon, _checked_icons`, and add three constructor parameters after `state_secret`:

```python
        state_secret: str | bytes | None = None,
        cache_ttl: float = 0.0,
        cache_scope: str = "private",
        icons: Iterable[Icon] | None = None,
    ) -> None:
        if cache_ttl < 0:
            raise ValueError(f"cache_ttl must be 0 or more seconds, got {cache_ttl!r}")
        if cache_scope not in ("private", "public"):
            raise ValueError(f"cache_scope must be 'private' or 'public', got {cache_scope!r}")
        # How long clients may treat lists and resource reads as fresh, and whether
        # shared intermediaries may cache them. 0 / private = always refetch.
        self._cache_ttl = cache_ttl
        self._cache_scope = cache_scope
        self.icons = _checked_icons(icons)
```

(`Iterable` joins the `collections.abc` import.)

- [ ] **Step 4: Trace context**

In `python/src/rapidmcp/context.py`, next to `_structured_content` in the `Context` class body:

```python
    # W3C trace context of the request (traceparent, tracestate, baggage). Empty on v1.
    trace_context: Mapping[str, str] = MappingProxyType({})
```

with `from collections.abc import Mapping` and `from types import MappingProxyType`.

In `python/src/rapidmcp/_v2_context.py`, the constructor takes and stores it:

```python
    def __init__(
        self,
        meta: pb.RequestMeta,
        emit: Callable[[pb.CallToolEvent], Awaitable[None]],
        answers: dict[str, dict[str, str]] | None = None,
        trace_context: dict[str, str] | None = None,
    ) -> None:
        self._meta = meta
        self._emit = emit
        self._answers = answers or {}
        self._elicit_calls = 0
        self.trace_context = dict(trace_context or {})
```

- [ ] **Step 5: The servicer — hints, icons, trace context, fast path**

In `python/src/rapidmcp/_v2_servicer.py`:

Module level, after `_HINTS`:

```python
_TRACE_KEYS = ("traceparent", "tracestate", "baggage")


def _icons(icons) -> list[pb.Icon]:
    return [
        pb.Icon(src=i.src, mime_type=i.mime_type, sizes=list(i.sizes), theme=i.theme)
        for i in icons
    ]


async def _discard(event) -> None:
    """The emit of a call that asked for no progress and no logs."""
```

Replace `_no_cache` with:

```python
    def _cache_hint(self) -> pb.CacheHint:
        public = self._server._cache_scope == "public"
        return pb.CacheHint(
            ttl_ms=int(self._server._cache_ttl * 1000),
            scope=pb.CACHE_SCOPE_PUBLIC if public else pb.CACHE_SCOPE_PRIVATE,
        )
```

and replace every `self._no_cache()` with `self._cache_hint()`.

In `Discover`, send the server's icons with its identity:

```python
        return pb.DiscoverResult(
            meta=pb.ResultMeta(
                server_info=pb.Implementation(
                    name=server.name, version=server.version, icons=_icons(server.icons)
                )
            ),
            supported_versions=SUPPORTED_VERSIONS,
            capabilities=capabilities,
            cache=self._cache_hint(),
        )
```

Add `icons=_icons(t.icons),` to the `pb.Tool(...)` call, `icons=_icons(r.icons)` to `pb.Resource(...)`, `icons=_icons(t.icons),` to `pb.ResourceTemplate(...)` and `icons=_icons(p.icons),` to `pb.Prompt(...)`.

In `CallTool`'s `work`, build the context with the request's trace context:

```python
            trace = {
                key: value for key, value in context.invocation_metadata() if key in _TRACE_KEYS
            }
            ctx = _V2Context(request.meta, emit, answers, trace)
```

and replace the `async with aclosing(...)` block inside the `try` with a version that skips the queue when the request asked for no events:

```python
            if not request.meta.HasField("progress_token") and not request.meta.HasField("log_level"):
                # Nothing can be emitted before the result, so there is nothing to
                # multiplex: run the work on this task and yield what it returns.
                yield await work(_discard)
            else:
                # aclosing: if this RPC is cancelled while suspended at the yield below,
                # the inner generator is closed at once and its tool task cancelled.
                async with aclosing(self._stream(work)) as events:
                    async for event in events:
                        yield event
```

- [ ] **Step 6: Run, lint, commit**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_extras_server.py -q
.\.venv\Scripts\python.exe -m ruff check src tests --fix; .\.venv\Scripts\python.exe -m ruff format src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp python/tests/test_v2_extras_server.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): cache hints, icons and trace context on v2; fast path for plain calls"
```

Expected: 13 tests pass; the full suite stays green.

---

### Task 3: Python client — cache hints, icons, trace context

**Files:**
- Modify: `python/src/rapidmcp/types.py`, `python/src/rapidmcp/_v2_client.py`, `python/src/rapidmcp/client.py`
- Test: `python/tests/test_v2_extras_client.py`

**Interfaces:**
- Produces: `ListResult.ttl_ms: int | None`, `ListResult.cache_scope: str | None`, the same two on `ReadResourceResult`; `icons: list[Icon]` on `Tool`, `Resource`, `ResourceTemplate`, `Prompt` and `ServerInfo`; `Client(..., trace_context: Callable[[], Mapping[str, str]] | None = None)`; `_V2Transport(..., trace_context=None)`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_extras_client.py`:

```python
"""A modern client sees cache hints and icons, and propagates trace context."""

import pytest

from rapidmcp import Client, Context, Icon, RapidMCP

ICON = Icon(src="https://example.com/i.png", mime_type="image/png", sizes=("48x48",), theme="dark")


@pytest.fixture
async def server():
    srv = RapidMCP(name="extras", version="1.0", cache_ttl=30, cache_scope="public", icons=[ICON])

    @srv.tool(icons=[ICON])
    async def trace(ctx: Context) -> dict:
        return dict(ctx.trace_context)

    @srv.resource("res://a", icons=[ICON])
    async def a() -> str:
        return "a"

    @srv.resource_template("res://items/{item_id}", icons=[ICON])
    async def item(item_id: str) -> str:
        return item_id

    @srv.prompt(icons=[ICON])
    async def greet(name: str) -> str:
        return f"hi {name}"

    async with srv:
        yield srv


async def test_modern_client_sees_cache_hints(server):
    async with Client(f"localhost:{server.port}", mode="modern") as client:
        results = [
            await client.list_tools(),
            await client.list_resources(),
            await client.list_resource_templates(),
            await client.list_prompts(),
            await client.read_resource("res://a"),
        ]

    assert [(r.ttl_ms, r.cache_scope) for r in results] == [(30_000, "public")] * 5


async def test_legacy_client_sees_no_cache_hints_or_icons(server):
    async with Client(f"localhost:{server.port}") as client:
        tools = await client.list_tools()
        read = await client.read_resource("res://a")

    assert (tools.ttl_ms, tools.cache_scope, read.ttl_ms, read.cache_scope) == (None,) * 4
    assert tools.items[0].icons == []
    assert client.server_info.icons == []


async def test_modern_client_sees_icons(server):
    async with Client(f"localhost:{server.port}", mode="modern") as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        prompts = await client.list_prompts()
        server_icons = client.server_info.icons

    assert tools.items[0].icons == [ICON]
    assert resources.items[0].icons == [ICON]
    assert templates.items[0].icons == [ICON]
    assert prompts.items[0].icons == [ICON]
    assert server_icons == [ICON]


async def test_trace_context_provider_is_called_per_request_and_filtered(server):
    spans = iter(["00-aaaa-01", "00-bbbb-01"])

    def provider() -> dict[str, str]:
        return {"traceparent": next(spans), "baggage": "user=ada", "authorization": "nope"}

    client = Client(f"localhost:{server.port}", mode="modern", trace_context=provider)
    await client.connect()  # discover does not consume a span: the provider is only for calls
    try:
        first = await client.call_tool("trace")
        second = await client.call_tool("trace")
    finally:
        await client.close()

    assert first.structured_content == {"traceparent": "00-aaaa-01", "baggage": "user=ada"}
    assert second.structured_content == {"traceparent": "00-bbbb-01", "baggage": "user=ada"}
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_extras_client.py -q
```

Expected: FAIL — `AttributeError: 'ListResult' object has no attribute 'ttl_ms'`, `TypeError: ... unexpected keyword argument 'trace_context'`.

- [ ] **Step 3: Types**

In `python/src/rapidmcp/types.py`: add `from rapidmcp.icons import Icon`; add

```python
    icons: list[Icon] = field(default_factory=list)
```

as the last field of `Tool`, `Resource`, `ResourceTemplate`, `Prompt` and `ServerInfo`; add to `ReadResourceResult` and `ListResult`:

```python
    # Freshness hint from a v2 server; None when the server sent none (v1).
    ttl_ms: int | None = None
    cache_scope: str | None = None  # "private" or "public"
```

Add a helper and use it in the converters:

```python
def _convert_icons(p) -> list[Icon]:
    """Icons of a listed item; v1 messages have none."""
    return [
        Icon(src=i.src, mime_type=i.mime_type, sizes=tuple(i.sizes), theme=i.theme)
        for i in getattr(p, "icons", [])
    ]
```

and pass `icons=_convert_icons(p),` in `_convert_tool_v2`, `_convert_resource`, `_convert_resource_template` and `_convert_prompt`.

- [ ] **Step 4: Transport**

In `python/src/rapidmcp/_v2_client.py`:

Add `from collections.abc import Callable, Mapping` (replacing the `Callable` import), import `_convert_icons` from `rapidmcp.types`, and at module level:

```python
TRACE_KEYS = ("traceparent", "tracestate", "baggage")


def _cache(result) -> dict:
    """ttl_ms / cache_scope keyword arguments from a result's cache hint."""
    if not result.HasField("cache"):
        return {}
    scope = "public" if result.cache.scope == pb.CACHE_SCOPE_PUBLIC else "private"
    return {"ttl_ms": result.cache.ttl_ms, "cache_scope": scope}
```

The constructor gains a last parameter `trace_context: Callable[[], Mapping[str, str]] | None = None` stored as `self._trace_context`, and a method:

```python
    def _call_metadata(self, *, traced: bool = True) -> list[tuple[str, str]]:
        """The call's gRPC metadata: credentials, plus the current trace context."""
        if not traced or self._trace_context is None:
            return self._metadata
        current = self._trace_context() or {}
        return self._metadata + [(key, current[key]) for key in TRACE_KEYS if key in current]
```

Use it: in `_call`, `metadata=self._call_metadata(traced=traced)` with a new keyword-only parameter `traced: bool = True` on `_call`; in `_stream`, `metadata=self._call_metadata()`; in `listen`, `metadata=self._call_metadata(traced=False)`. `discover` calls `self._call(self._stub.Discover, ..., traced=False)` — discovery is not part of any user operation.

In `discover`, add `icons=_convert_icons(result.meta.server_info),` to `ServerInfo(...)`. In each `list_*` method add `**_cache(result),` to the `ListResult(...)` call. `read_resource` becomes:

```python
    async def read_resource(self, uri: str) -> ReadResourceResult:
        def build(responses, state):
            return pb.ReadResourceRequest(
                meta=self._meta(), uri=uri, input_responses=responses, request_state=state
            )

        wire = await self._run(self._stub.ReadResource, build)
        result = _convert_read_resource_result(wire)
        hints = _cache(wire)
        result.ttl_ms, result.cache_scope = hints.get("ttl_ms"), hints.get("cache_scope")
        return result
```

- [ ] **Step 5: Client option**

In `python/src/rapidmcp/client.py`, add the constructor parameter `trace_context: Callable[[], Mapping[str, str]] | None = None,` after `mode`, store it as `self._trace_context`, pass `trace_context=self._trace_context,` to `_V2Transport(...)`, and add `from collections.abc import Callable, Mapping` to the imports. Document it in the class docstring:

```python
    ``trace_context`` is called once per v2 request and may return
    ``traceparent``, ``tracestate`` and ``baggage``; they travel as gRPC
    metadata and reach the tool as ``ctx.trace_context``.
```

- [ ] **Step 6: Run, lint, commit**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_extras_client.py -q
.\.venv\Scripts\python.exe -m ruff check src tests --fix; .\.venv\Scripts\python.exe -m ruff format src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp python/tests/test_v2_extras_client.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): v2 clients expose cache hints and icons and propagate trace context"
```

Expected: 4 tests pass; the full suite stays green.

---

### Task 4: TypeScript server — cache hints, icons, trace context

**Files:**
- Create: `typescript/src/icons.ts`
- Modify: `typescript/src/index.ts`, `typescript/src/tools/tool.ts`, `tools/tool-manager.ts`, `resources/resource.ts`, `resources/resource-manager.ts`, `prompts/prompt.ts`, `prompts/prompt-manager.ts`, `server.ts`, `v2/context.ts`, `v2/servicer.ts`
- Test: `typescript/tests/v2-extras-server.test.ts`

**Interfaces:**
- Produces: `export interface Icon { src: string; mimeType?: string; sizes?: string[]; theme?: string }`; `checkedIcons(icons?: Icon[]): Icon[]`; `icons?: Icon[]` on `ToolConfig`, `ResourceConfig`, `ResourceTemplateConfig`, `PromptConfig`, `RapidMCPOptions`; `icons: Icon[]` on the four `Registered*` types; `RapidMCPOptions.cacheTtlMs?: number`, `.cacheScope?: "private" | "public"`; `McpV2ServicerOptions.cache: CacheHint`, `.icons: Icon[]`; `V2Context.traceContext: Record<string, string>` (constructor's fifth argument).

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-extras-server.test.ts`:

```typescript
import { describe, it, expect, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { Metadata } from "nice-grpc-common";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import { CacheScope, McpDefinition, type McpClient } from "../generated/mcp_v2.js";

const META = { protocolVersion: "2026-07-28", clientCapabilities: { extensions: {} }, clientInfo: undefined };
const ICON = { src: "https://example.com/i.png", mimeType: "image/png", sizes: ["48x48"], theme: "dark" };

describe("cache hints, icons and trace context on v2", () => {
  let server: RapidMCP;
  let channel: Channel;

  async function start(opts: Partial<RapidMCPOptions> = {}): Promise<McpClient> {
    server = new RapidMCP({ name: "extras", version: "1.0", ...opts });
    server.addTool({ name: "echo", icons: [ICON], execute: async (a: any) => a.text });
    server.addTool({ name: "trace", execute: async (_a: unknown, ctx: any) => ({ ...ctx.traceContext }) });
    server.addResource({ uri: "res://a", name: "a", icons: [ICON], load: async () => ({ text: "a" }) });
    server.addResourceTemplate({
      uriTemplate: "res://items/{id}",
      name: "item",
      icons: [ICON],
      load: async () => ({ text: "i" }),
    });
    server.addPrompt({ name: "greet", icons: [ICON], load: async () => "hi" });
    const port = await server.listen();
    channel = createChannel(`127.0.0.1:${port}`);
    return createClientFactory().create(McpDefinition, channel);
  }

  afterEach(async () => {
    channel?.close();
    await server?.close();
  });

  async function everything(v2: McpClient) {
    let read: any;
    for await (const event of v2.readResource({ meta: META, uri: "res://a" })) read = (event.event as any).complete;
    return {
      discover: await v2.discover({ meta: META }),
      tools: await v2.listTools({ meta: META, cursor: "" }),
      resources: await v2.listResources({ meta: META, cursor: "" }),
      templates: await v2.listResourceTemplates({ meta: META, cursor: "" }),
      prompts: await v2.listPrompts({ meta: META, cursor: "" }),
      read,
    };
  }

  const hints = (results: Record<string, any>) =>
    Object.fromEntries(Object.entries(results).map(([name, r]) => [name, [r.cache.ttlMs, r.cache.scope]]));
  const all = (value: unknown) =>
    Object.fromEntries(["discover", "tools", "resources", "templates", "prompts", "read"].map((k) => [k, value]));

  it("defaults cache hints to immediately stale and private", async () => {
    const results = await everything(await start());

    expect(hints(results)).toEqual(all([0n, CacheScope.CACHE_SCOPE_PRIVATE]));
  });

  it("stamps configured cache hints on every cacheable result", async () => {
    const results = await everything(await start({ cacheTtlMs: 60_000, cacheScope: "public" }));

    expect(hints(results)).toEqual(all([60000n, CacheScope.CACHE_SCOPE_PUBLIC]));
  });

  it("rejects bad cache settings at construction", () => {
    expect(() => new RapidMCP({ name: "x", cacheTtlMs: -1 })).toThrow(/cacheTtlMs/);
    expect(() => new RapidMCP({ name: "x", cacheScope: "everyone" as any })).toThrow(/cacheScope/);
  });

  it("lists icons with their items", async () => {
    const results = await everything(await start({ icons: [ICON] }));

    expect(results.tools.tools.find((t) => t.name === "echo")!.icons).toEqual([ICON]);
    expect(results.tools.tools.find((t) => t.name === "trace")!.icons).toEqual([]);
    expect(results.resources.resources[0].icons).toEqual([ICON]);
    expect(results.templates.templates[0].icons).toEqual([ICON]);
    expect(results.prompts.prompts[0].icons).toEqual([ICON]);
  });

  it("sends the server's icons with discover only", async () => {
    const results = await everything(await start({ icons: [ICON] }));

    expect(results.discover.meta?.serverInfo?.icons).toEqual([ICON]);
    expect(results.tools.meta?.serverInfo?.icons).toEqual([]);
  });

  it.each(["javascript:alert(1)", "http://example.com/i.png", "file:///i.png", ""])(
    "rejects the icon source %j at registration",
    (src) => {
      const s = new RapidMCP({ name: "x" });

      expect(() => s.addTool({ name: "bad", icons: [{ src }], execute: async () => "x" })).toThrow(
        /https: or data:/,
      );
      expect(s.toolManager.listTools()).toEqual([]);
    },
  );

  it("accepts data: uri icons", () => {
    expect(() => new RapidMCP({ name: "x", icons: [{ src: "DATA:image/png;base64,AAAA" }] })).not.toThrow();
  });

  it("hands the tool the request's trace context and nothing else", async () => {
    const v2 = await start();
    const metadata = Metadata({
      traceparent: "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
      tracestate: "vendor=1",
      baggage: "user=ada",
      "x-other": "ignored",
    });

    let result: any;
    for await (const event of v2.callTool({ meta: META, name: "trace", arguments: "{}" }, { metadata })) {
      result = (event.event as any).complete;
    }

    expect(JSON.parse(result.structuredContent)).toEqual({
      traceparent: "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
      tracestate: "vendor=1",
      baggage: "user=ada",
    });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-extras-server.test.ts
```

Expected: FAIL — hints are always `0n` / private, icons absent, `traceContext` undefined, bad settings accepted.

- [ ] **Step 3: Icons**

`typescript/src/icons.ts`:

```typescript
/** An icon a client may show next to a tool, resource, prompt or server. */
export interface Icon {
  /** An https: URL or a data: URI. */
  src: string;
  mimeType?: string;
  /** e.g. "48x48", "any". */
  sizes?: string[];
  /** "light" | "dark". */
  theme?: string;
}

/** *icons* as a full list, refusing sources a client must not be asked to load. */
export function checkedIcons(icons: Icon[] | undefined): Icon[] {
  const result = icons ?? [];
  for (const icon of result) {
    const src = icon.src.toLowerCase();
    if (!src.startsWith("https://") && !src.startsWith("data:")) {
      throw new Error(`Icon src must be an https: or data: URI, got ${JSON.stringify(icon.src)}`);
    }
  }
  return result;
}

/** The wire form of an icon list. */
export function wireIcons(icons: Icon[]) {
  return icons.map((i) => ({
    src: i.src,
    mimeType: i.mimeType ?? "",
    sizes: i.sizes ?? [],
    theme: i.theme ?? "",
  }));
}
```

Export the type from `typescript/src/index.ts`: `export { type Icon } from "./icons.js";`.

Add `icons?: Icon[];` to `ToolConfig`, `ResourceConfig`, `ResourceTemplateConfig` and `PromptConfig`, and `icons: Icon[];` to `RegisteredTool`, `RegisteredResource`, `RegisteredResourceTemplate` and `RegisteredPrompt` (each file imports `type { Icon } from "../icons.js"`). In the three managers, each `add...` method sets `icons: checkedIcons(config.icons),` in the object it stores (import `checkedIcons`); `checkedIcons` runs before the `set`, so a bad icon registers nothing.

- [ ] **Step 4: Server options**

In `typescript/src/server.ts`, add to `RapidMCPOptions`:

```typescript
  /** How long clients may treat lists and resource reads as fresh, in ms. Default 0: always refetch. */
  cacheTtlMs?: number;
  /** Whether shared intermediaries may cache those results. Default "private". */
  cacheScope?: "private" | "public";
  /** Icons for the server itself, sent with discover. */
  icons?: Icon[];
```

validate and keep them in the constructor:

```typescript
    const cacheTtlMs = opts.cacheTtlMs ?? 0;
    const cacheScope = opts.cacheScope ?? "private";
    if (!(cacheTtlMs >= 0)) throw new Error(`cacheTtlMs must be 0 or more, got ${cacheTtlMs}`);
    if (cacheScope !== "private" && cacheScope !== "public") {
      throw new Error(`cacheScope must be "private" or "public", got ${JSON.stringify(cacheScope)}`);
    }
    this._cache = {
      ttlMs: BigInt(Math.floor(cacheTtlMs)),
      scope: cacheScope === "public" ? CacheScope.CACHE_SCOPE_PUBLIC : CacheScope.CACHE_SCOPE_PRIVATE,
    };
    this._icons = checkedIcons(opts.icons);
```

with fields `private _cache: CacheHint;` and `private _icons: Icon[];`, imports `import { CacheScope, type CacheHint } from "../generated/mcp_v2.js";` (merged with the existing generated-v2 import) and `import { checkedIcons, type Icon } from "./icons.js";`, and pass both to the v2 servicer:

```typescript
      cache: this._cache,
      icons: this._icons,
```

- [ ] **Step 5: Servicer and context**

In `typescript/src/v2/context.ts`, the constructor gains a fifth parameter:

```typescript
    private readonly _answers: Answers = {},
    /** W3C trace context of the request: traceparent, tracestate, baggage. */
    public readonly traceContext: Record<string, string> = {},
```

In `typescript/src/v2/servicer.ts`:

Add to the options:

```typescript
  /** Freshness hint stamped on every cacheable result. */
  cache: CacheHint;
  /** The server's own icons, sent with discover. */
  icons: Icon[];
```

with `import { wireIcons, type Icon } from "../icons.js";`. Delete the `NO_CACHE` constant (and the now-unused `CacheScope` import) and replace every `NO_CACHE` with `this._opts.cache`.

In `discover`, return the icons with the identity:

```typescript
      meta: {
        serverInfo: { name: this._opts.name, version: this._opts.version, icons: wireIcons(this._opts.icons) },
      },
```

Add `icons: wireIcons(t.icons),` to the objects built in `listTools`, `icons: wireIcons(r.icons),` in `listResources`, `icons: wireIcons(t.icons),` in `listResourceTemplates` and `icons: wireIcons(p.icons),` in `listPrompts`.

In `callTool`, collect the trace context and pass it:

```typescript
    const traceContext: Record<string, string> = {};
    for (const key of ["traceparent", "tracestate", "baggage"]) {
      const value = context.metadata.get(key);
      if (value !== undefined) traceContext[key] = value;
    }
    const ctx = new V2Context(
      request.meta!,
      (event) => queue.enqueue(event),
      context.signal,
      answers,
      traceContext,
    );
```

(replacing the existing `const ctx = new V2Context(...)` statement).

In `mount()` in `server.ts` nothing changes: the spread copies `icons` with the rest.

- [ ] **Step 6: Run and commit**

```powershell
cd typescript; npx tsc -p tsconfig.build.json --noEmit; npx vitest run tests/v2-extras-server.test.ts; npx vitest run
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src typescript/tests/v2-extras-server.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): cache hints, icons and trace context on v2"
```

Expected: type check clean; 11 tests pass; the full suite stays green.

---

### Task 5: TypeScript client — cache hints, icons, trace context

**Files:**
- Modify: `typescript/src/auth.ts` (`ClientOptions.traceContext`), `typescript/src/types.ts`, `typescript/src/v2/client-transport.ts`
- Test: `typescript/tests/v2-extras-client.test.ts`

**Interfaces:**
- Produces: `ClientOptions.traceContext?: () => Record<string, string>`; `ListResult<T>.ttlMs?: number`, `.cacheScope?: "private" | "public"`, the same two on `ReadResourceResult`; `icons?: Icon[]` on `Tool`, `Resource`, `ResourceTemplate`, `Prompt` and `ServerInfo` (absent when there are none).

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-extras-client.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";

const ICON = { src: "https://example.com/i.png", mimeType: "image/png", sizes: ["48x48"], theme: "dark" };

describe("a modern client sees cache hints and icons, and propagates trace context", () => {
  let server: RapidMCP;
  let port: number;
  let client: Client;

  beforeEach(async () => {
    server = new RapidMCP({ name: "extras", cacheTtlMs: 30_000, cacheScope: "public", icons: [ICON] });
    server.addTool({ name: "trace", icons: [ICON], execute: async (_a: unknown, ctx: any) => ({ ...ctx.traceContext }) });
    server.addResource({ uri: "res://a", name: "a", icons: [ICON], load: async () => ({ text: "a" }) });
    server.addResourceTemplate({ uriTemplate: "res://items/{id}", name: "item", icons: [ICON], load: async () => ({ text: "i" }) });
    server.addPrompt({ name: "greet", icons: [ICON], load: async () => "hi" });
    port = await server.listen();
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  it("sees cache hints on v2", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const results = [
      await client.listTools(),
      await client.listResources(),
      await client.listResourceTemplates(),
      await client.listPrompts(),
      await client.readResource("res://a"),
    ];

    expect(results.map((r) => [r.ttlMs, r.cacheScope])).toEqual(Array(5).fill([30_000, "public"]));
  });

  it("sees no cache hints or icons on v1", async () => {
    client = new Client(`127.0.0.1:${port}`);
    await client.connect();

    const tools = await client.listTools();
    const read = await client.readResource("res://a");

    expect([tools.ttlMs, tools.cacheScope, read.ttlMs, read.cacheScope]).toEqual([
      undefined,
      undefined,
      undefined,
      undefined,
    ]);
    expect(tools.items[0].icons).toBeUndefined();
    expect(client.serverInfo?.icons).toBeUndefined();
  });

  it("sees icons on v2", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    expect((await client.listTools()).items[0].icons).toEqual([ICON]);
    expect((await client.listResources()).items[0].icons).toEqual([ICON]);
    expect((await client.listResourceTemplates()).items[0].icons).toEqual([ICON]);
    expect((await client.listPrompts()).items[0].icons).toEqual([ICON]);
    expect(client.serverInfo?.icons).toEqual([ICON]);
  });

  it("calls the trace context provider per request and sends only the trace keys", async () => {
    const spans = ["00-aaaa-01", "00-bbbb-01"];
    client = new Client(`127.0.0.1:${port}`, {
      mode: "modern",
      traceContext: () => ({ traceparent: spans.shift()!, baggage: "user=ada", authorization: "nope" }),
    });
    await client.connect(); // discover does not consume a span: the provider is only for calls

    const first = await client.callTool("trace");
    const second = await client.callTool("trace");

    expect(first.structuredContent).toEqual({ traceparent: "00-aaaa-01", baggage: "user=ada" });
    expect(second.structuredContent).toEqual({ traceparent: "00-bbbb-01", baggage: "user=ada" });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-extras-client.test.ts
```

Expected: FAIL — `ttlMs` undefined on v2, icons missing, trace context empty.

- [ ] **Step 3: Types**

In `typescript/src/auth.ts`, add to `ClientOptions`:

```typescript
  /**
   * Called once per v2 request; may return traceparent, tracestate and baggage.
   * They travel as gRPC metadata and reach the tool as ctx.traceContext.
   */
  traceContext?: () => Record<string, string>;
```

In `typescript/src/types.ts`: `import type { Icon } from "./icons.js";`, add `icons?: Icon[];` to `Tool`, `Resource`, `ResourceTemplate`, `Prompt` and `ServerInfo`, add to `ReadResourceResult` and `ListResult<T>`:

```typescript
  /** Freshness hint from a v2 server, in ms; absent when the server sent none (v1). */
  ttlMs?: number;
  cacheScope?: "private" | "public";
```

and append:

```typescript
/** Icons of a listed item, or undefined when it has none (as on v1). */
export function convertIcons(
  icons: Array<{ src: string; mimeType: string; sizes: string[]; theme: string }> | undefined,
): Icon[] | undefined {
  if (!icons || icons.length === 0) return undefined;
  return icons.map((i) => ({ src: i.src, mimeType: i.mimeType, sizes: i.sizes, theme: i.theme }));
}

/** ttlMs / cacheScope from a result's cache hint. */
export function convertCacheHint(
  cache: { ttlMs: bigint; scope: number } | undefined,
): { ttlMs?: number; cacheScope?: "private" | "public" } {
  if (!cache) return {};
  return { ttlMs: Number(cache.ttlMs), cacheScope: cache.scope === 1 ? "public" : "private" };
}
```

- [ ] **Step 4: Transport**

In `typescript/src/v2/client-transport.ts`, import `convertCacheHint` and `convertIcons` from `../types.js`, and add:

```typescript
const TRACE_KEYS = ["traceparent", "tracestate", "baggage"];
```

```typescript
  /** The call's gRPC metadata: credentials, plus the current trace context. */
  private _callMetadata(traced = true): Metadata | undefined {
    const provider = traced ? this._opts.traceContext : undefined;
    if (!this._opts.token && !provider) return undefined;
    const metadata = this._opts.token ? buildMetadata(this._opts) : new Metadata();
    const current = provider?.() ?? {};
    for (const key of TRACE_KEYS) {
      if (current[key] !== undefined) metadata.set(key, current[key]);
    }
    return metadata;
  }
```

Use it in the three places that build call options. In `_call`, which gains a second parameter `traced = true`:

```typescript
    const metadata = this._callMetadata(traced);
    if (metadata) options.metadata = metadata;
```

(replacing `if (this._opts.token) options.metadata = buildMetadata(this._opts);`); the same two lines with `this._callMetadata()` in `_stream`; and with `this._callMetadata(false)` in `listen`. `discover` passes `false`: `this._call((o) => this._client.discover({ meta: this._meta() }, o), false)`.

Return the new fields:

- `discover`: add `icons: convertIcons(result.meta?.serverInfo?.icons),` to the returned object.
- `listTools`: `items: result.tools.map((t) => ({ ...convertToolV2(t), icons: convertIcons(t.icons) })),` and `...convertCacheHint(result.cache),`.
- `listResources`: `items: result.resources.map((r) => ({ ...convertResource(r), icons: convertIcons(r.icons) })),` and `...convertCacheHint(result.cache),`.
- `listResourceTemplates`: `items: result.templates.map((t) => ({ ...convertResourceTemplate(t), icons: convertIcons(t.icons) })),` and `...convertCacheHint(result.cache),`.
- `listPrompts`: `items: result.prompts.map((p) => ({ ...convertPrompt(p), icons: convertIcons(p.icons) })),` and `...convertCacheHint(result.cache),`.
- `readResource`: `return { ...convertReadResourceResult(wire), ...convertCacheHint(wire.cache) };`.

- [ ] **Step 5: Run and commit**

```powershell
cd typescript; npx tsc -p tsconfig.build.json --noEmit; npx vitest run tests/v2-extras-client.test.ts; npx vitest run
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src typescript/tests/v2-extras-client.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): v2 clients expose cache hints and icons and propagate trace context"
```

Expected: type check clean; 4 tests pass; the full suite stays green.

---

### Task 6: Cross-language tests

**Files:**
- Create: `python/tests/servers/interop.py`, `typescript/tests/interop/server.ts`
- Create: `python/tests/test_interop_typescript.py`, `typescript/tests/interop-python.test.ts`

**Interfaces:** Both servers print `PORT <n>` on stdout once listening, use `state_secret` / `stateSecret` `"interop"`, and register the same things: tools `echo(text)`, `add(a, b)` → `{"sum"}`, `chatty` (one info log, one progress `1/2`), `ask` (one form elicitation, returns `confirmed` or `declined`), `poke` (sends tools-list-changed, returns `poked`); resource `res://greeting` → `hello`; template `res://items/{id}` → `item <id>`; prompt `greet` with one required argument → `hi <value>`.

- [ ] **Step 1: The two servers**

`python/tests/servers/interop.py`:

```python
"""Server for the cross-language tests. Prints ``PORT <n>`` once it is listening."""

import asyncio

from rapidmcp import BoolField, Context, RapidMCP

server = RapidMCP(name="interop-python", version="1.0", host="127.0.0.1", state_secret="interop")


@server.tool()
async def echo(text: str) -> str:
    return text


@server.tool()
async def add(a: int, b: int) -> dict:
    return {"sum": a + b}


@server.tool()
async def chatty(ctx: Context) -> str:
    await ctx.info("working")
    await ctx.report_progress(1, 2)
    return "done"


@server.tool()
async def ask(ctx: Context) -> str:
    answer = await ctx.elicit("Confirm?", fields={"confirm": BoolField()})
    return "confirmed" if answer.accepted else "declined"


@server.tool()
async def poke() -> str:
    server.notify_tools_list_changed()
    return "poked"


@server.resource("res://greeting")
async def greeting() -> str:
    return "hello"


@server.resource_template("res://items/{id}")
async def item(id: str) -> str:
    return f"item {id}"


@server.prompt()
async def greet(who: str) -> str:
    return f"hi {who}"


async def _main() -> None:
    grpc_server = await server._start_grpc(0)
    print(f"PORT {server.port}", flush=True)
    await grpc_server.wait_for_termination()


asyncio.run(_main())
```

`typescript/tests/interop/server.ts`:

```typescript
/** Server for the cross-language tests. Prints `PORT <n>` once it is listening. */
import { RapidMCP } from "../../src/server.js";

const server = new RapidMCP({ name: "interop-typescript", version: "1.0", stateSecret: "interop" });

server.addTool({ name: "echo", execute: async (args: any) => args.text });
server.addTool({ name: "add", execute: async (args: any) => ({ sum: args.a + args.b }) });
server.addTool({
  name: "chatty",
  execute: async (_args: unknown, ctx: any) => {
    ctx.log.info("working");
    ctx.reportProgress(1, 2);
    return "done";
  },
});
server.addTool({
  name: "ask",
  execute: async (_args: unknown, ctx: any) => {
    const answer = await ctx.elicit("Confirm?", {
      type: "object",
      properties: { confirm: { type: "boolean" } },
    });
    return answer.action === "accept" ? "confirmed" : "declined";
  },
});
server.addTool({
  name: "poke",
  execute: async () => {
    server.notifyToolsListChanged();
    return "poked";
  },
});
server.addResource({ uri: "res://greeting", name: "greeting", load: async () => ({ text: "hello" }) });
server.addResourceTemplate({
  uriTemplate: "res://items/{id}",
  name: "item",
  load: async (args) => ({ text: `item ${args.id}` }),
});
server.addPrompt({
  name: "greet",
  arguments: [{ name: "who", required: true }],
  load: async (args) => `hi ${args.who}`,
});

const port = await server.listen();
console.log(`PORT ${port}`);
```

- [ ] **Step 2: Python client against the TypeScript server**

`python/tests/test_interop_typescript.py`:

```python
"""The Python client against the TypeScript server, over the v2 protocol."""

import asyncio
import json
import queue
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from rapidmcp import Client
from rapidmcp._generated import mcp_pb2

TS_DIR = Path(__file__).resolve().parents[2] / "typescript"
VITE_NODE = TS_DIR / "node_modules" / "vite-node" / "vite-node.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None or not VITE_NODE.exists(),
    reason="needs Node and the TypeScript package's installed dependencies",
)


@pytest.fixture(scope="module")
def ts_server():
    process = subprocess.Popen(
        [NODE, str(VITE_NODE), "tests/interop/server.ts"],
        cwd=TS_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(
        target=lambda: [lines.put(line) for line in process.stdout], daemon=True
    ).start()
    seen: list[str] = []
    try:
        while True:
            try:
                line = lines.get(timeout=60)
            except queue.Empty:
                raise RuntimeError("TypeScript server did not start:\n" + "".join(seen)) from None
            seen.append(line)
            if line.startswith("PORT "):
                yield f"127.0.0.1:{int(line.split()[1])}"
                break
    finally:
        process.kill()
        process.wait(timeout=10)


async def test_discovery_and_lists(ts_server):
    async with Client(ts_server, mode="modern") as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        prompts = await client.list_prompts()

        assert client.protocol == "v2"
        assert client.server_info.server_name == "interop-typescript"
        assert sorted(t.name for t in tools.items) == ["add", "ask", "chatty", "echo", "poke"]
        assert [r.uri for r in resources.items] == ["res://greeting"]
        assert [t.uri_template for t in templates.items] == ["res://items/{id}"]
        assert [(p.name, [a.name for a in p.arguments]) for p in prompts.items] == [("greet", ["who"])]


async def test_calls_structured_results_progress_and_logs(ts_server):
    client = Client(ts_server, mode="modern")
    progress, logs = [], []
    client.on_notification("progress", lambda payload: progress.append(json.loads(payload)))
    client.on_notification("log", lambda payload: logs.append(json.loads(payload)))

    async with client:
        echo = await client.call_tool("echo", {"text": "hola"})
        added = await client.call_tool("add", {"a": 2, "b": 3})
        chatty = await client.call_tool("chatty")

    assert echo.content[0].text == "hola"
    assert added.structured_content == {"sum": 5}
    assert chatty.content[0].text == "done"
    assert [(p["progress"], p["total"]) for p in progress] == [(1, 2)]
    assert [(entry["level"], entry["message"]) for entry in logs] == [("info", "working")]


async def test_an_input_round(ts_server):
    client = Client(ts_server, mode="modern")
    asked: list[str] = []

    async def handler(request):
        asked.append(request.message)
        return mcp_pb2.ElicitationResponse(action="accept", content='{"confirm": true}')

    client.set_elicitation_handler(handler)
    async with client:
        result = await client.call_tool("ask")

    assert (result.content[0].text, asked) == ("confirmed", ["Confirm?"])


async def test_resources_and_prompts(ts_server):
    async with Client(ts_server, mode="modern") as client:
        greeting = await client.read_resource("res://greeting")
        item = await client.read_resource("res://items/7")
        prompt = await client.get_prompt("greet", {"who": "Ada"})

    assert greeting.content[0].text == "hello"
    assert item.content[0].text == "item 7"
    assert [(m.role, m.content.text) for m in prompt.messages] == [("user", "hi Ada")]


async def test_errors_keep_their_codes(ts_server):
    from rapidmcp.errors import McpError

    async with Client(ts_server, mode="modern") as client:
        with pytest.raises(McpError) as unknown_tool:
            await client.call_tool("nope")
        with pytest.raises(McpError) as missing_argument:
            await client.get_prompt("greet")

    assert (unknown_tool.value.code, missing_argument.value.code) == (-32602, -32602)


async def test_a_notification(ts_server):
    client = Client(ts_server, mode="modern")
    seen: list[str] = []
    client.on_notification("tools_list_changed", lambda payload: seen.append("tools"))

    async with client:
        await client.call_tool("poke")
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.02)

    assert seen == ["tools"]
```

- [ ] **Step 3: TypeScript client against the Python server**

`typescript/tests/interop-python.test.ts`:

```typescript
/** The TypeScript client against the Python server, over the v2 protocol. */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";

const PYTHON_DIR = fileURLToPath(new URL("../../python/", import.meta.url));
const INTERPRETER = [".venv/Scripts/python.exe", ".venv/bin/python"]
  .map((relative) => PYTHON_DIR + relative)
  .find((path) => existsSync(path));

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe.skipIf(!INTERPRETER)("TypeScript client against the Python server", () => {
  let server: ChildProcess;
  let target: string;

  beforeAll(async () => {
    server = spawn(INTERPRETER!, ["tests/servers/interop.py"], { cwd: PYTHON_DIR });
    target = await new Promise<string>((resolve, reject) => {
      let output = "";
      const timer = setTimeout(() => reject(new Error(`Python server did not start:\n${output}`)), 60_000);
      const onData = (chunk: Buffer) => {
        output += chunk.toString();
        const match = /PORT (\d+)/.exec(output);
        if (match) {
          clearTimeout(timer);
          resolve(`127.0.0.1:${match[1]}`);
        }
      };
      server.stdout!.on("data", onData);
      server.stderr!.on("data", onData);
      server.on("exit", (code) => reject(new Error(`Python server exited with ${code}:\n${output}`)));
    });
  }, 70_000);

  afterAll(() => {
    server?.kill();
  });

  async function connected(opts: ConstructorParameters<typeof Client>[1] = {}) {
    const client = new Client(target, { mode: "modern", ...opts });
    return client;
  }

  it("discovers and lists", async () => {
    const client = await connected();
    await client.connect();

    const tools = await client.listTools();
    const resources = await client.listResources();
    const templates = await client.listResourceTemplates();
    const prompts = await client.listPrompts();
    await client.close();

    expect(client.protocol).toBeNull(); // closed
    expect(tools.items.map((t) => t.name).sort()).toEqual(["add", "ask", "chatty", "echo", "poke"]);
    expect(resources.items.map((r) => r.uri)).toEqual(["res://greeting"]);
    expect(templates.items.map((t) => t.uriTemplate)).toEqual(["res://items/{id}"]);
    expect(prompts.items.map((p) => [p.name, p.arguments.map((a) => a.name)])).toEqual([["greet", ["who"]]]);
  });

  it("identifies the server over v2", async () => {
    const client = await connected();
    await client.connect();

    expect([client.protocol, client.serverInfo?.serverName]).toEqual(["v2", "interop-python"]);
    await client.close();
  });

  it("calls tools, with structured results, progress and logs", async () => {
    const client = await connected();
    const progress: any[] = [];
    const logs: any[] = [];
    client.onNotification("progress", (payload) => void progress.push(JSON.parse(payload)));
    client.onNotification("log", (payload) => void logs.push(JSON.parse(payload)));
    await client.connect();

    const echo = await client.callTool("echo", { text: "hola" });
    const added = await client.callTool("add", { a: 2, b: 3 });
    const chatty = await client.callTool("chatty");
    await client.close();

    expect(echo.content[0].text).toBe("hola");
    expect(added.structuredContent).toEqual({ sum: 5 });
    expect(chatty.content[0].text).toBe("done");
    expect(progress.map((p) => [p.progress, p.total])).toEqual([[1, 2]]);
    expect(logs.map((l) => [l.level, l.message])).toEqual([["info", "working"]]);
  });

  it("completes an input round", async () => {
    const client = await connected();
    const asked: string[] = [];
    client.setElicitationHandler(async (request) => {
      asked.push(request.message);
      return { action: "accept", content: '{"confirm": true}' };
    });
    await client.connect();

    const result = await client.callTool("ask");
    await client.close();

    expect([result.content[0].text, asked]).toEqual(["confirmed", ["Confirm?"]]);
  });

  it("reads resources and gets prompts", async () => {
    const client = await connected();
    await client.connect();

    const greeting = await client.readResource("res://greeting");
    const item = await client.readResource("res://items/7");
    const prompt = await client.getPrompt("greet", { who: "Ada" });
    await client.close();

    expect(greeting.content[0].text).toBe("hello");
    expect(item.content[0].text).toBe("item 7");
    expect(prompt.messages.map((m) => [m.role, m.content.text])).toEqual([["user", "hi Ada"]]);
  });

  it("keeps error codes", async () => {
    const client = await connected();
    await client.connect();
    const code = (p: Promise<unknown>) =>
      p.then(
        () => null,
        (e: unknown) => (e instanceof McpError ? e.code : String(e)),
      );

    const codes = [await code(client.callTool("nope")), await code(client.getPrompt("greet"))];
    await client.close();

    expect(codes).toEqual([-32602, -32602]);
  });

  it("receives a notification", async () => {
    const client = await connected();
    const seen: string[] = [];
    client.onNotification("tools_list_changed", () => void seen.push("tools"));
    await client.connect();

    await client.callTool("poke");
    for (let i = 0; i < 100 && seen.length === 0; i++) await sleep(20);
    await client.close();

    expect(seen).toEqual(["tools"]);
  });
});
```

- [ ] **Step 4: Run both directions, then commit**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_interop_typescript.py -q
cd ..\typescript; npx vitest run tests/interop-python.test.ts
```

Expected: 6 Python tests and 7 TypeScript tests pass. A failure here is a real incompatibility between the two implementations: fix the implementation that departs from the spec, not the test.

```powershell
cd python; .\.venv\Scripts\python.exe -m ruff format tests; .\.venv\Scripts\python.exe -m ruff check tests
git -c safe.directory=D:/Trabajo/mcp-grpc add python/tests/servers/interop.py python/tests/test_interop_typescript.py typescript/tests/interop typescript/tests/interop-python.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "test: run each language's v2 client against the other language's server"
```
