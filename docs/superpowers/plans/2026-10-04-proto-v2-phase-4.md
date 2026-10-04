# Proto v2, Phase 4 Implementation Plan — subscriptions

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A v2 client receives list-changed and resource-updated notifications through one opt-in `Listen` stream, using the same `on_notification` / `subscribe_resource` calls it uses on v1.

**Architecture:** `Listen` is a server-streaming RPC. The request names what the client wants; the first event acknowledges it; after that the server sends only what was asked for. The server keeps a listener per open stream and nothing once it closes. The existing `notify_*` methods publish to v1 sessions and v2 listeners alike. The client derives the filter from its registered handlers and subscribed URIs and reopens the stream when that changes.

**Tech Stack:** Python 3.10+ (`grpcio` aio), TypeScript (`nice-grpc`).

**Spec:** `docs/superpowers/specs/2026-10-01-proto-v2-stateless-design.md`, section "Subscriptions".

## Global Constraints

- The first event on a `Listen` stream is `acknowledged`, carrying the filter the server will honour. Nothing is sent before it.
- A listener receives a list-changed event only if it asked for that kind, and `resource_updated` only for URIs in its `resource_subscriptions`.
- A server holds listener state only while the stream is open. A client that reconnects sends `Listen` again.
- Notification names and payloads handed to client handlers are the v1 ones: `tools_list_changed`, `prompts_list_changed`, `resources_list_changed` with payload `""`, and `resource_updated` with payload `{"uri": "<uri>"}`.
- `Listen` has no deadline. It ends when the client cancels it or the connection drops.
- Server `on_resource_subscribe` handlers run once per URI in a `Listen` request, after the acknowledgement.
- Roots-list-changed does not exist on v2; `notify_roots_list_changed` keeps raising `-32601` there.
- Python: project venv; `ruff format` and `ruff check` before each commit. Git: `-c safe.directory=D:/Trabajo/mcp-grpc`; no co-author or tool attribution in commit messages.

## Review Focus

1. **A listener that asked only for one kind** must not receive the others.
2. **A cancelled or dropped stream** must leave no listener behind on the server.
3. **A subscribe handler that throws** must not end the stream.
4. **A client with no notification handlers** must not open a stream at all.
5. **A handler registered after connecting** must start receiving without reconnecting.

---

### Task 1: Proto — `Listen`

**Files:**
- Modify: `proto/mcp_v2.proto`; regenerate the v2 stubs in both languages
- Modify: `python/tests/test_v2_proto.py`, `typescript/tests/v2-proto.test.ts`

- [ ] **Step 1: Update the stub tests**

In `python/tests/test_v2_proto.py`, add `"Listen",` after `"GetPrompt",` in the method list and replace the `streaming` assertion with:

```python
    assert streaming == {"CallTool", "ReadResource", "GetPrompt", "Listen"}
```

In `typescript/tests/v2-proto.test.ts`, add `"listen",` after `"getPrompt",` in the method list and, after the `getPrompt` stream assertion:

```typescript
    expect(McpDefinition.methods.listen.responseStream).toBe(true);
```

Run both; expected: FAIL on the method list.

- [ ] **Step 2: Extend the proto**

Add to `service Mcp`, after `rpc GetPrompt(...)`:

```proto
  rpc Listen(ListenRequest) returns (stream ListenEvent);
```

Append to the file:

```proto

// ── Subscriptions ─────────────────────────────────────────────────────────

// What a client wants to hear about. Unset / empty = not interested.
message NotificationFilter {
  bool tools_list_changed     = 1;
  bool prompts_list_changed   = 2;
  bool resources_list_changed = 3;
  repeated string resource_subscriptions = 4;  // URIs to get resource_updated for
}

message ListenRequest {
  RequestMeta meta = 1;
  NotificationFilter notifications = 2;
}

message ToolsListChanged {}
message PromptsListChanged {}
message ResourcesListChanged {}
message ResourceUpdated { string uri = 1; }

message ListenEvent {
  oneof event {
    NotificationFilter   acknowledged           = 1;  // always first: what the server will send
    ToolsListChanged     tools_list_changed     = 2;
    PromptsListChanged   prompts_list_changed   = 3;
    ResourcesListChanged resources_list_changed = 4;
    ResourceUpdated      resource_updated       = 5;
  }
}
```

- [ ] **Step 3: Regenerate v2 only, verify, commit**

```powershell
cd python; .\.venv\Scripts\python.exe generate.py mcp_v2.proto
cd ..\typescript; npm run generate -- --path ../proto/mcp_v2.proto
```

Stub tests pass; the Python suite passes; the TypeScript build does not type-check until Task 4.

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add proto python/src/rapidmcp/_generated python/tests/test_v2_proto.py typescript/generated/mcp_v2.ts typescript/tests/v2-proto.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(proto): v2 Listen stream for opt-in notifications"
```

---

### Task 2: Python server — `Listen`

**Files:**
- Create: `python/src/rapidmcp/_v2_listen.py`
- Modify: `python/src/rapidmcp/server.py` (`_v2_listeners`, `notify_*`)
- Modify: `python/src/rapidmcp/_v2_servicer.py` (`Listen`)
- Test: `python/tests/test_v2_listen_server.py`

**Interfaces:**
- Produces: `rapidmcp._v2_listen._Listeners` with `add(notifications) -> _Listener`, `remove(listener)`, `__len__`, `tools_list_changed()`, `prompts_list_changed()`, `resources_list_changed()`, `resource_updated(uri)`; `_Listener.queue: asyncio.Queue[pb.ListenEvent]`; `server._v2_listeners`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_listen_server.py`:

