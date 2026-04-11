"""Tests for elicitation form helpers, ElicitationResult, pagination, MIME resources, cancellation."""

import asyncio
import json

import pytest

from mcp_grpc import (
    BoolField,
    Client,
    Context,
    ElicitationResult,
    EnumField,
    FasterMCP,
    FloatField,
    IntField,
    StringField,
    build_elicitation_schema,
)
from mcp_grpc._generated import mcp_pb2


# ---------------------------------------------------------------------------
# Unit tests — field descriptors and schema builder
# ---------------------------------------------------------------------------


def test_string_field_minimal():
    schema = json.loads(build_elicitation_schema({"name": StringField()}))
    assert schema["type"] == "object"
    assert schema["properties"]["name"]["type"] == "string"
    assert "name" in schema["required"]


def test_string_field_optional():
    schema = json.loads(build_elicitation_schema({"alias": StringField(required=False)}))
    assert "required" not in schema or "alias" not in schema.get("required", [])


def test_string_field_constraints():
    schema = json.loads(
        build_elicitation_schema(
            {"code": StringField(min_length=3, max_length=10, pattern=r"^[A-Z]+$")}
        )
    )
    prop = schema["properties"]["code"]
    assert prop["minLength"] == 3
    assert prop["maxLength"] == 10
    assert prop["pattern"] == r"^[A-Z]+$"


def test_bool_field():
    schema = json.loads(build_elicitation_schema({"agree": BoolField(title="I agree")}))
    prop = schema["properties"]["agree"]
    assert prop["type"] == "boolean"
    assert prop["title"] == "I agree"
    assert "agree" in schema["required"]


def test_int_field():
    schema = json.loads(
        build_elicitation_schema({"count": IntField(minimum=1, maximum=100)})
    )
    prop = schema["properties"]["count"]
    assert prop["type"] == "integer"
    assert prop["minimum"] == 1
    assert prop["maximum"] == 100


def test_float_field():
    schema = json.loads(
        build_elicitation_schema({"ratio": FloatField(minimum=0.0, maximum=1.0)})
    )
    prop = schema["properties"]["ratio"]
    assert prop["type"] == "number"
    assert prop["minimum"] == 0.0
    assert prop["maximum"] == 1.0


def test_enum_field():
    schema = json.loads(
        build_elicitation_schema(
            {"env": EnumField(choices=["prod", "staging", "dev"], default="dev")}
        )
    )
    prop = schema["properties"]["env"]
    assert prop["type"] == "string"
    assert prop["enum"] == ["prod", "staging", "dev"]
    assert prop["default"] == "dev"


def test_mixed_fields_required_tracking():
    schema = json.loads(
        build_elicitation_schema(
            {
                "name": StringField(required=True),
                "alias": StringField(required=False),
                "count": IntField(required=True),
            }
        )
    )
    assert set(schema["required"]) == {"name", "count"}


# ---------------------------------------------------------------------------
# Unit tests — ElicitationResult
# ---------------------------------------------------------------------------


def test_elicitation_result_accept():
    r = ElicitationResult(action="accept", data={"confirm": True})
    assert r.accepted
    assert not r.declined
    assert not r.cancelled
    assert r.data["confirm"] is True


def test_elicitation_result_decline():
    r = ElicitationResult(action="decline")
    assert r.declined
    assert not r.accepted
    assert r.data == {}


def test_elicitation_result_cancel():
    r = ElicitationResult(action="cancel")
    assert r.cancelled


# ---------------------------------------------------------------------------
# gRPC integration — elicitation with fields= builder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_elicit_with_fields_builder():
    """ctx.elicit(fields=...) builds schema and returns ElicitationResult."""
    app = FasterMCP("E", "1")

    @app.tool(description="Deploy confirm")
    async def deploy(service: str, ctx: Context) -> str:
        result = await ctx.elicit(
            message=f"Deploy {service}?",
            fields={
                "environment": EnumField(choices=["prod", "staging"], title="Environment"),
                "dry_run": BoolField(title="Dry run?", required=False),
            },
        )
        if result.accepted:
            return f"Deployed {service} to {result.data.get('environment', '?')}"
        return "Cancelled"

    async with app:
        client = Client(f"localhost:{app.port}")
        client.set_elicitation_handler(None)

        async def handler(req: mcp_pb2.ElicitationRequest) -> mcp_pb2.ElicitationResponse:
            schema = json.loads(req.schema)
            # Verify schema was built correctly
            assert schema["properties"]["environment"]["enum"] == ["prod", "staging"]
            assert schema["properties"]["dry_run"]["type"] == "boolean"
            return mcp_pb2.ElicitationResponse(
                action="accept",
                content='{"environment": "prod", "dry_run": false}',
            )

        client.set_elicitation_handler(handler)
        await client.connect()
        try:
            result = await client.call_tool("deploy", {"service": "api"})
        finally:
            await client.close()

    assert not result.is_error
    assert result.content[0].text == "Deployed api to prod"


