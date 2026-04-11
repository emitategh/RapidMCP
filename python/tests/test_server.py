import json

import pytest

from mcp_grpc.server import FasterMCP


def test_register_tool():
    server = FasterMCP(name="test", version="0.1")

    @server.tool(description="Echo text back")
    async def echo(text: str) -> str:
        return text

    tools = server.list_registered_tools()
    assert len(tools) == 1
    assert tools[0].name == "echo"
    assert tools[0].description == "Echo text back"
    assert '"text"' in tools[0].input_schema


def test_register_resource():
    server = FasterMCP(name="test", version="0.1")

    @server.resource(uri="res://config", description="App config")
    async def config() -> str:
        return '{"env": "prod"}'

    resources = server.list_registered_resources()
    assert len(resources) == 1
    assert resources[0].uri == "res://config"


def test_register_prompt():
    server = FasterMCP(name="test", version="0.1")

    @server.prompt(description="Greet the user")
    async def greet(name: str) -> str:
        return f"Hello, {name}!"

    prompts = server.list_registered_prompts()
    assert len(prompts) == 1
    assert prompts[0].name == "greet"


def test_register_resource_template():
    server = FasterMCP(name="test", version="0.1")

    @server.resource_template(
        uri_template="file:///{path}",
        description="Read a file",
    )
    async def read_file(path: str) -> str:
        return f"contents of {path}"

    templates = server.list_registered_resource_templates()
    assert len(templates) == 1
    assert templates[0].uri_template == "file:///{path}"
    assert templates[0].description == "Read a file"


@pytest.mark.asyncio
async def test_call_tool_handler():
    server = FasterMCP(name="test", version="0.1")

    @server.tool(description="Echo text back")
    async def echo(text: str) -> str:
        return text

    result = await server.handle_call_tool("echo", '{"text": "hello"}')
    assert result.content[0].text == "hello"
    assert not result.is_error


@pytest.mark.asyncio
async def test_call_unknown_tool():
    server = FasterMCP(name="test", version="0.1")

    from mcp_grpc.errors import McpError

    with pytest.raises(McpError, match="not found"):
        await server.handle_call_tool("nonexistent", "{}")


def test_tool_context_excluded_from_schema():
    """Context parameter should not appear in input_schema."""
    from mcp_grpc.server import Context

    server = FasterMCP(name="test", version="0.1")

    @server.tool(description="Summarize with LLM")
    async def summarize(text: str, ctx: Context) -> str:
        return text

    tools = server.list_registered_tools()
    assert len(tools) == 1
    schema = json.loads(tools[0].input_schema)
    assert "text" in schema["properties"]
    assert "ctx" not in schema["properties"]
    assert tools[0].needs_context is True


def test_tool_without_context_has_no_needs_context():
    server = FasterMCP(name="test", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    tools = server.list_registered_tools()
    assert tools[0].needs_context is False


@pytest.mark.asyncio
async def test_tool_context_injection():
    """Tool handler receives a Context when type-hinted."""
    import asyncio
    from mcp_grpc.server import Context
    from mcp_grpc._generated import mcp_pb2
    from mcp_grpc.session import PendingRequests

    server = FasterMCP(name="test", version="0.1")
    received_ctx = []

    @server.tool(description="Check ctx")
    async def check_ctx(text: str, ctx: Context) -> str:
        received_ctx.append(ctx)
        return text

    result = await server.handle_call_tool(
        "check_ctx", '{"text": "hello"}',
        context=Context(
            client_capabilities=mcp_pb2.ClientCapabilities(),
            pending=PendingRequests(),
            write_queue=asyncio.Queue(),
        ),
    )
    assert result.content[0].text == "hello"
    assert len(received_ctx) == 1
    assert isinstance(received_ctx[0], Context)


def test_tool_bare_decorator():
    """@mcp.tool without parens infers description from docstring."""
    mcp = FasterMCP(name="test", version="0.1")

    @mcp.tool
    async def echo(text: str) -> str:
        """Echo text back"""
        return text

    tools = mcp.list_registered_tools()
    assert len(tools) == 1
    assert tools[0].name == "echo"
    assert tools[0].description == "Echo text back"


def test_tool_empty_parens():
    """@mcp.tool() with empty parens infers description from docstring."""
    mcp = FasterMCP(name="test", version="0.1")

    @mcp.tool()
    async def echo(text: str) -> str:
        """Echo text back"""
        return text

    tools = mcp.list_registered_tools()
    assert len(tools) == 1
    assert tools[0].description == "Echo text back"


def test_tool_no_docstring_no_description():
    """@mcp.tool with no docstring and no description uses empty string."""
    mcp = FasterMCP(name="test", version="0.1")

    @mcp.tool
    async def echo(text: str) -> str:
        return text

    tools = mcp.list_registered_tools()
    assert tools[0].description == ""


def test_prompt_bare_decorator():
    """@mcp.prompt without parens infers description from docstring."""
    mcp = FasterMCP(name="test", version="0.1")

    @mcp.prompt
    async def greet(name: str) -> str:
        """Greet the user"""
        return f"Hello, {name}!"

    prompts = mcp.list_registered_prompts()
    assert len(prompts) == 1
    assert prompts[0].description == "Greet the user"


def test_prompt_empty_parens():
    """@mcp.prompt() with empty parens infers description from docstring."""
    mcp = FasterMCP(name="test", version="0.1")

    @mcp.prompt()
    async def greet(name: str) -> str:
        """Greet the user"""
        return f"Hello, {name}!"

    prompts = mcp.list_registered_prompts()
    assert len(prompts) == 1
    assert prompts[0].description == "Greet the user"


def test_resource_optional_description():
    """@mcp.resource(uri) without description infers from docstring."""
    mcp = FasterMCP(name="test", version="0.1")

    @mcp.resource("res://config")
    async def config() -> str:
        """App config"""
        return '{"env": "prod"}'

    resources = mcp.list_registered_resources()
    assert len(resources) == 1
    assert resources[0].description == "App config"


def test_resource_template_optional_description():
    """@mcp.resource_template(uri) without description infers from docstring."""
    mcp = FasterMCP(name="test", version="0.1")

    @mcp.resource_template("file:///{path}")
    async def read_file(path: str) -> str:
        """Read a file"""
        return f"contents of {path}"

    templates = mcp.list_registered_resource_templates()
    assert len(templates) == 1
    assert templates[0].description == "Read a file"


def test_tool_explicit_description_overrides_docstring():
    """Explicit description= kwarg wins over docstring."""
    mcp = FasterMCP(name="test", version="0.1")

    @mcp.tool(description="Override")
    async def echo(text: str) -> str:
        """Docstring that should be ignored"""
        return text

    tools = mcp.list_registered_tools()
    assert tools[0].description == "Override"


def test_prompt_explicit_description_overrides_docstring():
    """Explicit description= kwarg wins over docstring."""
    mcp = FasterMCP(name="test", version="0.1")

    @mcp.prompt(description="Override")
    async def greet(name: str) -> str:
        """Docstring that should be ignored"""
        return f"Hello, {name}!"

    prompts = mcp.list_registered_prompts()
    assert prompts[0].description == "Override"