```python
"""v2 Listen: an opt-in stream of notifications, acknowledged first."""

import asyncio

import grpc
import pytest
from grpc import aio

from rapidmcp import RapidMCP
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc

META = pb.RequestMeta(protocol_version="2026-07-28", client_capabilities=pb.ClientCapabilities())


@pytest.fixture
async def pair():
    srv = RapidMCP(name="listen", version="1.0")
    async with srv, aio.insecure_channel(f"localhost:{srv.port}") as channel:
        yield srv, mcp_v2_pb2_grpc.McpStub(channel)


def _listen(stub, **wanted):
    return stub.Listen(pb.ListenRequest(meta=META, notifications=pb.NotificationFilter(**wanted)))


async def _next(call) -> pb.ListenEvent:
    return await asyncio.wait_for(call.read(), timeout=3)


async def _acknowledged(call) -> pb.NotificationFilter:
    event = await _next(call)
    assert event.WhichOneof("event") == "acknowledged"
    return event.acknowledged


async def test_the_first_event_acknowledges_the_filter(pair):
    _, stub = pair
    call = _listen(stub, tools_list_changed=True, resource_subscriptions=["res://a"])

    accepted = await _acknowledged(call)

    assert accepted.tools_list_changed is True
    assert accepted.prompts_list_changed is False
    assert list(accepted.resource_subscriptions) == ["res://a"]
    call.cancel()


async def test_a_listener_gets_only_the_kinds_it_asked_for(pair):
    srv, stub = pair
    call = _listen(stub, prompts_list_changed=True)
    await _acknowledged(call)

    srv.notify_tools_list_changed()  # not asked for
    srv.notify_resources_list_changed()  # not asked for
    srv.notify_prompts_list_changed()

    assert (await _next(call)).WhichOneof("event") == "prompts_list_changed"
    call.cancel()


async def test_resource_updates_arrive_only_for_subscribed_uris(pair):
    srv, stub = pair
    call = _listen(stub, resource_subscriptions=["res://mine"])
    await _acknowledged(call)

    srv.notify_resource_updated("res://other")
    srv.notify_resource_updated("res://mine")

    event = await _next(call)
    assert (event.WhichOneof("event"), event.resource_updated.uri) == ("resource_updated", "res://mine")
    call.cancel()


async def test_each_listener_is_served_independently(pair):
    srv, stub = pair
    tools = _listen(stub, tools_list_changed=True)
    prompts = _listen(stub, prompts_list_changed=True)
    await _acknowledged(tools)
    await _acknowledged(prompts)

    srv.notify_prompts_list_changed()
    srv.notify_tools_list_changed()

    assert (await _next(tools)).WhichOneof("event") == "tools_list_changed"
    assert (await _next(prompts)).WhichOneof("event") == "prompts_list_changed"
    tools.cancel()
    prompts.cancel()


async def test_subscribe_handlers_run_for_each_uri_and_may_fail(pair):
    srv, stub = pair
    seen: list[str] = []

    def explode(uri: str) -> None:
        raise RuntimeError("handler bug")

    async def record(uri: str) -> None:
        seen.append(uri)

    srv.on_resource_subscribe(explode)
    srv.on_resource_subscribe(record)
    call = _listen(stub, resource_subscriptions=["res://a", "res://b"])
    await _acknowledged(call)

    srv.notify_resource_updated("res://b")  # the stream survived the failing handler

    assert (await _next(call)).resource_updated.uri == "res://b"
    assert seen == ["res://a", "res://b"]
    call.cancel()


async def test_a_cancelled_stream_leaves_no_listener_behind(pair):
    srv, stub = pair
    call = _listen(stub, tools_list_changed=True)
    await _acknowledged(call)
    assert len(srv._v2_listeners) == 1

    call.cancel()
    for _ in range(40):
        if len(srv._v2_listeners) == 0:
            break
        await asyncio.sleep(0.05)

    assert len(srv._v2_listeners) == 0
    srv.notify_tools_list_changed()  # publishing to nobody is fine


async def test_listen_without_meta_is_invalid_params(pair):
    _, stub = pair
    call = stub.Listen(pb.ListenRequest())

    with pytest.raises(aio.AioRpcError) as exc:
        await call.read()

    assert dict(exc.value.trailing_metadata())["mcp-error-code"] == "-32602"
    assert exc.value.code() is grpc.StatusCode.INVALID_ARGUMENT
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_listen_server.py -q
```

Expected: every test fails with gRPC `UNIMPLEMENTED`.

- [ ] **Step 3: The listener registry**

`python/src/rapidmcp/_v2_listen.py`:

