"""Plain ``def`` handlers must work, and must not block the event loop."""

import asyncio
import threading

from rapidmcp import Client, RapidMCP


async def test_sync_tool_returns_its_result():
    server = RapidMCP(name="sync", version="0.1")
    calls: list[int] = []

    @server.tool()
    def double(x: int) -> str:
        calls.append(x)
        return str(x * 2)

    result = await server.handle_call_tool("double", '{"x": 21}')

    assert not result.is_error
    assert result.content[0].text == "42"
    assert calls == [21]


async def test_sync_tool_runs_off_the_event_loop_thread():
    server = RapidMCP(name="sync", version="0.1")

    @server.tool()
    def where() -> str:
        return "loop" if threading.current_thread() is threading.main_thread() else "worker"

    result = await server.handle_call_tool("where", "{}")

    assert result.content[0].text == "worker"


async def test_sync_resource_template_and_prompt_over_grpc():
    server = RapidMCP(name="sync", version="0.1")

    @server.resource("res://greeting")
    def greeting() -> str:
        return "hello"

    @server.resource_template("res://items/{item_id}")
    def item(item_id: str) -> str:
        return f"item {item_id}"

    @server.prompt()
    def greet(name: str) -> str:
        return f"hi {name}"

    async with server, Client(f"localhost:{server.port}") as client:
        static = await asyncio.wait_for(client.read_resource("res://greeting"), 3)
        templated = await asyncio.wait_for(client.read_resource("res://items/7"), 3)
        prompt = await asyncio.wait_for(client.get_prompt("greet", {"name": "Ada"}), 3)

    assert static.content[0].text == "hello"
    assert templated.content[0].text == "item 7"
    assert prompt.messages[0].content.text == "hi Ada"
