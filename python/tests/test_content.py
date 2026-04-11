"""Tests for tool annotations, output_schema, and rich content types."""

from __future__ import annotations

import json

import pytest

from mcp_grpc import Audio, Client, FasterMCP, Image, ToolAnnotations


# ---------------------------------------------------------------------------
# Unit tests — content type helpers (no gRPC)
# ---------------------------------------------------------------------------


def test_image_defaults():
    img = Image(data=b"\x89PNG")
    assert img.mime_type == "image/png"
    assert img.data == b"\x89PNG"


def test_image_custom_mime():
    img = Image(data=b"...", mime_type="image/jpeg")
    assert img.mime_type == "image/jpeg"


def test_audio_defaults():
    aud = Audio(data=b"\xff\xfb")
    assert aud.mime_type == "audio/mpeg"


def test_audio_custom_mime():
    aud = Audio(data=b"...", mime_type="audio/wav")
    assert aud.mime_type == "audio/wav"


def test_tool_annotations_defaults():
    ann = ToolAnnotations()
    assert ann.title == ""
    assert ann.read_only_hint is False
    assert ann.destructive_hint is False
    assert ann.idempotent_hint is False
    assert ann.open_world_hint is False


# ---------------------------------------------------------------------------
# Unit tests — @tool() decorator stores annotations and output_schema
# ---------------------------------------------------------------------------


def test_tool_decorator_stores_read_only_hint():
    app = FasterMCP("test", "1.0")

    @app.tool(description="Safe read", read_only=True)
    async def safe_read() -> str:
        return "ok"

    t = app._tools["safe_read"]
    assert t.annotations is not None
    assert t.annotations.read_only_hint is True
    assert t.annotations.destructive_hint is False


def test_tool_decorator_stores_all_hints():
    app = FasterMCP("test", "1.0")

    @app.tool(
        description="Complex",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
        title="My Tool",
    )
    async def complex_tool() -> str:
        return "ok"

    ann = app._tools["complex_tool"].annotations
    assert ann is not None
    assert ann.title == "My Tool"
    assert ann.destructive_hint is True
    assert ann.idempotent_hint is True
    assert ann.open_world_hint is True


def test_tool_decorator_no_annotations_when_defaults():
    """When no hint flags are set, annotations should be None."""
    app = FasterMCP("test", "1.0")

    @app.tool(description="Plain")
    async def plain() -> str:
        return "ok"

    assert app._tools["plain"].annotations is None


def test_tool_decorator_stores_output_schema():
    app = FasterMCP("test", "1.0")
    schema = {"type": "object", "properties": {"result": {"type": "string"}}}

    @app.tool(description="Structured", output_schema=schema)
    async def structured() -> str:
        return "{}"

    stored = app._tools["structured"].output_schema
    assert stored == json.dumps(schema)


def test_tool_decorator_empty_output_schema_by_default():
    app = FasterMCP("test", "1.0")

    @app.tool(description="No schema")
    async def no_schema() -> str:
        return "ok"

    assert app._tools["no_schema"].output_schema == ""


# ---------------------------------------------------------------------------
# gRPC integration — list_tools returns annotations and output_schema
# ---------------------------------------------------------------------------


@pytest.fixture
async def annotated_server():
    app = FasterMCP("Annotated", "1.0")

    @app.tool(
        description="Read-only idempotent tool",
        read_only=True,
        idempotent=True,
        title="Safe Reader",
        output_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
    )
    async def safe_read() -> str:
        return '{"value": 42}'

    @app.tool(description="Plain tool — no annotations")
    async def plain() -> str:
        return "plain"

    async with app:
        yield app


@pytest.mark.asyncio
async def test_list_tools_includes_annotations(annotated_server):
    async with Client(f"localhost:{annotated_server.port}") as client:
        result = await client.list_tools()
        by_name = {t.name: t for t in result.items}

        t = by_name["safe_read"]
        assert t.annotations.read_only_hint is True
        assert t.annotations.idempotent_hint is True
        assert t.annotations.title == "Safe Reader"
        assert t.annotations.destructive_hint is False

        plain = by_name["plain"]
        # annotations message present but all fields at default (falsy)
        assert plain.annotations.read_only_hint is False


@pytest.mark.asyncio
async def test_list_tools_includes_output_schema(annotated_server):
    async with Client(f"localhost:{annotated_server.port}") as client:
        result = await client.list_tools()
        by_name = {t.name: t for t in result.items}

        t = by_name["safe_read"]
        parsed = json.loads(t.output_schema)
        assert parsed["type"] == "object"
        assert "value" in parsed["properties"]

        assert by_name["plain"].output_schema == ""


# ---------------------------------------------------------------------------
# gRPC integration — rich content types in tool responses
# ---------------------------------------------------------------------------


@pytest.fixture
async def rich_server():
    app = FasterMCP("Rich", "1.0")

    @app.tool(description="Returns an image")
    async def get_image() -> Image:
        return Image(data=b"\x89PNG\r\n\x1a\n", mime_type="image/png")

    @app.tool(description="Returns audio")
    async def get_audio() -> Audio:
        return Audio(data=b"\xff\xfb\x90\x00", mime_type="audio/mpeg")

    @app.tool(description="Returns a dict (structured output)")
    async def get_dict() -> dict:
        return {"answer": 42}

    @app.tool(description="Returns a mixed list")
    async def get_list() -> list:
        return ["hello", Image(data=b"img", mime_type="image/png")]

    async with app:
        yield app


@pytest.mark.asyncio
async def test_image_content_over_grpc(rich_server):
    async with Client(f"localhost:{rich_server.port}") as client:
        result = await client.call_tool("get_image", {})

    assert not result.is_error
    assert len(result.content) == 1
    item = result.content[0]
    assert item.type == "image"
    assert item.data == b"\x89PNG\r\n\x1a\n"
    assert item.mime_type == "image/png"


@pytest.mark.asyncio
async def test_audio_content_over_grpc(rich_server):
    async with Client(f"localhost:{rich_server.port}") as client:
        result = await client.call_tool("get_audio", {})

    assert not result.is_error
    item = result.content[0]
    assert item.type == "audio"
    assert item.data == b"\xff\xfb\x90\x00"
    assert item.mime_type == "audio/mpeg"


@pytest.mark.asyncio
async def test_dict_content_over_grpc(rich_server):
    async with Client(f"localhost:{rich_server.port}") as client:
        result = await client.call_tool("get_dict", {})

    assert not result.is_error
    item = result.content[0]
    assert item.type == "text"
    assert json.loads(item.text) == {"answer": 42}


@pytest.mark.asyncio
async def test_list_content_over_grpc(rich_server):
    async with Client(f"localhost:{rich_server.port}") as client:
        result = await client.call_tool("get_list", {})

    assert not result.is_error
    assert len(result.content) == 2
    assert result.content[0].type == "text"
    assert result.content[0].text == "hello"
    assert result.content[1].type == "image"
    assert result.content[1].data == b"img"