```python
"""Open v2 Listen streams and what each one asked for.

The server holds a listener only while its stream is open; nothing about a
subscription survives the stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from rapidmcp._generated import mcp_v2_pb2 as pb


@dataclass(eq=False)
class _Listener:
    notifications: pb.NotificationFilter
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


class _Listeners:
    def __init__(self) -> None:
        self._open: list[_Listener] = []

    def __len__(self) -> int:
        return len(self._open)

    def add(self, notifications: pb.NotificationFilter) -> _Listener:
        listener = _Listener(notifications)
        self._open.append(listener)
        return listener

    def remove(self, listener: _Listener) -> None:
        if listener in self._open:
            self._open.remove(listener)

    def _publish(self, event: pb.ListenEvent, wanted: Callable[[pb.NotificationFilter], bool]) -> None:
        for listener in self._open:
            if wanted(listener.notifications):
                listener.queue.put_nowait(event)

    def tools_list_changed(self) -> None:
        self._publish(
            pb.ListenEvent(tools_list_changed=pb.ToolsListChanged()),
            lambda wanted: wanted.tools_list_changed,
        )

    def prompts_list_changed(self) -> None:
        self._publish(
            pb.ListenEvent(prompts_list_changed=pb.PromptsListChanged()),
            lambda wanted: wanted.prompts_list_changed,
        )

    def resources_list_changed(self) -> None:
        self._publish(
            pb.ListenEvent(resources_list_changed=pb.ResourcesListChanged()),
            lambda wanted: wanted.resources_list_changed,
        )

    def resource_updated(self, uri: str) -> None:
        self._publish(
            pb.ListenEvent(resource_updated=pb.ResourceUpdated(uri=uri)),
            lambda wanted: uri in wanted.resource_subscriptions,
        )
```

- [ ] **Step 4: Publish to listeners, and serve `Listen`**

In `python/src/rapidmcp/server.py`: import `from rapidmcp._v2_listen import _Listeners`, add `self._v2_listeners = _Listeners()` next to `self._session_queues`, and add one line at the end of each `notify_*` method:

```python
        self._v2_listeners.tools_list_changed()
```

```python
        self._v2_listeners.resources_list_changed()
```

```python
        self._v2_listeners.resource_updated(uri)
```

```python
        self._v2_listeners.prompts_list_changed()
```

(in `notify_tools_list_changed`, `notify_resources_list_changed`, `notify_resource_updated` and `notify_prompts_list_changed` respectively).

Append to `_McpV2Servicer` in `python/src/rapidmcp/_v2_servicer.py`:

```python
    async def Listen(self, request, context):
        await self._check_meta(request, context)
        wanted = request.notifications
        listener = self._server._v2_listeners.add(wanted)
        try:
            yield pb.ListenEvent(acknowledged=wanted)
            for uri in wanted.resource_subscriptions:
                for handler in self._server._subscribe_handlers:
                    try:
                        await _invoke(handler, uri)
                    except Exception:
                        logger.exception("Subscribe handler for '%s' raised", uri)
            while True:
                yield await listener.queue.get()
        finally:
            # Cancelled by the client, or the connection dropped: forget the listener.
            self._server._v2_listeners.remove(listener)
```

- [ ] **Step 5: Run, lint, commit**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_listen_server.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp python/tests/test_v2_listen_server.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): v2 Listen stream; notify_* reach v2 listeners"
```

Expected: 7 tests pass; the full suite stays green.

---

### Task 3: Python client — notifications on v2

**Files:**
- Modify: `python/src/rapidmcp/_v2_client.py` (`listen`)
- Modify: `python/src/rapidmcp/client.py`
- Modify: `python/tests/test_v2_client.py` (one test names a different operation)
- Test: `python/tests/test_v2_listen_client.py`

**Interfaces:**
- Produces: `_V2Transport.listen(*, tools_list_changed, prompts_list_changed, resources_list_changed, uris, ready)`; `Client.subscribe_resource(uri)` and `Client.on_notification(...)` working on v2; `Client._refresh_listen()`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_v2_listen_client.py`:

```python
"""on_notification / subscribe_resource behave the same on both protocol versions."""

import asyncio
import json

import pytest

from rapidmcp import Client, RapidMCP


@pytest.fixture
async def server():
    srv = RapidMCP(name="listen", version="1.0")
    async with srv:
        yield srv


async def _until(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition never became true"
        await asyncio.sleep(0.02)


@pytest.mark.parametrize("mode", ["legacy", "modern"])
async def test_list_changed_notifications_reach_handlers(server, mode):
    client = Client(f"localhost:{server.port}", mode=mode)
    seen: list[tuple[str, str]] = []
    for kind in ("tools_list_changed", "prompts_list_changed", "resources_list_changed"):
        client.on_notification(kind, lambda payload, kind=kind: seen.append((kind, payload)))

    async with client:
        await client.ping()  # on v1 this proves the session is registered for broadcasts
        server.notify_tools_list_changed()
        server.notify_prompts_list_changed()
        server.notify_resources_list_changed()
        await _until(lambda: len(seen) == 3)

    assert seen == [
        ("tools_list_changed", ""),
        ("prompts_list_changed", ""),
        ("resources_list_changed", ""),
    ]


@pytest.mark.parametrize("mode", ["legacy", "modern"])
async def test_subscribed_resource_updates_reach_the_handler(server, mode):
    client = Client(f"localhost:{server.port}", mode=mode)
    updates: list[dict] = []
    client.on_notification("resource_updated", lambda payload: updates.append(json.loads(payload)))

    async with client:
        await client.subscribe_resource("res://mine")
        await client.ping()
        server.notify_resource_updated("res://mine")
        await _until(lambda: len(updates) == 1)

    assert updates == [{"uri": "res://mine"}]


async def test_modern_client_hears_only_about_uris_it_subscribed_to(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    updates: list[str] = []
    client.on_notification(
        "resource_updated", lambda payload: updates.append(json.loads(payload)["uri"])
    )

    async with client:
        await client.subscribe_resource("res://mine")
        server.notify_resource_updated("res://other")
        server.notify_resource_updated("res://mine")
        await _until(lambda: len(updates) == 1)

    assert updates == ["res://mine"]


async def test_modern_client_without_handlers_opens_no_stream(server):
    async with Client(f"localhost:{server.port}", mode="modern") as client:
        await client.ping()

        assert len(server._v2_listeners) == 0


async def test_a_handler_registered_after_connecting_starts_receiving(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    seen: list[str] = []

    async with client:
        client.on_notification("tools_list_changed", lambda payload: seen.append("tools"))
        await _until(lambda: len(server._v2_listeners) == 1)
        server.notify_tools_list_changed()
        await _until(lambda: seen == ["tools"])


async def test_closing_the_client_ends_its_subscription(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    client.on_notification("tools_list_changed", lambda payload: None)

    async with client:
        assert len(server._v2_listeners) == 1

    await _until(lambda: len(server._v2_listeners) == 0)
```

