"""Tests for enhanced sampling: model_preferences, tools, tool_choice, multi-content."""

import pytest

from mcp_grpc import Client, Context, FasterMCP
from mcp_grpc._generated import mcp_pb2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_response(*texts: str) -> mcp_pb2.SamplingResponse:
    return mcp_pb2.SamplingResponse(
        role="assistant",
        content=[mcp_pb2.ContentItem(type="text", text=t) for t in texts],
        model="test-model",
        stop_reason="end_turn",
    )


# ---------------------------------------------------------------------------
# Unit tests — ctx.sample() message normalisation (no gRPC)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sample_dict_message_string_content():
    """ctx.sample() wraps a plain-string dict content into a ContentItem list."""
    app = FasterMCP("t", "1")

    @app.tool(description="t")
    async def do_sample(ctx: Context) -> str:
        await ctx.sample(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=10,
        )
        return "ok"

    # Patch write_queue to capture the envelope without real gRPC
    import asyncio

    queue: asyncio.Queue = asyncio.Queue()
    pending_mock = type(
        "P",
        (),
        {
            "next_id": lambda s: 1,
            "create": lambda s, rid: asyncio.get_event_loop().create_future(),
        },
    )()
    caps = mcp_pb2.ClientCapabilities(sampling=True)
    ctx = Context(client_capabilities=caps, pending=pending_mock, write_queue=queue)

    # Don't await sample (it would block on future); just put enough to inspect queue
    import asyncio

    loop = asyncio.get_event_loop()
    task = loop.create_task(
        ctx.sample(messages=[{"role": "user", "content": "hello"}], max_tokens=10)
    )
    await asyncio.sleep(0)  # let task run until it hits wait_for
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    env: mcp_pb2.ServerEnvelope = queue.get_nowait()
    msg = env.sampling.messages[0]
    assert msg.role == "user"
    assert len(msg.content) == 1
    assert msg.content[0].type == "text"
    assert msg.content[0].text == "hello"


