import pytest

from mcp_grpc.client import ListResult
from mcp_grpc.server import McpServer
from mcp_grpc.testing import InProcessChannel


@pytest.fixture
def echo_server():
    server = McpServer(name="test-server", version="0.1")

    @server.tool(description="Echo text back")
    async def echo(text: str) -> str:
        return text

    @server.resource(uri="res://greeting", description="A greeting")
    async def greeting() -> str:
        return "Hello, world!"

    return server


@pytest.mark.asyncio
async def test_list_tools(echo_server):
    async with InProcessChannel(echo_server) as client:
        result = await client.list_tools()
        assert len(result.items) == 1
        assert result.items[0].name == "echo"


@pytest.mark.asyncio
async def test_call_tool(echo_server):
    async with InProcessChannel(echo_server) as client:
        result = await client.call_tool("echo", {"text": "hello"})
        assert result.content[0].text == "hello"
        assert not result.is_error


@pytest.mark.asyncio
async def test_call_unknown_tool(echo_server):
    async with InProcessChannel(echo_server) as client:
        from mcp_grpc.errors import McpError

        with pytest.raises(McpError, match="not found"):
            await client.call_tool("nonexistent", {})


@pytest.mark.asyncio
async def test_list_resources(echo_server):
    async with InProcessChannel(echo_server) as client:
        result = await client.list_resources()
        assert len(result.items) == 1
        assert result.items[0].uri == "res://greeting"


@pytest.mark.asyncio
async def test_read_resource(echo_server):
    async with InProcessChannel(echo_server) as client:
        result = await client.read_resource("res://greeting")
        assert result.content[0].text == "Hello, world!"


@pytest.mark.asyncio
async def test_ping(echo_server):
    async with InProcessChannel(echo_server) as client:
        await client.ping()


@pytest.mark.asyncio
async def test_list_resource_templates():
    server = McpServer(name="test-server", version="0.1")

    @server.resource_template(
        uri_template="file:///{path}",
        description="Read a file",
    )
    async def read_file(path: str) -> str:
        return f"contents of {path}"

    async with InProcessChannel(server) as client:
        result = await client.list_resource_templates()
        assert len(result.items) == 1
        assert result.items[0].uri_template == "file:///{path}"


@pytest.mark.asyncio
async def test_initialize(echo_server):
    async with InProcessChannel(echo_server) as client:
        info = client.server_info
        assert info.server_name == "test-server"
        assert info.capabilities.tools is True


@pytest.mark.asyncio
async def test_list_tools_returns_list_result():
    server = McpServer(name="test-server", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with InProcessChannel(server) as client:
        result = await client.list_tools()
        assert isinstance(result, ListResult)
        assert len(result.items) == 1
        assert result.items[0].name == "echo"
        assert result.next_cursor is None or result.next_cursor == ""


@pytest.mark.asyncio
async def test_complete():
    server = McpServer(name="test-server", version="0.1")

    @server.prompt(description="Greet someone")
    async def greet(language: str) -> str:
        return f"Hello in {language}"

    @server.completion("greet")
    async def complete_language(argument_name: str, value: str) -> list[str]:
        options = ["english", "spanish", "french", "german"]
        return [o for o in options if o.startswith(value)]

    async with InProcessChannel(server) as client:
        result = await client.complete("ref/prompt", "greet", "language", "sp")
        assert "spanish" in result.values
        assert "english" not in result.values