In `python/tests/test_v2_client.py`, in `test_modern_client_says_which_operations_v2_lacks`, replace `await client.subscribe_resource("res://a")` with:

```python
            await client.notify_roots_list_changed()
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_listen_client.py -q
```

Expected: the `legacy` cases pass; the `modern` cases fail (`subscribe_resource is not available on the v2 protocol yet`, or no notification ever arriving).

- [ ] **Step 3: The transport's `listen`**

In `python/src/rapidmcp/_v2_client.py`, add `import asyncio`, replace `_notify` with a pair of methods, and add `listen`:

```python
    async def _dispatch(self, kind: str, payload: str) -> None:
        try:
            await self._notifications.dispatch(kind, payload)
        except Exception:
            logger.exception("Notification handler for '%s' raised", kind)

    async def _notify(self, kind: str, payload: dict) -> None:
        await self._dispatch(kind, json.dumps(payload))

    async def listen(
        self,
        *,
        tools_list_changed: bool,
        prompts_list_changed: bool,
        resources_list_changed: bool,
        uris: list[str],
        ready: asyncio.Event,
    ) -> None:
        """Hold a Listen stream open, handing its notifications to the registered handlers.

        Sets *ready* once the server has acknowledged the subscription (or the
        stream has ended, so a waiter never hangs). Runs until cancelled.
        """
        request = pb.ListenRequest(
            meta=self._meta(),
            notifications=pb.NotificationFilter(
                tools_list_changed=tools_list_changed,
                prompts_list_changed=prompts_list_changed,
                resources_list_changed=resources_list_changed,
                resource_subscriptions=uris,
            ),
        )
        call = self._stub.Listen(request, metadata=self._metadata)  # long-lived: no deadline
        try:
            async for event in call:
                kind = event.WhichOneof("event")
                if kind == "acknowledged":
                    ready.set()
                elif kind == "resource_updated":
                    await self._notify("resource_updated", {"uri": event.resource_updated.uri})
                elif kind is not None:
                    await self._dispatch(kind, "")
        except aio.AioRpcError as exc:
            logger.warning("subscription stream ended: %s", exc.code())
        finally:
            call.cancel()
            ready.set()
```

- [ ] **Step 4: The client keeps one subscription matching what it registered**

In `python/src/rapidmcp/client.py`:

Add `import contextlib` to the imports. In `__init__`, next to `self._v2`:

```python
        self._subscribed_uris: list[str] = []
        self._listen_task: asyncio.Task | None = None
        self._listen_lock = asyncio.Lock()
```

Add, below `_v1_only`:

```python
    async def _stop_listening(self) -> None:
        task, self._listen_task = self._listen_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _refresh_listen(self) -> None:
        """(Re)open the v2 subscription so it matches the handlers and URIs registered now."""
        if self._v2 is None:
            return
        async with self._listen_lock:
            await self._stop_listening()
            wanted = {
                kind: self._notifications.has(kind)
                for kind in ("tools_list_changed", "prompts_list_changed", "resources_list_changed")
            }
            if not any(wanted.values()) and not self._subscribed_uris:
                return
            ready = asyncio.Event()
            self._listen_task = asyncio.create_task(
                self._v2.listen(**wanted, uris=list(self._subscribed_uris), ready=ready)
            )
            await asyncio.wait_for(ready.wait(), timeout=self._request_timeout)
```

Change the message in `_v1_only` (the operations left are not coming to v2):

```python
                f"{operation} is not part of the v2 protocol; use mode='legacy'",
```

In `_connect_v2`, after `self._v2 = transport`:

```python
        await self._refresh_listen()
```

Replace the first line of `subscribe_resource`'s body (`self._v1_only("subscribe_resource")`) with:

```python
        if self._v2 is not None:
            if uri not in self._subscribed_uris:
                self._subscribed_uris.append(uri)
            await self._refresh_listen()
            return
```

