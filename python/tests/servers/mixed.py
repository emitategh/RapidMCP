"""Test server: one slow tool and one fast tool (head-of-line blocking test)."""

import asyncio
import sys

from mcp_grpc import FasterMCP

port = int(sys.argv[1]) if len(sys.argv) > 1 else 50051

server = FasterMCP(name="docker-mixed", version="0.1")


@server.tool(description="Slow")
async def slow(x: str) -> str:
    await asyncio.sleep(0.5)
    return x


@server.tool(description="Fast")
async def fast(x: str) -> str:
    return x


async def _main() -> None:
    grpc_server = await server._start_grpc(port)
    await grpc_server.wait_for_termination()


asyncio.run(_main())
