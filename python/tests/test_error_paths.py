"""Server/client failure paths must answer promptly instead of hanging or dropping the session."""

import asyncio

import pytest

from rapidmcp import Client, Middleware, RapidMCP
from rapidmcp._generated import mcp_pb2
from rapidmcp.errors import McpError

# Every call below must be answered well inside this; the client's own
# request timeout is 30s, so hitting this limit means the server never replied.
_PROMPT = 3.0


class _ExplodingMiddleware(Middleware):
    async def on_tool_call(self, tool_ctx, call_next):
        raise ValueError("middleware exploded")


@pytest.fixture
async def server():
    srv = RapidMCP(name="error-paths", version="0.1")

    @srv.tool(description="Echo text back")
    async def echo(text: str) -> str:
        return text

    @srv.prompt()
    async def greet(name: str) -> str:
        return f"hi {name}"

    @srv.completion("greet")
    async def complete_greet(argument_name: str, value: str) -> list[str]:
        raise RuntimeError("completion exploded")

    async with srv:
        yield srv


async def _call_tool_raw(client: Client, name: str, arguments: str):
    env = mcp_pb2.ClientEnvelope(call_tool=mcp_pb2.CallToolRequest(name=name, arguments=arguments))
    return await asyncio.wait_for(client._request(env), timeout=_PROMPT)


async def test_middleware_exception_is_reported_to_client(server):
    server.add_middleware(_ExplodingMiddleware())
    async with Client(f"localhost:{server.port}") as client:
        with pytest.raises(McpError) as exc:
            await asyncio.wait_for(client.call_tool("echo", {"text": "x"}), timeout=_PROMPT)
        assert exc.value.code == 500


async def test_invalid_json_arguments_are_rejected(server):
    async with Client(f"localhost:{server.port}") as client:
        with pytest.raises(McpError) as exc:
            await _call_tool_raw(client, "echo", "{not json")
        assert exc.value.code == 400


async def test_non_object_arguments_are_rejected(server):
    async with Client(f"localhost:{server.port}") as client:
        with pytest.raises(McpError) as exc:
            await _call_tool_raw(client, "echo", "[1, 2]")
        assert exc.value.code == 400


async def test_completion_handler_exception_is_reported_to_client(server):
    async with Client(f"localhost:{server.port}") as client:
        with pytest.raises(McpError) as exc:
            await asyncio.wait_for(
                client.complete("ref/prompt", "greet", "name", "a"), timeout=_PROMPT
            )
        assert exc.value.code == 500


async def test_session_survives_completion_handler_exception(server):
    async with Client(f"localhost:{server.port}") as client:
        with pytest.raises(Exception):  # noqa: B017 - any failure; survival is the point
            await asyncio.wait_for(
                client.complete("ref/prompt", "greet", "name", "a"), timeout=_PROMPT
            )
        result = await asyncio.wait_for(client.call_tool("echo", {"text": "still here"}), _PROMPT)
        assert result.content[0].text == "still here"


async def test_in_flight_tool_is_cancelled_when_client_disconnects():
    srv = RapidMCP(name="disconnect", version="0.1")
    started = asyncio.Event()
    finished: list[str] = []

    @srv.tool()
    async def slow() -> str:
        started.set()
        await asyncio.sleep(0.5)
        finished.append("finished")
        return "done"

    async with srv:
        client = Client(f"localhost:{srv.port}")
        await client.connect()
        call = asyncio.create_task(client.call_tool("slow"))
        await asyncio.wait_for(started.wait(), timeout=_PROMPT)

        await client.close()
        call.cancel()
        await asyncio.sleep(0.8)  # longer than the tool would need to finish

        assert finished == []


async def test_prompt_text_is_returned_as_a_user_message(server):
    async with Client(f"localhost:{server.port}") as client:
        result = await asyncio.wait_for(client.get_prompt("greet", {"name": "Ada"}), _PROMPT)

    assert [(m.role, m.content.text) for m in result.messages] == [("user", "hi Ada")]


async def test_request_after_stream_died_fails_fast():
    srv = RapidMCP(name="short-lived", version="0.1")
    client = None
    async with srv:
        client = Client(f"localhost:{srv.port}")
        await client.connect()
    try:
        # Server is gone; give the reader loop a moment to observe the broken stream.
        for _ in range(50):
            if not client.is_connected:
                break
            await asyncio.sleep(0.05)
        assert not client.is_connected

        with pytest.raises(McpError) as exc:
            await asyncio.wait_for(client.ping(), timeout=_PROMPT)
        assert exc.value.code == 503
    finally:
        await client.close()