Replace `on_notification` with:

```python
    def on_notification(self, notification_type: str, handler) -> None:
        self._notifications.register(notification_type, handler)
        if self._v2 is not None:
            # Already connected over v2: widen the subscription in the background.
            with contextlib.suppress(RuntimeError):  # no running loop: nothing to refresh
                task = asyncio.get_running_loop().create_task(self._refresh_listen())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
```

In `close()`, as the first statement after the debug log line:

```python
        await self._stop_listening()
```

- [ ] **Step 5: Run, lint, commit**

```powershell
cd python; .\.venv\Scripts\python.exe -m pytest tests/test_v2_listen_client.py tests/test_v2_client.py -q
.\.venv\Scripts\python.exe -m ruff format src tests; .\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_tls_docker.py --ignore=tests/test_stress_subprocess.py
git -c safe.directory=D:/Trabajo/mcp-grpc add python/src/rapidmcp python/tests/test_v2_listen_client.py python/tests/test_v2_client.py
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(py): v2 clients receive notifications through Listen"
```

Expected: 8 tests pass in the new file; the full suite stays green.

---

### Task 4: TypeScript server — `listen`

**Files:**
- Create: `typescript/src/v2/listeners.ts`
- Modify: `typescript/src/server.ts`, `typescript/src/v2/servicer.ts`
- Test: `typescript/tests/v2-listen-server.test.ts`

**Interfaces:**
- Produces: `class Listeners` with `add(filter, send): Listener`, `remove(listener)`, `size`, `toolsListChanged()`, `promptsListChanged()`, `resourcesListChanged()`, `resourceUpdated(uri)`; `McpV2ServicerOptions.listeners: Listeners` and `.subscribeHandlers: Array<(uri: string) => void | Promise<void>>`; `server._listeners`.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-listen-server.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status } from "nice-grpc-common";
import { RapidMCP } from "../src/server.js";
import { McpDefinition, type ListenEvent, type McpClient, type NotificationFilter } from "../generated/mcp_v2.js";

