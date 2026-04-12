"""Test server: simple echo tool with a small sleep."""

import asyncio
import sys

from rapidmcp import RapidMCP

port = int(sys.argv[1]) if len(sys.argv) > 1 else 50051

server = RapidMCP(name="docker-echo", version="0.1")


@server.tool(description="Echo")
async def echo(text: str) -> str:
    await asyncio.sleep(0.005)
    return text


async def _main() -> None:
    grpc_server = await server._start_grpc(port)
    await grpc_server.wait_for_termination()


asyncio.run(_main())
