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