@pytest.mark.asyncio
async def test_sample_dict_message_list_content():
    """ctx.sample() handles list-of-strings as content."""
    import asyncio

    queue: asyncio.Queue = asyncio.Queue()
    pending_mock = type(
        "P",
        (),
        {
            "next_id": lambda s: 1,
            "create": lambda s, rid: asyncio.get_event_loop().create_future(),
        },
    )()
    caps = mcp_pb2.ClientCapabilities(sampling=True)
    ctx = Context(client_capabilities=caps, pending=pending_mock, write_queue=queue)

    task = asyncio.get_event_loop().create_task(
        ctx.sample(
            messages=[{"role": "user", "content": ["first", "second"]}],
            max_tokens=10,
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    env: mcp_pb2.ServerEnvelope = queue.get_nowait()
    msg = env.sampling.messages[0]
    assert len(msg.content) == 2
    assert msg.content[0].text == "first"
    assert msg.content[1].text == "second"


# ---------------------------------------------------------------------------
# gRPC integration — model_preferences round-trip
# ---------------------------------------------------------------------------


@pytest.fixture
async def sampling_server():
    """Server whose tool calls ctx.sample() and captures the request."""
    app = FasterMCP("SamplingTest", "1.0")
    captured_requests: list[mcp_pb2.SamplingRequest] = []

    @app.tool(description="Calls sample with model preferences")
    async def call_with_prefs(ctx: Context) -> str:
        result = await ctx.sample(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
            model_preferences={
                "hints": ["claude-3-5-sonnet"],
                "cost_priority": 0.2,
                "speed_priority": 0.8,
                "intelligence_priority": 0.5,
            },
        )
        return result.content[0].text

    @app.tool(description="Calls sample with tools and tool_choice")
    async def call_with_tools(ctx: Context) -> str:
        result = await ctx.sample(
            messages=[{"role": "user", "content": "use a tool"}],
            max_tokens=50,
            tools=[
                {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "input_schema": '{"type": "object", "properties": {"location": {"type": "string"}}}',
                }
            ],
            tool_choice="auto",
        )
        return result.content[0].text

    @app.tool(description="Calls sample with proto SamplingMessage")
    async def call_with_proto_msg(ctx: Context) -> str:
        result = await ctx.sample(
            messages=[
                mcp_pb2.SamplingMessage(
                    role="user",
                    content=[mcp_pb2.ContentItem(type="text", text="proto message")],
                )
            ],
            max_tokens=10,
        )
        return result.content[0].text

    app._captured = captured_requests  # expose for inspection

    async with app:
        yield app


async def _connect_with_sampling(port: int, handler) -> Client:
    """Create a client, set the sampling handler, then connect (order matters)."""
    client = Client(f"localhost:{port}")
    client.set_sampling_handler(handler)
    await client.connect()
    return client


@pytest.mark.asyncio
async def test_model_preferences_round_trip(sampling_server):
    """model_preferences fields arrive at the sampling handler intact."""

    async def handler(req: mcp_pb2.SamplingRequest) -> mcp_pb2.SamplingResponse:
        prefs = req.model_preferences
        assert len(prefs.hints) == 1
        assert prefs.hints[0].name == "claude-3-5-sonnet"
        assert abs(prefs.cost_priority - 0.2) < 1e-5
        assert abs(prefs.speed_priority - 0.8) < 1e-5
        assert abs(prefs.intelligence_priority - 0.5) < 1e-5
        return _text_response("pref-ok")

    client = await _connect_with_sampling(sampling_server.port, handler)
    try:
        result = await client.call_tool("call_with_prefs", {})
    finally:
        await client.close()

    assert not result.is_error
    assert result.content[0].text == "pref-ok"


@pytest.mark.asyncio
async def test_tools_and_tool_choice_round_trip(sampling_server):
    """tools and tool_choice fields arrive at the sampling handler intact."""

    async def handler(req: mcp_pb2.SamplingRequest) -> mcp_pb2.SamplingResponse:
        assert req.tool_choice == "auto"
        assert len(req.tools) == 1
        assert req.tools[0].name == "get_weather"
        assert "location" in req.tools[0].input_schema
        return _text_response("tools-ok")

    client = await _connect_with_sampling(sampling_server.port, handler)
    try:
        result = await client.call_tool("call_with_tools", {})
    finally:
        await client.close()

    assert not result.is_error
    assert result.content[0].text == "tools-ok"


@pytest.mark.asyncio
async def test_proto_sampling_message_passthrough(sampling_server):
    """Pre-built SamplingMessage protos pass through unchanged."""

    async def handler(req: mcp_pb2.SamplingRequest) -> mcp_pb2.SamplingResponse:
        assert req.messages[0].content[0].text == "proto message"
        return _text_response("proto-ok")

    client = await _connect_with_sampling(sampling_server.port, handler)
    try:
        result = await client.call_tool("call_with_proto_msg", {})
    finally:
        await client.close()

    assert not result.is_error
    assert result.content[0].text == "proto-ok"


@pytest.mark.asyncio
async def test_multi_content_sampling_response(sampling_server):
    """SamplingResponse with multiple ContentItems — tool reads first item."""

    async def handler(req: mcp_pb2.SamplingRequest) -> mcp_pb2.SamplingResponse:
        return mcp_pb2.SamplingResponse(
            role="assistant",
            content=[
                mcp_pb2.ContentItem(type="text", text="first"),
                mcp_pb2.ContentItem(type="text", text="second"),
            ],
            model="test-model",
            stop_reason="end_turn",
        )

    client = await _connect_with_sampling(sampling_server.port, handler)
    try:
        result = await client.call_tool("call_with_prefs", {})
    finally:
        await client.close()

    assert not result.is_error
    # Tool reads content[0].text; "first" is what the tool returns
    assert result.content[0].text == "first"


@pytest.mark.asyncio
async def test_tool_use_content_in_sampling_response():
    """SamplingResponse with tool_use ContentItem — fields preserved."""
    app = FasterMCP("ToolUseSampling", "1.0")

    @app.tool(description="Inspect tool_use in sampling response")
    async def inspect_tool_use(ctx: Context) -> str:
        result = await ctx.sample(
            messages=[{"role": "user", "content": "call a tool"}],
            max_tokens=50,
            tools=[{"name": "do_thing", "description": "Does a thing", "input_schema": "{}"}],
            tool_choice="required",
        )
        item = result.content[0]
        return f"{item.type}:{item.tool_name}:{item.tool_use_id}"

    async with app:

        async def handler(req: mcp_pb2.SamplingRequest) -> mcp_pb2.SamplingResponse:
            assert req.tool_choice == "required"
            return mcp_pb2.SamplingResponse(
                role="assistant",
                content=[
                    mcp_pb2.ContentItem(
                        type="tool_use",
                        tool_use_id="tu_123",
                        tool_name="do_thing",
                        tool_input='{"x": 1}',
                    )
                ],
                model="test-model",
                stop_reason="tool_use",
            )

        client = await _connect_with_sampling(app.port, handler)
        try:
            result = await client.call_tool("inspect_tool_use", {})
        finally:
            await client.close()

    assert not result.is_error
    assert result.content[0].text == "tool_use:do_thing:tu_123"
