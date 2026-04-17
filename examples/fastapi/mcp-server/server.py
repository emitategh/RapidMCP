"""MCP test server — exercises the full RapidMCP spec surface."""

import asyncio
import json
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

from rapidmcp import (
    BoolField,
    Context,
    LoggingMiddleware,
    RapidMCP,
    TimingMiddleware,
    ToolError,
)

server = RapidMCP(
    name="mcp-test-server",
    version="0.1.0",
    middleware=[TimingMiddleware(), LoggingMiddleware()],
)

# ---------------------------------------------------------------------------
# Simple tools
# ---------------------------------------------------------------------------


@server.tool(description="Add two numbers", read_only=True)
async def add(a: float, b: float) -> str:
    return str(a + b)


@server.tool(description="Echo text back unchanged", read_only=True)
async def echo(text: str) -> str:
    return text


# ---------------------------------------------------------------------------
# Elicitation — asks the user to confirm before proceeding
# ---------------------------------------------------------------------------


@server.tool(description="Perform an action after user confirmation")
async def confirm_action(action: str, ctx: Context) -> str:
    result = await ctx.elicit(
        message=f"Please confirm: {action}",
        fields={"confirm": BoolField(title="Confirm?", description=f"Do you want to: {action}")},
    )
    if result.accepted and result.data.get("confirm"):
        return f"Action confirmed and executed: {action}"
    return f"Action declined: {action}"


# ---------------------------------------------------------------------------
# Sampling — delegates summarization to the client LLM
# ---------------------------------------------------------------------------


@server.tool(description="Summarize text using the client LLM via sampling")
async def summarize_with_llm(text: str, ctx: Context) -> str:
    response = await ctx.sample(
        messages=[{"role": "user", "content": f"Summarize in one sentence: {text}"}],
        max_tokens=200,
    )
    if response.content:
        return response.content[0].text
    return "No summary returned"


# ---------------------------------------------------------------------------
# Progress — simulates a slow multi-step task
# ---------------------------------------------------------------------------


@server.tool(description="Simulate a long-running task with progress reporting")
async def long_running_task(steps: int, ctx: Context) -> str:
    steps = min(max(steps, 1), 10)
    for i in range(1, steps + 1):
        await ctx.report_progress(i, steps)
        await asyncio.sleep(0.2)
    return f"Completed {steps} steps"


# ---------------------------------------------------------------------------
# Logging — emits all four log levels to the client
# ---------------------------------------------------------------------------


@server.tool(description="Demo of server-to-client logging at all levels")
async def log_demo(ctx: Context) -> str:
    await ctx.debug("debug: low-level detail")
    await ctx.info("info: normal operation")
    await ctx.warning("warning: something to watch")
    await ctx.error("error: something went wrong")
    return "All four log levels emitted"


# ---------------------------------------------------------------------------
# ToolError — always fails
# ---------------------------------------------------------------------------


@server.tool(description="A tool that always fails with a ToolError")
async def fail_tool() -> str:
    raise ToolError("This tool always fails on purpose")


# ---------------------------------------------------------------------------
# Static resources
# ---------------------------------------------------------------------------


@server.resource(uri="res://server-info", description="Server name, version, and current timestamp")
async def server_info_resource() -> str:
    return json.dumps(
        {
            "name": "mcp-test-server",
            "version": "0.1.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@server.resource(uri="res://config", description="Static server configuration")
async def config_resource() -> str:
    return json.dumps({"debug": True, "max_retries": 3, "timeout_seconds": 30})


# ---------------------------------------------------------------------------
# Resource template
# ---------------------------------------------------------------------------


@server.resource_template("res://items/{item_id}", description="Fetch an item by its ID")
async def get_item(item_id: str) -> str:
    return json.dumps({"id": item_id, "name": f"Item {item_id}", "status": "active"})


# ---------------------------------------------------------------------------
# Prompt + completion
# ---------------------------------------------------------------------------


@server.prompt(description="Generate a greeting in a given style")
async def greet(name: str, style: str = "formal") -> str:
    greetings = {
        "formal": f"Dear {name}, I hope this message finds you well.",
        "casual": f"Hey {name}! What's up?",
        "pirate": f"Ahoy, {name}! Shiver me timbers!",
        "shakespearean": f"Hark! {name}, thou art most welcome.",
    }
    return greetings.get(style, f"Hello, {name}!")


@server.completion("greet")
async def complete_greet(argument_name: str, value: str) -> list[str]:
    if argument_name == "style":
        options = ["formal", "casual", "pirate", "shakespearean"]
        return [o for o in options if o.startswith(value)]
    return []


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server.run(port=50051)