const META = { protocolVersion: "2026-07-28", clientCapabilities: { extensions: {} }, clientInfo: undefined };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("v2 listen", () => {
  let server: RapidMCP;
  let channel: Channel;
  let v2: McpClient;
  let open: AbortController[];

  beforeEach(async () => {
    open = [];
    server = new RapidMCP({ name: "listen" });
    const port = await server.listen();
    channel = createChannel(`127.0.0.1:${port}`);
    v2 = createClientFactory().create(McpDefinition, channel);
  });

  afterEach(async () => {
    for (const controller of open) controller.abort();
    channel.close();
    await server.close();
  });

  /** Open a stream; returns a reader for its events one at a time. */
  function listen(wanted: Partial<NotificationFilter>) {
    const controller = new AbortController();
    open.push(controller);
    const iterator = v2
      .listen(
        {
          meta: META,
          notifications: {
            toolsListChanged: false,
            promptsListChanged: false,
            resourcesListChanged: false,
            resourceSubscriptions: [],
            ...wanted,
          },
        },
        { signal: controller.signal },
      )
      [Symbol.asyncIterator]();
    return {
      controller,
      next: async (): Promise<NonNullable<ListenEvent["event"]>> => {
        const result = await iterator.next();
        if (result.done) throw new Error("stream ended");
        return result.value.event!;
      },
    };
  }

  async function acknowledged(stream: ReturnType<typeof listen>): Promise<NotificationFilter> {
    const event = await stream.next();
    expect(event.$case).toBe("acknowledged");
    return (event as any).acknowledged;
  }

  const listeners = () => (server as any)._listeners.size as number;

  it("acknowledges the filter first", async () => {
    const stream = listen({ toolsListChanged: true, resourceSubscriptions: ["res://a"] });

    const accepted = await acknowledged(stream);

    expect(accepted.toolsListChanged).toBe(true);
    expect(accepted.promptsListChanged).toBe(false);
    expect(accepted.resourceSubscriptions).toEqual(["res://a"]);
  });

  it("sends a listener only the kinds it asked for", async () => {
    const stream = listen({ promptsListChanged: true });
    await acknowledged(stream);

    server.notifyToolsListChanged(); // not asked for
    server.notifyResourcesListChanged(); // not asked for
    server.notifyPromptsListChanged();

    expect((await stream.next()).$case).toBe("promptsListChanged");
  });

  it("sends resource updates only for subscribed uris", async () => {
    const stream = listen({ resourceSubscriptions: ["res://mine"] });
    await acknowledged(stream);

    server.notifyResourceUpdated("res://other");
    server.notifyResourceUpdated("res://mine");

    const event = await stream.next();
    expect([event.$case, (event as any).resourceUpdated.uri]).toEqual(["resourceUpdated", "res://mine"]);
  });

  it("serves each listener independently", async () => {
    const tools = listen({ toolsListChanged: true });
    const prompts = listen({ promptsListChanged: true });
    await acknowledged(tools);
    await acknowledged(prompts);

    server.notifyPromptsListChanged();
    server.notifyToolsListChanged();

    expect((await tools.next()).$case).toBe("toolsListChanged");
    expect((await prompts.next()).$case).toBe("promptsListChanged");
  });

  it("runs subscribe handlers for each uri, and survives one that throws", async () => {
    const seen: string[] = [];
    server.onResourceSubscribe(() => {
      throw new Error("handler bug");
    });
    server.onResourceSubscribe(async (uri) => void seen.push(uri));
    const stream = listen({ resourceSubscriptions: ["res://a", "res://b"] });
    await acknowledged(stream);
    await sleep(50);

    server.notifyResourceUpdated("res://b");

    expect(((await stream.next()) as any).resourceUpdated.uri).toBe("res://b");
    expect(seen).toEqual(["res://a", "res://b"]);
  });

  it("forgets a listener whose stream was cancelled", async () => {
    const stream = listen({ toolsListChanged: true });
    await acknowledged(stream);
    expect(listeners()).toBe(1);

    stream.controller.abort();
    for (let i = 0; i < 40 && listeners() > 0; i++) await sleep(50);

    expect(listeners()).toBe(0);
    server.notifyToolsListChanged(); // publishing to nobody is fine
  });

  it("rejects a listen request without meta", async () => {
    let trailer = new Metadata();
    const iterator = v2.listen({}, { onTrailer: (t) => (trailer = t) })[Symbol.asyncIterator]();

    const err = await iterator.next().then(
      () => null,
      (e: unknown) => e,
    );

    expect((err as ClientError).code).toBe(Status.INVALID_ARGUMENT);
    expect(trailer.get("mcp-error-code")).toBe("-32602");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-listen-server.test.ts
```

Expected: every test fails (`UNIMPLEMENTED`, or the server refusing to register a service with a missing method).

- [ ] **Step 3: The listener registry**

`typescript/src/v2/listeners.ts`:

```typescript
/**
 * Open v2 Listen streams and what each one asked for. The server holds a
 * listener only while its stream is open; nothing about a subscription
 * survives the stream.
 */
import type { DeepPartial, ListenEvent, NotificationFilter } from "../../generated/mcp_v2.js";

export interface Listener {
  filter: NotificationFilter;
  send: (event: DeepPartial<ListenEvent>) => void;
}

export class Listeners {
  private _open = new Set<Listener>();

  get size(): number {
    return this._open.size;
  }

  add(filter: NotificationFilter, send: Listener["send"]): Listener {
    const listener = { filter, send };
    this._open.add(listener);
    return listener;
  }

  remove(listener: Listener): void {
    this._open.delete(listener);
  }

  private _publish(event: DeepPartial<ListenEvent>, wanted: (filter: NotificationFilter) => boolean): void {
    for (const listener of this._open) {
      if (wanted(listener.filter)) listener.send(event);
    }
  }

  toolsListChanged(): void {
    this._publish({ event: { $case: "toolsListChanged", toolsListChanged: {} } }, (f) => f.toolsListChanged);
  }

  promptsListChanged(): void {
    this._publish(
      { event: { $case: "promptsListChanged", promptsListChanged: {} } },
      (f) => f.promptsListChanged,
    );
  }

  resourcesListChanged(): void {
    this._publish(
      { event: { $case: "resourcesListChanged", resourcesListChanged: {} } },
      (f) => f.resourcesListChanged,
    );
  }

  resourceUpdated(uri: string): void {
    this._publish({ event: { $case: "resourceUpdated", resourceUpdated: { uri } } }, (f) =>
      f.resourceSubscriptions.includes(uri),
    );
  }
}
```

- [ ] **Step 4: Publish to listeners, and serve `listen`**

In `typescript/src/server.ts`: `import { Listeners } from "./v2/listeners.js";`, add the field `private _listeners = new Listeners();`, pass to the v2 servicer:

```typescript
      listeners: this._listeners,
      subscribeHandlers: this._subscribeHandlers,
```

and add one line to each notify method, before its `_broadcast` call:

```typescript
    this._listeners.toolsListChanged();
```

```typescript
    this._listeners.resourcesListChanged();
```

```typescript
    this._listeners.resourceUpdated(uri);
```

```typescript
    this._listeners.promptsListChanged();
```

(in `notifyToolsListChanged`, `notifyResourcesListChanged`, `notifyResourceUpdated`, `notifyPromptsListChanged` respectively).

In `typescript/src/v2/servicer.ts`: add `type ListenEvent`, `type ListenRequest`, `type NotificationFilter` to the generated type import, `import type { Listeners } from "./listeners.js";`, two options:

```typescript
  /** Open Listen streams; the server's notify methods publish through it. */
  listeners: Listeners;
  /** Called with each uri a Listen request subscribes to. */
  subscribeHandlers: Array<(uri: string) => void | Promise<void>>;
```

and append to the class:

```typescript
  async *listen(
    request: ListenRequest,
    context: CallContext,
  ): AsyncGenerator<DeepPartial<ListenEvent>> {
    this._checkMeta(request.meta, context);
    const wanted: NotificationFilter = request.notifications ?? {
      toolsListChanged: false,
      promptsListChanged: false,
      resourcesListChanged: false,
      resourceSubscriptions: [],
    };

    const END = Symbol("end");
    const queue = new AsyncQueue<DeepPartial<ListenEvent> | typeof END>();
    const listener = this._opts.listeners.add(wanted, (event) => queue.enqueue(event));
    const stop = () => queue.enqueue(END);
    context.signal.addEventListener("abort", stop, { once: true });

    try {
      yield { event: { $case: "acknowledged", acknowledged: wanted } };
      for (const uri of wanted.resourceSubscriptions) {
        for (const handler of this._opts.subscribeHandlers) {
          try {
            await handler(uri);
          } catch (err) {
            console.error(`[rapidmcp] resource subscribe handler failed:`, err);
          }
        }
      }
      for (;;) {
        const item = await queue.dequeue();
        if (item === END) return;
        yield item;
      }
    } finally {
      // Cancelled by the client, or the connection dropped: forget the listener.
      context.signal.removeEventListener("abort", stop);
      this._opts.listeners.remove(listener);
    }
  }
```

- [ ] **Step 5: Run and commit**

```powershell
cd typescript; npx tsc -p tsconfig.build.json --noEmit; npx vitest run tests/v2-listen-server.test.ts; npx vitest run
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src typescript/tests/v2-listen-server.test.ts
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): v2 listen stream; notify methods reach v2 listeners"
```

Expected: type check clean; 7 tests pass; the full suite stays green.

---

### Task 5: TypeScript client — notifications on v2

**Files:**
- Modify: `typescript/src/v2/client-transport.ts` (`listen`)
- Modify: `typescript/src/client.ts`
- Modify: `typescript/tests/v2-client.test.ts`
- Modify: `CHANGELOG.md`
- Test: `typescript/tests/v2-listen-client.test.ts`

**Interfaces:**
- Produces: `V2Transport.listen(filter: NotificationFilter, signal: AbortSignal): Promise<void>` (resolves once acknowledged; keeps delivering until aborted); `Client.subscribeResource(uri): Promise<void>` (was `void`); `Client.onNotification` working on v2.

- [ ] **Step 1: Write the failing test**

`typescript/tests/v2-listen-client.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function until(condition: () => boolean, timeout = 3000): Promise<void> {
  const deadline = Date.now() + timeout;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error("condition never became true");
    await sleep(20);
  }
}

describe("notifications behave the same on both protocol versions", () => {
  let server: RapidMCP;
  let port: number;
  let client: Client;

  beforeEach(async () => {
    server = new RapidMCP({ name: "listen" });
    port = await server.listen();
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  const listeners = () => (server as any)._listeners.size as number;

  it.each(["legacy", "modern"] as const)("delivers list-changed notifications (%s)", async (mode) => {
    client = new Client(`127.0.0.1:${port}`, { mode });
    const seen: Array<[string, string]> = [];
    for (const kind of ["tools_list_changed", "prompts_list_changed", "resources_list_changed"]) {
      client.onNotification(kind, (payload) => void seen.push([kind, payload]));
    }
    await client.connect();
    await client.ping(); // on v1 this proves the session is registered for broadcasts

    server.notifyToolsListChanged();
    server.notifyPromptsListChanged();
    server.notifyResourcesListChanged();
    await until(() => seen.length === 3);

    expect(seen).toEqual([
      ["tools_list_changed", ""],
      ["prompts_list_changed", ""],
      ["resources_list_changed", ""],
    ]);
  });

  it.each(["legacy", "modern"] as const)("delivers updates for a subscribed resource (%s)", async (mode) => {
    client = new Client(`127.0.0.1:${port}`, { mode });
    const updates: unknown[] = [];
    client.onNotification("resource_updated", (payload) => void updates.push(JSON.parse(payload)));
    await client.connect();

    await client.subscribeResource("res://mine");
    await client.ping();
    server.notifyResourceUpdated("res://mine");
    await until(() => updates.length === 1);

    expect(updates).toEqual([{ uri: "res://mine" }]);
  });

  it("hears only about uris it subscribed to on v2", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const updates: string[] = [];
    client.onNotification("resource_updated", (payload) => void updates.push(JSON.parse(payload).uri));
    await client.connect();
    await client.subscribeResource("res://mine");

    server.notifyResourceUpdated("res://other");
    server.notifyResourceUpdated("res://mine");
    await until(() => updates.length === 1);

    expect(updates).toEqual(["res://mine"]);
  });

  it("opens no stream when it has no notification handlers", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();
    await client.ping();

    expect(listeners()).toBe(0);
  });

  it("starts receiving when a handler is registered after connecting", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const seen: string[] = [];
    await client.connect();

    client.onNotification("tools_list_changed", () => void seen.push("tools"));
    await until(() => listeners() === 1);
    server.notifyToolsListChanged();

    await until(() => seen.length === 1);
  });

  it("ends its subscription when closed", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    client.onNotification("tools_list_changed", () => {});
    await client.connect();
    expect(listeners()).toBe(1);

    await client.close();

    await until(() => listeners() === 0);
  });
});
```

In `typescript/tests/v2-client.test.ts`, in "says which operations v2 does not carry yet", replace `client.subscribeResource("res://a");` with:

```typescript
      client.notifyRootsListChanged();
