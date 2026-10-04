"""Client(mode="modern"): tool calls, resource reads and prompts over v2."""

import asyncio
import json

import pytest

from rapidmcp import Client, Context, RapidMCP
from rapidmcp.errors import McpError, ToolError


@pytest.fixture
async def server():
    srv = RapidMCP(name="calls", version="1.0")
    srv.finished = []

    @srv.tool()
    async def echo(text: str) -> str:
        return text

    @srv.tool()
    async def add(a: int, b: int) -> dict:
        return {"sum": a + b}

    @srv.tool()
    async def friendly() -> str:
        raise ToolError("order id must start with ORD-")

    @srv.tool()
    async def chatty(ctx: Context) -> str:
        await ctx.info("working", extra={"n": 1})
        await ctx.report_progress(1, 2)
        return "done"

    @srv.tool()
    async def slow() -> str:
        await asyncio.sleep(0.6)
        srv.finished.append("slow")
        return "done"

    @srv.resource("res://text")
    async def text() -> str:
        return "hello"

    @srv.resource("res://logo", mime_type="image/png")
    async def logo() -> bytes:
        return b"\x89PNG"

    @srv.prompt()
    async def greet(name: str) -> str:
        return f"hi {name}"

    async with srv:
        yield srv


def _modern(server, **kwargs) -> Client:
    return Client(f"localhost:{server.port}", mode="modern", **kwargs)


async def test_call_tool(server):
    async with _modern(server) as client:
        result = await client.call_tool("echo", {"text": "hi"})

    assert (result.is_error, result.content[0].text, result.structured_content) == (
        False,
        "hi",
        None,
    )


async def test_structured_content_is_parsed(server):
    async with _modern(server) as client:
        result = await client.call_tool("add", {"a": 2, "b": 3})

    assert result.structured_content == {"sum": 5}
    assert json.loads(result.content[0].text) == {"sum": 5}


async def test_tool_error_comes_back_as_an_error_result(server):
    async with _modern(server) as client:
        result = await client.call_tool("friendly")

    assert (result.is_error, result.content[0].text) == (True, "order id must start with ORD-")


async def test_unknown_tool_raises_invalid_params(server):
    async with _modern(server) as client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("nope")

    assert exc.value.code == -32602


async def test_progress_and_log_handlers_receive_the_calls_events(server):
    client = _modern(server)
    progress: list[dict] = []
    logs: list[dict] = []
    client.on_notification("progress", lambda payload: progress.append(json.loads(payload)))
    client.on_notification("log", lambda payload: logs.append(json.loads(payload)))

    async with client:
        result = await client.call_tool("chatty")

    assert result.content[0].text == "done"
    assert [(p["progress"], p["total"]) for p in progress] == [(1, 2)]
    assert progress[0]["token"]
    assert logs == [{"level": "info", "message": "working", "extra": {"n": 1}}]


async def test_a_throwing_notification_handler_does_not_break_the_call(server):
    client = _modern(server)

    def explode(payload):
        raise RuntimeError("handler bug")

    client.on_notification("progress", explode)
    async with client:
        result = await client.call_tool("chatty")

    assert result.content[0].text == "done"


async def test_timeout_raises_408_and_cancels_the_tool(server):
    async with _modern(server) as client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("slow", timeout=0.2)
        await asyncio.sleep(0.8)

    assert exc.value.code == 408
    assert server.finished == []


async def test_cancelling_the_awaiting_task_cancels_the_tool(server):
    async with _modern(server) as client:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client.call_tool("slow"), timeout=0.2)
        await asyncio.sleep(0.8)

    assert server.finished == []


async def test_read_resource(server):
    async with _modern(server) as client:
        text = await client.read_resource("res://text")
        logo = await client.read_resource("res://logo")
        with pytest.raises(McpError) as exc:
            await client.read_resource("res://nope")

    assert (text.content[0].type, text.content[0].text) == ("text", "hello")
    assert (logo.content[0].type, logo.content[0].data) == ("image", b"\x89PNG")
    assert exc.value.code == -32602


async def test_get_prompt(server):
    async with _modern(server) as client:
        result = await client.get_prompt("greet", {"name": "Ada"})
        with pytest.raises(McpError) as exc:
            await client.get_prompt("greet")

    assert [(m.role, m.content.text) for m in result.messages] == [("user", "hi Ada")]
    assert exc.value.code == -32602
