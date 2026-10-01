"""Request timeouts are configurable and a timed-out tool call is cancelled on the server."""

import asyncio

import pytest

from rapidmcp import Client, Context, RapidMCP
from rapidmcp._generated import mcp_pb2
from rapidmcp.errors import McpError


@pytest.fixture
async def server():
    srv = RapidMCP(name="timeouts", version="0.1")
    srv.finished = []

    @srv.tool()
    async def slow() -> str:
        await asyncio.sleep(0.6)
        srv.finished.append("slow")
        return "done"

    @srv.tool()
    async def ask(ctx: Context) -> str:
        try:
            answer = await ctx.elicit("Continue?", timeout=0.2)
        except asyncio.TimeoutError:
            return "elicitation timed out"
        return answer.action

    async with srv:
        yield srv


async def _elapsed(coro) -> tuple[float, BaseException | None]:
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        await coro
    except BaseException as exc:
        return loop.time() - start, exc
    return loop.time() - start, None


async def test_client_request_timeout_is_configurable(server):
    async with Client(f"localhost:{server.port}", request_timeout=0.2) as client:
        elapsed, exc = await _elapsed(client.call_tool("slow"))

    assert isinstance(exc, McpError)
    assert exc.code == 408
    assert elapsed < 0.5


async def test_call_tool_timeout_can_be_shortened_per_call(server):
    async with Client(f"localhost:{server.port}") as client:
        elapsed, exc = await _elapsed(client.call_tool("slow", timeout=0.2))

    assert isinstance(exc, McpError)
    assert exc.code == 408
    assert elapsed < 0.5


async def test_call_tool_timeout_can_be_extended_per_call(server):
    async with Client(f"localhost:{server.port}", request_timeout=0.2) as client:
        result = await client.call_tool("slow", timeout=3)

    assert result.content[0].text == "done"


async def test_timed_out_tool_call_is_cancelled_on_the_server(server):
    async with Client(f"localhost:{server.port}") as client:
        with pytest.raises(McpError):
            await client.call_tool("slow", timeout=0.2)
        await asyncio.sleep(0.7)  # the tool would have finished by now
        await client.ping()

    assert server.finished == []


async def test_elicitation_timeout_is_configurable(server):
    client = Client(f"localhost:{server.port}")

    async def slow_human(request):
        await asyncio.sleep(1.0)
        return mcp_pb2.ElicitationResponse(action="accept", content="{}")

    client.set_elicitation_handler(slow_human)
    async with client:
        result = await client.call_tool("ask", timeout=3)

    assert result.content[0].text == "elicitation timed out"


async def test_cancelling_the_awaiting_task_cancels_the_tool_on_the_server(server):
    async with Client(f"localhost:{server.port}") as client:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client.call_tool("slow"), timeout=0.2)
        await asyncio.sleep(0.7)  # the tool would have finished by now
        await client.ping()

    assert server.finished == []
