"""Concurrency stress tests — deadlock detection and correctness under load.

Every test is wrapped in asyncio.timeout() so a deadlock surfaces as a
TimeoutError rather than a hung test runner.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from rapidmcp import Client, RapidMCP
from rapidmcp._generated import mcp_pb2
from rapidmcp.context import Context

# ---------------------------------------------------------------------------
# 1. Many concurrent calls from one client (same session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_tool_calls_same_client():
    """50 concurrent calls on a single connection must all complete correctly."""
    server = RapidMCP(name="stress", version="0.1")

    @server.tool(description="Slow echo")
    async def slow_echo(text: str) -> str:
        await asyncio.sleep(0.05)
        return text

    async with asyncio.timeout(15):
        async with server:
            async with Client(f"localhost:{server.port}") as client:
                results = await asyncio.gather(
                    *[client.call_tool("slow_echo", {"text": str(i)}) for i in range(50)]
                )

    assert len(results) == 50
    texts = {r.content[0].text for r in results}
    assert texts == {str(i) for i in range(50)}


# ---------------------------------------------------------------------------
# 2. Many concurrent clients (independent sessions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_clients():
    """30 clients connecting and calling a tool simultaneously."""
    server = RapidMCP(name="stress", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        await asyncio.sleep(0.01)
        return text

    async def one_client(i: int) -> str:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("echo", {"text": str(i)})
            return result.content[0].text

    async with asyncio.timeout(15):
        async with server:
            results = await asyncio.gather(*[one_client(i) for i in range(30)])

    assert sorted(results, key=int) == [str(i) for i in range(30)]


# ---------------------------------------------------------------------------
# 3. Concurrent tool calls with sampling (most likely deadlock vector)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_sampling_no_deadlock():
    """10 concurrent tool calls each doing a sampling round-trip.

    This exercises the path where multiple tool tasks simultaneously write
    SamplingRequest into write_queue and wait on PendingRequests futures.
    The race loop must pump all of them without deadlocking.
    """
    server = RapidMCP(name="stress", version="0.1")

    @server.tool(description="Sample")
    async def do_sample(n: str, ctx: Context) -> str:
        resp = await ctx.sample(
            messages=[{"role": "user", "content": f"msg {n}"}],
            max_tokens=5,
        )
        return resp.content[0].text

    async def sampling_handler(req: mcp_pb2.SamplingRequest) -> mcp_pb2.SamplingResponse:
        await asyncio.sleep(0.01)  # simulate LLM latency
        text = req.messages[0].content[0].text
        return mcp_pb2.SamplingResponse(
            role="assistant",
            content=[mcp_pb2.ContentItem(type="text", text=f"reply:{text}")],
        )

    async with asyncio.timeout(15):
        async with server:
            client = Client(f"localhost:{server.port}")
            client.set_sampling_handler(sampling_handler)
            await client.connect()
            try:
                results = await asyncio.gather(
                    *[client.call_tool("do_sample", {"n": str(i)}) for i in range(10)]
                )
            finally:
                await client.close()

    assert len(results) == 10
    for r in results:
        assert not r.is_error
        assert r.content[0].text.startswith("reply:msg ")


# ---------------------------------------------------------------------------
# 4. Concurrent sampling across multiple clients
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_sampling_multiple_clients():
    """5 clients, each running 3 concurrent sampling calls = 15 in-flight samples."""
    server = RapidMCP(name="stress", version="0.1")

    @server.tool(description="Sample")
    async def do_sample(label: str, ctx: Context) -> str:
        resp = await ctx.sample(
            messages=[{"role": "user", "content": label}],
            max_tokens=5,
        )
        return resp.content[0].text

    async def sampling_handler(req: mcp_pb2.SamplingRequest) -> mcp_pb2.SamplingResponse:
        text = req.messages[0].content[0].text
        return mcp_pb2.SamplingResponse(
            role="assistant",
            content=[mcp_pb2.ContentItem(type="text", text=f"ok:{text}")],
        )

    async def client_session(client_id: int) -> list[str]:
        client = Client(f"localhost:{server.port}")
        client.set_sampling_handler(sampling_handler)
        await client.connect()
        try:
            results = await asyncio.gather(
                *[client.call_tool("do_sample", {"label": f"c{client_id}-t{j}"}) for j in range(3)]
            )
            return [r.content[0].text for r in results]
        finally:
            await client.close()

    async with asyncio.timeout(15):
        async with server:
            all_results = await asyncio.gather(*[client_session(i) for i in range(5)])

    flat = [text for session in all_results for text in session]
    assert len(flat) == 15
    assert all(t.startswith("ok:c") for t in flat)


# ---------------------------------------------------------------------------
# 5. Server broadcast notifications during active tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_during_concurrent_tool_calls():
    """Notifications must be delivered even while 20 tool calls are in flight."""
    server = RapidMCP(name="stress", version="0.1")

    @server.tool(description="Slow tool")
    async def slow(x: str) -> str:
        await asyncio.sleep(0.1)
        return x

    async with asyncio.timeout(10):
        async with server:
            async with Client(f"localhost:{server.port}") as client:
                received: list[int] = []
                client.on_notification("tools_list_changed", lambda _: received.append(1))

                # Fire off 20 concurrent slow tool calls
                tool_tasks = [
                    asyncio.create_task(client.call_tool("slow", {"x": str(i)})) for i in range(20)
                ]

                # Broadcast twice while the tools are running
                await asyncio.sleep(0.02)
                server.notify_tools_list_changed()
                server.notify_tools_list_changed()

                results = await asyncio.gather(*tool_tasks)
                await asyncio.sleep(0.05)  # let notifications drain

    assert len(results) == 20
    assert len(received) == 2


# ---------------------------------------------------------------------------
# 6. Fast tools not head-of-line blocked by slow tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_tools_not_blocked_by_slow():
    """A fast tool must complete well before a slow tool finishes."""
    server = RapidMCP(name="stress", version="0.1")

    @server.tool(description="Slow")
    async def slow(x: str) -> str:
        await asyncio.sleep(0.5)
        return x

    @server.tool(description="Fast")
    async def fast(x: str) -> str:
        return x

    async with asyncio.timeout(10):
        async with server:
            async with Client(f"localhost:{server.port}") as client:
                slow_task = asyncio.create_task(client.call_tool("slow", {"x": "slow"}))
                await asyncio.sleep(0.05)  # let slow start

                t0 = time.perf_counter()
                fast_result = await client.call_tool("fast", {"x": "fast"})
                elapsed = time.perf_counter() - t0

                slow_result = await slow_task

    assert fast_result.content[0].text == "fast"
    assert slow_result.content[0].text == "slow"
    assert elapsed < 0.2, f"fast tool took {elapsed:.3f}s — likely blocked by slow tool"


# ---------------------------------------------------------------------------
# 7. Cancellations under concurrent load
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_under_load():
    """Cancel some in-flight calls while others complete normally."""
    server = RapidMCP(name="stress", version="0.1")

    @server.tool(description="Slow")
    async def slow(x: str) -> str:
        await asyncio.sleep(0.3)
        return x

    @server.tool(description="Fast")
    async def fast(x: str) -> str:
        return x

    async with asyncio.timeout(10):
        async with server:
            async with Client(f"localhost:{server.port}") as client:
                # Start 10 slow tasks
                slow_tasks = [
                    asyncio.create_task(client.call_tool("slow", {"x": str(i)})) for i in range(10)
                ]

                await asyncio.sleep(0.05)

                # Cancel half of them at the asyncio level
                for task in slow_tasks[:5]:
                    task.cancel()

                # Fast tool must still work after cancellations
                fast_result = await client.call_tool("fast", {"x": "ok"})

                # Remaining slow tasks should complete
                remaining = await asyncio.gather(*slow_tasks[5:], return_exceptions=True)

    assert fast_result.content[0].text == "ok"
    # Cancelled tasks raised CancelledError; remaining succeeded
    successes = [r for r in remaining if not isinstance(r, BaseException)]
    assert len(successes) == 5


# ---------------------------------------------------------------------------
# 8. High-volume sequential calls — memory and state leak check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_volume_sequential_no_leak():
    """200 sequential calls on one connection — PendingRequests must not grow."""
    server = RapidMCP(name="stress", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with asyncio.timeout(30):
        async with server:
            async with Client(f"localhost:{server.port}") as client:
                for i in range(200):
                    result = await client.call_tool("echo", {"text": str(i)})
                    assert result.content[0].text == str(i)

                # After all calls complete, no pending requests should remain
                assert len(client._pending._pending) == 0


# ---------------------------------------------------------------------------
# 9. Mixed concurrent + sequential on multiple clients simultaneously
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_concurrent_and_sequential():
    """Combine concurrent and sequential access patterns across 5 clients."""
    server = RapidMCP(name="stress", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        await asyncio.sleep(0.005)
        return text

    async def sequential_client(client_id: int) -> list[str]:
        async with Client(f"localhost:{server.port}") as client:
            texts = []
            for j in range(10):
                r = await client.call_tool("echo", {"text": f"{client_id}-{j}"})
                texts.append(r.content[0].text)
            return texts

    async def burst_client(client_id: int) -> list[str]:
        async with Client(f"localhost:{server.port}") as client:
            results = await asyncio.gather(
                *[client.call_tool("echo", {"text": f"{client_id}-{j}"}) for j in range(10)]
            )
            return [r.content[0].text for r in results]

    async with asyncio.timeout(15):
        async with server:
            all_results = await asyncio.gather(
                sequential_client(0),
                sequential_client(1),
                burst_client(2),
                burst_client(3),
                sequential_client(4),
            )

    for client_id, texts in enumerate(all_results):
        assert len(texts) == 10
        expected = {f"{client_id}-{j}" for j in range(10)}
        assert set(texts) == expected