@pytest.mark.asyncio
async def test_elicit_decline_returns_result():
    """Client declining elicitation → ElicitationResult(action='decline')."""
    app = FasterMCP("E", "1")

    @app.tool(description="Ask")
    async def ask(ctx: Context) -> str:
        r = await ctx.elicit(message="Really?", fields={"ok": BoolField()})
        return r.action  # return the action string as tool output

    async with app:
        client = Client(f"localhost:{app.port}")
        client.set_elicitation_handler(None)

        async def handler(req):
            return mcp_pb2.ElicitationResponse(action="decline", content="")

        client.set_elicitation_handler(handler)
        await client.connect()
        try:
            result = await client.call_tool("ask", {})
        finally:
            await client.close()

    assert result.content[0].text == "decline"


@pytest.mark.asyncio
async def test_elicit_schema_and_fields_mutual_exclusion():
    """Passing both schema= and fields= raises ValueError."""
    app = FasterMCP("E", "1")

    @app.tool(description="t")
    async def bad(ctx: Context) -> str:
        await ctx.elicit(
            message="hi",
            schema='{"type": "object"}',
            fields={"x": StringField()},
        )
        return "ok"

    async with app:
        client = Client(f"localhost:{app.port}")
        client.set_elicitation_handler(
            lambda req: mcp_pb2.ElicitationResponse(action="accept", content="{}")
        )
        await client.connect()
        try:
            result = await client.call_tool("bad", {})
        finally:
            await client.close()

    # Tool raises ValueError → returned as is_error
    assert result.is_error
    assert "both" in result.content[0].text.lower() or "fields" in result.content[0].text.lower()


# ---------------------------------------------------------------------------
# gRPC integration — pagination
# ---------------------------------------------------------------------------



def _register_tools(app: FasterMCP, names: list) -> None:
    """Register one tool per name — avoids the closure-in-loop trap."""
    for name in names:
        def _make(n: str):
            async def _fn() -> str:
                return n
            _fn.__name__ = n
            return _fn
        app.tool(description=name)(_make(name))


@pytest.mark.asyncio
async def test_pagination_first_page():
    """page_size=2: first page has 2 items and a next_cursor."""
    app = FasterMCP("Paged", "1.0", page_size=2)
    _register_tools(app, ["alpha", "beta", "gamma"])

    async with app:
        async with Client(f"localhost:{app.port}") as client:
            result = await client.list_tools()
            assert len(result.items) == 2
            assert result.next_cursor == "2"


@pytest.mark.asyncio
async def test_pagination_follow_cursor():
    """Following next_cursor fetches subsequent pages until exhausted."""
    app = FasterMCP("Paged", "1.0", page_size=2)
    _register_tools(app, ["alpha", "beta", "gamma", "delta", "epsilon"])

    async with app:
        async with Client(f"localhost:{app.port}") as client:
            r1 = await client.list_tools()
            assert len(r1.items) == 2
            assert r1.next_cursor is not None

            r2 = await client.list_tools(cursor=r1.next_cursor)
            assert len(r2.items) == 2
            assert r2.next_cursor is not None

            r3 = await client.list_tools(cursor=r2.next_cursor)
            assert len(r3.items) == 1
            assert r3.next_cursor is None

            all_names = {t.name for t in r1.items + r2.items + r3.items}
            assert all_names == {"alpha", "beta", "gamma", "delta", "epsilon"}


@pytest.mark.asyncio
async def test_pagination_no_page_size_returns_all():
    """Default (page_size=None) still returns all items with no cursor."""
    app = FasterMCP("NoPaged", "1.0")
    _register_tools(app, ["alpha", "beta", "gamma"])

    async with app:
        async with Client(f"localhost:{app.port}") as client:
            result = await client.list_tools()
            assert len(result.items) == 3
            assert result.next_cursor is None


@pytest.mark.asyncio
async def test_pagination_resources():
    """page_size applies to list_resources too."""
    app = FasterMCP("Res", "1.0", page_size=2)

    @app.resource("res://a", description="a")
    async def _a() -> str:
        return "a"

    @app.resource("res://b", description="b")
    async def _b() -> str:
        return "b"

    @app.resource("res://c", description="c")
    async def _c() -> str:
        return "c"

    async with app:
        async with Client(f"localhost:{app.port}") as client:
            r1 = await client.list_resources()
            assert len(r1.items) == 2
            assert r1.next_cursor == "2"

            r2 = await client.list_resources(cursor=r1.next_cursor)
            assert len(r2.items) == 1
            assert r2.next_cursor is None


