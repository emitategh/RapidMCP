import pytest

from mcp_grpc.client import ListResult
from mcp_grpc.server import FasterMCP
from mcp_grpc.testing import InProcessChannel


@pytest.fixture
def echo_server():
    server = FasterMCP(name="test-server", version="0.1")

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
async def test_list_tools_twice(echo_server):
    """list_tools can be called twice without error."""
    async with InProcessChannel(echo_server) as client:
        result1 = await client.list_tools()
        result2 = await client.list_tools()
        assert len(result1.items) == len(result2.items) == 1
        assert result1.items[0].name == result2.items[0].name == "echo"


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
    server = FasterMCP(name="test-server", version="0.1")

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
    server = FasterMCP(name="test-server", version="0.1")

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
async def test_cancel_no_crash():
    """Cancellation is acknowledged without error."""
    server = FasterMCP(name="test-server", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with InProcessChannel(server) as client:
        await client.cancel(target_request_id=999)


@pytest.mark.asyncio
async def test_call_tool_after_concurrent_refactor(echo_server):
    """Normal tool calls still work after concurrent dispatch refactor."""
    async with InProcessChannel(echo_server) as client:
        result = await client.call_tool("echo", {"text": "concurrent test"})
        assert result.content[0].text == "concurrent test"
        assert not result.is_error


@pytest.mark.asyncio
async def test_complete():
    server = FasterMCP(name="test-server", version="0.1")

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


# ---------------------------------------------------------------------------
# Issue 4: close() before connect() should not raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_before_connect_no_error():
    """Calling close() on a freshly-constructed client should not raise."""
    from mcp_grpc import Client

    client = Client("localhost:1")
    await client.close()  # should be a no-op, no exception


# ---------------------------------------------------------------------------
# Issue 6: __aenter__ connect failure resets ref_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aenter_connect_failure_resets_ref_count():
    """If connect() fails, _ref_count must be reset to 0 so the client is reusable."""
    from mcp_grpc import Client

    client = Client("localhost:1")  # no server running here
    try:
        async with client:
            pass
    except BaseException:
        # CancelledError (BaseException) is raised when the gRPC stream fails
        pass
    assert client._ref_count == 0
