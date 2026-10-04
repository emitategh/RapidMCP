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
        assert [(p.name, [a.name for a in p.arguments]) for p in prompts.items] == [
            ("greet", ["who"])
        ]


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