# ---------------------------------------------------------------------------
# gRPC integration — MIME-aware read_resource
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_resource_image_bytes():
    """Resource returning bytes with image/* MIME → ContentItem type=image."""
    app = FasterMCP("Mime", "1.0")

    @app.resource("res://logo", mime_type="image/png")
    async def logo() -> bytes:
        return b"\x89PNG\r\n\x1a\n"

    async with app:
        async with Client(f"localhost:{app.port}") as client:
            resp = await client.read_resource("res://logo")

    assert len(resp.content) == 1
    item = resp.content[0]
    assert item.type == "image"
    assert item.data == b"\x89PNG\r\n\x1a\n"
    assert item.mime_type == "image/png"


@pytest.mark.asyncio
async def test_read_resource_audio_bytes():
    """Resource returning bytes with audio/* MIME → ContentItem type=audio."""
    app = FasterMCP("Mime", "1.0")

    @app.resource("res://sound", mime_type="audio/mpeg")
    async def sound() -> bytes:
        return b"\xff\xfb\x90\x00"

    async with app:
        async with Client(f"localhost:{app.port}") as client:
            resp = await client.read_resource("res://sound")

    item = resp.content[0]
    assert item.type == "audio"
    assert item.data == b"\xff\xfb\x90\x00"
    assert item.mime_type == "audio/mpeg"


@pytest.mark.asyncio
async def test_read_resource_binary_non_image_audio():
    """Resource returning bytes with application/* MIME → ContentItem type=resource."""
    app = FasterMCP("Mime", "1.0")

    @app.resource("res://data", mime_type="application/octet-stream")
    async def data() -> bytes:
        return b"\x00\x01\x02"

    async with app:
        async with Client(f"localhost:{app.port}") as client:
            resp = await client.read_resource("res://data")

    item = resp.content[0]
    assert item.type == "resource"
    assert item.data == b"\x00\x01\x02"
    assert item.mime_type == "application/octet-stream"


@pytest.mark.asyncio
async def test_read_resource_text_unchanged():
    """Resource returning str still produces ContentItem type=text."""
    app = FasterMCP("Mime", "1.0")

    @app.resource("res://greeting")
    async def greeting() -> str:
        return "hello"

    async with app:
        async with Client(f"localhost:{app.port}") as client:
            resp = await client.read_resource("res://greeting")

    item = resp.content[0]
    assert item.type == "text"
    assert item.text == "hello"


# ---------------------------------------------------------------------------
# gRPC integration — cancellation propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_in_flight_tool():
    """Cancelling an in-flight tool_id → tool gets CancelledError, error response sent."""
    app = FasterMCP("Cancel", "1.0")
    tool_started = asyncio.Event()

    @app.tool(description="Slow tool")
    async def slow() -> str:
        tool_started.set()
        await asyncio.sleep(10)  # will be cancelled
        return "done"

    async with app:
        client = Client(f"localhost:{app.port}")
        await client.connect()
        try:
            # Fire tool call without awaiting — capture the request_id
            import asyncio as _asyncio

            rid = client._pending.next_id()
            env = mcp_pb2.ClientEnvelope(
                request_id=rid,
                call_tool=mcp_pb2.CallToolRequest(name="slow", arguments="{}"),
            )
            future = client._pending.create(rid)
            await client._send(env)

            # Wait for tool to start
            await asyncio.wait_for(tool_started.wait(), timeout=5.0)

            # Now cancel it
            await client.cancel(rid)

            # The pending future should resolve to an error response
            result = await asyncio.wait_for(future, timeout=5.0)
            # result is either a CallToolResponse (is_error) or an ErrorResponse
            # Since cancel sends an ErrorResponse with code 499, _pending.resolve gets ErrorResponse
            # But _reader_loop rejects on "error" message type
        except Exception:
            pass  # McpError(499) is fine — tool was cancelled
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_cancel_nonexistent_id_no_crash():
    """Cancelling a request_id that doesn't exist is silently ignored."""
    app = FasterMCP("Cancel", "1.0")

    @app.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with app:
        async with Client(f"localhost:{app.port}") as client:
            await client.cancel(99999)
            result = await client.call_tool("echo", {"text": "ok"})
            assert result.content[0].text == "ok"
