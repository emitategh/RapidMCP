"""fastermcp echo server for benchmarking. Runs on port 50052."""
from fastermcp import FasterMCP

server = FasterMCP(name="grpc-benchmark", version="1.0.0")


@server.tool(description="Echo the input back")
async def echo(text: str) -> str:
    return text


if __name__ == "__main__":
    server.run(port=50052)
