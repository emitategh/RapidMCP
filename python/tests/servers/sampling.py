"""Test server: tool that does a sampling round-trip on every call."""

import asyncio
import sys

from fastermcp import FasterMCP
from fastermcp.context import Context

port = int(sys.argv[1]) if len(sys.argv) > 1 else 50051

server = FasterMCP(name="docker-sampling", version="0.1")


@server.tool(description="Sample")
async def do_sample(n: str, ctx: Context) -> str:
    resp = await ctx.sample(
        messages=[{"role": "user", "content": f"msg {n}"}],
        max_tokens=5,
    )
    return resp.content[0].text


async def _main() -> None:
    grpc_server = await server._start_grpc(port)
    await grpc_server.wait_for_termination()


asyncio.run(_main())