```

- [ ] **Step 2: Run it to verify it fails**

```powershell
cd typescript; npx vitest run tests/v2-listen-client.test.ts
```

Expected: the `legacy` cases pass; the `modern` cases fail.

- [ ] **Step 3: The transport's `listen`**

In `typescript/src/v2/client-transport.ts`, add `type NotificationFilter` to the generated import and append to the class:

```typescript
  private async _dispatch(kind: string, payload: string): Promise<void> {
    try {
      await this._notifications.dispatch(kind, payload);
    } catch (err) {
      console.error(`[rapidmcp] notification handler for '${kind}' failed:`, err);
    }
  }

  /**
   * Hold a Listen stream open, handing its notifications to the registered
   * handlers. Resolves once the server acknowledges the subscription; delivery
   * continues until *signal* aborts.
   */
  listen(filter: NotificationFilter, signal: AbortSignal): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new McpError(ErrorCode.RequestTimeout, "Subscription was not acknowledged")),
        this._timeoutMs,
      );
      const options: CallOptions = { signal };
      if (this._opts.token) options.metadata = buildMetadata(this._opts);

      void (async () => {
        try {
          for await (const message of this._client.listen({ meta: this._meta(), notifications: filter }, options)) {
            const event = message.event;
            if (!event) continue;
            switch (event.$case) {
              case "acknowledged":
                clearTimeout(timer);
                resolve();
                break;
              case "toolsListChanged":
                await this._dispatch("tools_list_changed", "");
                break;
              case "promptsListChanged":
                await this._dispatch("prompts_list_changed", "");
                break;
              case "resourcesListChanged":
                await this._dispatch("resources_list_changed", "");
                break;
              case "resourceUpdated":
                await this._dispatch("resource_updated", JSON.stringify({ uri: event.resourceUpdated.uri }));
                break;
            }
          }
          resolve(); // ended without an acknowledgement: nothing more will come
        } catch (err) {
          if (!signal.aborted) console.warn("[rapidmcp] subscription stream ended:", err);
          resolve();
        } finally {
          clearTimeout(timer);
        }
      })();
    });
  }
