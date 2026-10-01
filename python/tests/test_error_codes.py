"""Error codes follow MCP: JSON-RPC codes for protocol failures, -32021 for a missing client capability."""

import asyncio

import pytest

from rapidmcp import Client, Context, RapidMCP
from rapidmcp._generated import mcp_pb2
from rapidmcp._servicer import _McpServicer
from rapidmcp.errors import McpError


@pytest.fixture
async def server():
    srv = RapidMCP(name="codes", version="0.1")

    @srv.tool()
    async def ask(ctx: Context) -> str:
        answer = await ctx.elicit("Continue?")
        return answer.action

    @srv.tool()
    async def stubborn() -> str:
        """Keeps going for a while even after being cancelled."""
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            await asyncio.sleep(0.5)
            raise
        return "done"

    async with srv:
        yield srv


async def _code(coro) -> int:
    with pytest.raises(McpError) as exc:
        await asyncio.wait_for(coro, timeout=3)
    return exc.value.code


async def test_unknown_tool_is_invalid_params(server):
    async with Client(f"localhost:{server.port}") as client:
        assert await _code(client.call_tool("nope")) == -32602


async def test_unknown_resource_is_invalid_params(server):
    async with Client(f"localhost:{server.port}") as client:
        assert await _code(client.read_resource("res://nope")) == -32602


async def test_unknown_prompt_is_invalid_params(server):
    async with Client(f"localhost:{server.port}") as client:
        assert await _code(client.get_prompt("nope")) == -32602


async def test_missing_client_capability_has_its_own_code(server):
    # This client registers no elicitation handler, so it never declared the capability.
    async with Client(f"localhost:{server.port}") as client:
        assert await _code(client.call_tool("ask")) == -32021


async def test_cancel_ends_the_local_wait_without_waiting_for_the_server(server):
    async with Client(f"localhost:{server.port}") as client:  # initialize used request id 1
        call = asyncio.create_task(client.call_tool("stubborn"))  # request id 2
        await asyncio.sleep(0.1)
        loop = asyncio.get_running_loop()
        start = loop.time()

        await client.cancel(2)
        with pytest.raises(McpError) as exc:
            await call

        assert exc.value.code == 499
        assert loop.time() - start < 0.3


async def test_server_sends_no_response_for_a_cancelled_call():
    srv = RapidMCP(name="codes", version="0.1")

    @srv.tool()
    async def slow() -> str:
        await asyncio.sleep(1)
        return "done"

    async def messages():
        yield mcp_pb2.ClientEnvelope(
            request_id=7, call_tool=mcp_pb2.CallToolRequest(name="slow", arguments="{}")
        )
        await asyncio.sleep(0.05)  # let the tool start before cancelling it
        yield mcp_pb2.ClientEnvelope(cancel=mcp_pb2.CancelRequest(target_request_id=7))

    responses = [r async for r in _McpServicer(srv).Session(messages(), None)]

    assert [r.WhichOneof("message") for r in responses if r.request_id == 7] == []
