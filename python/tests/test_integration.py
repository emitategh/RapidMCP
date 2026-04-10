import pytest

from mcp_grpc import McpClient, McpServer


@pytest.fixture
async def echo_server():
    server = McpServer(name="echo-server", version="0.1")

    @server.tool(description="Echo text back")
    async def echo(text: str) -> str:
        return text

    @server.tool(description="Reverse text")
    async def reverse(text: str) -> str:
        return text[::-1]

    @server.resource(uri="res://greeting", description="A greeting")
    async def greeting() -> str:
        return "Hello, world!"

    async with server:
        yield server


@pytest.mark.asyncio
async def test_grpc_list_tools(echo_server):
    async with McpClient(f"localhost:{echo_server.port}") as client:
        result = await client.list_tools()
        names = {t.name for t in result.items}
        assert names == {"echo", "reverse"}


@pytest.mark.asyncio
async def test_grpc_call_tool(echo_server):
    async with McpClient(f"localhost:{echo_server.port}") as client:
        result = await client.call_tool("echo", {"text": "hello grpc"})
        assert result.content[0].text == "hello grpc"
        assert not result.is_error


@pytest.mark.asyncio
async def test_grpc_call_reverse(echo_server):
    async with McpClient(f"localhost:{echo_server.port}") as client:
        result = await client.call_tool("reverse", {"text": "abc"})
        assert result.content[0].text == "cba"


@pytest.mark.asyncio
async def test_grpc_ping(echo_server):
    async with McpClient(f"localhost:{echo_server.port}") as client:
        await client.ping()


@pytest.mark.asyncio
async def test_grpc_list_resources(echo_server):
    async with McpClient(f"localhost:{echo_server.port}") as client:
        result = await client.list_resources()
        assert len(result.items) == 1
        assert result.items[0].uri == "res://greeting"


@pytest.mark.asyncio
async def test_grpc_read_resource(echo_server):
    async with McpClient(f"localhost:{echo_server.port}") as client:
        result = await client.read_resource("res://greeting")
        assert result.content[0].text == "Hello, world!"


@pytest.mark.asyncio
async def test_grpc_error_unknown_tool(echo_server):
    async with McpClient(f"localhost:{echo_server.port}") as client:
        from mcp_grpc.errors import McpError

        with pytest.raises(McpError, match="not found"):
            await client.call_tool("nope", {})