```

- [ ] **Step 4: The client keeps one subscription matching what it registered**

In `typescript/src/client.ts`:

Fields, next to `_v2`:

```typescript
  private _subscribedUris: string[] = [];
  private _listenAbort: AbortController | null = null;
```

Below `_v1Only` (and change that method's message to `` `${operation} is not part of the v2 protocol; use mode: "legacy"` ``):

```typescript
  /** (Re)open the v2 subscription so it matches the handlers and uris registered now. */
  private async _refreshListen(): Promise<void> {
    if (!this._v2) return;
    this._listenAbort?.abort();
    this._listenAbort = null;
    const filter = {
      toolsListChanged: this._notifications.has("tools_list_changed"),
      promptsListChanged: this._notifications.has("prompts_list_changed"),
      resourcesListChanged: this._notifications.has("resources_list_changed"),
      resourceSubscriptions: [...this._subscribedUris],
    };
    const wantsAnything =
      filter.toolsListChanged ||
      filter.promptsListChanged ||
      filter.resourcesListChanged ||
      filter.resourceSubscriptions.length > 0;
    if (!wantsAnything) return;
    const controller = new AbortController();
    this._listenAbort = controller;
    await this._v2.listen(filter, controller.signal);
  }
```

In `_doConnect`, in the v2 branch, after `this._connected = true;` and before `return;`:

```typescript
        await this._refreshListen();
```

Replace `subscribeResource` with:

```typescript
  /** Ask for updates to one resource. Resolves once the server has the subscription. */
  subscribeResource(uri: string): Promise<void> {
    if (this._v2) {
      if (!this._subscribedUris.includes(uri)) this._subscribedUris.push(uri);
      return this._refreshListen();
    }
    this._sendQueue.enqueue({
      requestId: 0n,
      message: {
        $case: "subscribeRes" as const,
        subscribeRes: { uri },
      },
    });
    return Promise.resolve();
  }
```

Replace `onNotification` with:

```typescript
  onNotification(
    type: string,
    handler: (payload: string) => void | Promise<void>,
  ): void {
    this._notifications.register(type, handler);
    // Already connected over v2: widen the subscription in the background.
    if (this._v2) {
      this._refreshListen().catch((err) => console.warn("[rapidmcp] could not update subscription:", err));
    }
  }
```

In `close()`, at the top of the `if (this._v2) {` branch:

```typescript
      this._listenAbort?.abort();
      this._listenAbort = null;
```

- [ ] **Step 5: Run, changelog, commit**

```powershell
cd typescript; npx tsc -p tsconfig.build.json --noEmit; npx vitest run tests/v2-listen-client.test.ts tests/v2-client.test.ts; npx vitest run
```

Expected: type check clean; 8 new tests pass; the full suite is green.

In `CHANGELOG.md`, in both "Protocol v2 (experimental)" bullets, replace the final sentence `Not on v2 yet: subscriptions` with:

```markdown
List-changed and resource-updated notifications arrive through one opt-in `Listen` stream, opened for exactly the handlers and URIs the client registered
```

and add under TypeScript `### Changed`:

```markdown
- `Client.subscribeResource()` returns a promise (it resolves once the server has the subscription); it used to return nothing
```

```powershell
git -c safe.directory=D:/Trabajo/mcp-grpc add typescript/src typescript/tests CHANGELOG.md
git -c safe.directory=D:/Trabajo/mcp-grpc commit -m "feat(ts): v2 clients receive notifications through listen"
```
