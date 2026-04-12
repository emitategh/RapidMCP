import asyncio
import json

import pytest

from mcp_grpc import Client, FasterMCP
from mcp_grpc._generated import mcp_pb2
from mcp_grpc.context import Context


@pytest.fixture
async def echo_server():
    server = FasterMCP(name="echo-server", version="0.1")

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
    async with Client(f"localhost:{echo_server.port}") as client:
        result = await client.list_tools()
        names = {t.name for t in result.items}
        assert names == {"echo", "reverse"}


@pytest.mark.asyncio
async def test_grpc_call_tool(echo_server):
    async with Client(f"localhost:{echo_server.port}") as client:
        result = await client.call_tool("echo", {"text": "hello grpc"})
        assert result.content[0].text == "hello grpc"
        assert not result.is_error


@pytest.mark.asyncio
async def test_grpc_call_reverse(echo_server):
    async with Client(f"localhost:{echo_server.port}") as client:
        result = await client.call_tool("reverse", {"text": "abc"})
        assert result.content[0].text == "cba"


@pytest.mark.asyncio
async def test_grpc_ping(echo_server):
    async with Client(f"localhost:{echo_server.port}") as client:
        await client.ping()


@pytest.mark.asyncio
async def test_grpc_list_resources(echo_server):
    async with Client(f"localhost:{echo_server.port}") as client:
        result = await client.list_resources()
        assert len(result.items) == 1
        assert result.items[0].uri == "res://greeting"


@pytest.mark.asyncio
async def test_grpc_read_resource(echo_server):
    async with Client(f"localhost:{echo_server.port}") as client:
        result = await client.read_resource("res://greeting")
        assert result.content[0].text == "Hello, world!"


@pytest.mark.asyncio
async def test_grpc_list_tools_twice(echo_server):
    """Session stays alive between requests — list_tools works on the same stream."""
    async with Client(f"localhost:{echo_server.port}") as client:
        result1 = await client.list_tools()
        result2 = await client.list_tools()
        assert {t.name for t in result1.items} == {t.name for t in result2.items}
        assert {t.name for t in result1.items} == {"echo", "reverse"}


@pytest.mark.asyncio
async def test_grpc_error_unknown_tool(echo_server):
    async with Client(f"localhost:{echo_server.port}") as client:
        from mcp_grpc.errors import McpError

        with pytest.raises(McpError, match="not found"):
            await client.call_tool("nope", {})


@pytest.mark.asyncio
async def test_grpc_client_notification():
    server = FasterMCP(name="notif-server", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    received = []
    server.on_roots_list_changed(lambda payload: received.append("roots_changed"))

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            await client.notify_roots_list_changed()
            await asyncio.sleep(0.2)
            assert len(received) == 1
            assert received[0] == "roots_changed"


@pytest.mark.asyncio
async def test_grpc_server_notification():
    server = FasterMCP(name="notif-server", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    received = []

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            client.on_notification("tools_list_changed", lambda payload: received.append("got"))
            # Give the server's reader task time to process the initialized ack
            await asyncio.sleep(0.05)
            server.notify_tools_list_changed()
            await asyncio.sleep(0.2)
            assert len(received) == 1


@pytest.mark.asyncio
async def test_grpc_sampling_roundtrip():
    """Full round-trip: tool calls ctx.sample(), client handler responds."""
    server = FasterMCP(name="sampling-server", version="0.1")

    @server.tool(description="Summarize with LLM")
    async def summarize(text: str, ctx: Context) -> str:
        result = await ctx.sample(
            messages=[
                mcp_pb2.SamplingMessage(
                    role="user",
                    content=[mcp_pb2.ContentItem(type="text", text=f"Summarize: {text}")],
                )
            ],
            max_tokens=100,
        )
        return result.content[0].text

    async with server:
        client = Client(f"localhost:{server.port}")

        async def sampling_handler(request):
            prompt_text = request.messages[0].content[0].text
            return mcp_pb2.SamplingResponse(
                role="assistant",
                content=[mcp_pb2.ContentItem(type="text", text=f"Summary of: {prompt_text}")],
                model="test-model",
                stop_reason="end",
            )

        client.set_sampling_handler(sampling_handler)
        await client.connect()

        try:
            result = await client.call_tool("summarize", {"text": "hello world"})
            assert "Summary of: Summarize: hello world" in result.content[0].text
            assert not result.is_error
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_grpc_elicitation_roundtrip():
    """Full round-trip: tool calls ctx.elicit(), client handler responds."""
    server = FasterMCP(name="elicit-server", version="0.1")

    @server.tool(description="Confirm deploy")
    async def deploy(service: str, ctx: Context) -> str:
        response = await ctx.elicit(
            message=f"Deploy {service} to production?",
            schema='{"type": "object", "properties": {"confirm": {"type": "boolean"}}}',
        )
        if response.action == "accept":
            return f"Deployed {service}"
        return "Cancelled"

    async with server:
        client = Client(f"localhost:{server.port}")

        async def elicitation_handler(request):
            return mcp_pb2.ElicitationResponse(
                action="accept",
                content='{"confirm": true}',
            )

        client.set_elicitation_handler(elicitation_handler)
        await client.connect()

        try:
            result = await client.call_tool("deploy", {"service": "api"})
            assert result.content[0].text == "Deployed api"
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_grpc_sampling_without_capability():
    """Tool calling ctx.sample() without client capability gets an error response."""
    server = FasterMCP(name="no-cap-server", version="0.1")

    @server.tool(description="Try sampling")
    async def try_sample(text: str, ctx: Context) -> str:
        await ctx.sample(messages=[], max_tokens=10)
        return "should not reach"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            # No sampling handler registered — capability is False
            result = await client.call_tool("try_sample", {"text": "hi"})
            assert result.is_error
            assert "sampling" in result.content[0].text.lower()


@pytest.mark.asyncio
async def test_grpc_ctx_logging_and_progress():
    """Tool calls ctx.info() and ctx.report_progress(); client receives notifications."""
    server = FasterMCP(name="log-server", version="0.1")

    @server.tool()
    async def tracked_tool(text: str, ctx: Context) -> str:
        """A tool that logs and reports progress."""
        await ctx.info("starting", extra={"input": text})
        await ctx.report_progress(progress=50, total=100)
        await ctx.info("done")
        return text

    log_received = []
    progress_received = []

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            client.on_notification("log", lambda p: log_received.append(json.loads(p)))
            client.on_notification("progress", lambda p: progress_received.append(json.loads(p)))
            result = await client.call_tool("tracked_tool", {"text": "hello"})
            await asyncio.sleep(0.2)

            assert result.content[0].text == "hello"
            assert not result.is_error

            assert len(log_received) == 2
            assert log_received[0]["level"] == "info"
            assert log_received[0]["message"] == "starting"
            assert log_received[0]["extra"] == {"input": "hello"}
            assert log_received[1]["message"] == "done"

            assert len(progress_received) == 1
            assert progress_received[0]["progress"] == 50
            assert progress_received[0]["total"] == 100


@pytest.mark.asyncio
async def test_grpc_list_resource_templates():
    """Register a resource template, list it, verify all fields are returned."""
    server = FasterMCP(name="template-server", version="0.1")

    @server.resource_template(
        uri_template="res://items/{id}",
        description="Fetch an item by ID",
        mime_type="application/json",
    )
    async def get_item(id: str) -> str:
        return f'{{"id": "{id}"}}'

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.list_resource_templates()
            assert len(result.items) == 1
            tmpl = result.items[0]
            assert tmpl.uri_template == "res://items/{id}"
            assert tmpl.description == "Fetch an item by ID"
            assert tmpl.mime_type == "application/json"
            assert result.next_cursor is None


@pytest.mark.asyncio
async def test_grpc_completions_roundtrip():
    """Register a completion handler; client calls complete(); verify suggestions returned."""
    server = FasterMCP(name="completion-server", version="0.1")

    @server.completion("my_tool")
    async def suggest_city(argument_name: str, value: str) -> list[str]:
        cities = ["london", "paris", "berlin", "madrid", "rome"]
        return [c for c in cities if c.startswith(value)]

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.complete(
                ref_type="tool",
                ref_name="my_tool",
                argument_name="city",
                value="l",
            )
            assert result.values == ["london"]
            assert result.total == 1
            assert not result.has_more


@pytest.mark.asyncio
async def test_grpc_list_tools_all_returned():
    """Registering 5 tools: list_tools returns all 5 at once; next_cursor is None."""
    server = FasterMCP(name="paginate-server", version="0.1")

    @server.tool(description="Tool alpha")
    async def alpha() -> str:
        return "alpha"

    @server.tool(description="Tool beta")
    async def beta() -> str:
        return "beta"

    @server.tool(description="Tool gamma")
    async def gamma() -> str:
        return "gamma"

    @server.tool(description="Tool delta")
    async def delta() -> str:
        return "delta"

    @server.tool(description="Tool epsilon")
    async def epsilon() -> str:
        return "epsilon"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.list_tools()
            assert len(result.items) == 5
            names = {t.name for t in result.items}
            assert names == {"alpha", "beta", "gamma", "delta", "epsilon"}
            assert result.next_cursor is None


@pytest.mark.asyncio
async def test_grpc_cancel_no_crash():
    """Cancelling a non-existent request_id is silently ignored; subsequent calls succeed."""
    server = FasterMCP(name="cancel-server", version="0.1")

    @server.tool(description="Echo")
    async def echo(text: str) -> str:
        return text

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            # Send a cancel for a request_id that has never existed
            await client.cancel(9999)
            # Server must still be operational
            result = await client.call_tool("echo", {"text": "still works"})
            assert result.content[0].text == "still works"
            assert not result.is_error


@pytest.mark.asyncio
async def test_grpc_resource_subscribe():
    """Client subscribes to a resource URI; server fires resource_updated; client receives it."""
    server = FasterMCP(name="subscribe-server", version="0.1")

    @server.resource(uri="res://counter", description="A counter")
    async def counter() -> str:
        return "0"

    subscribed_uris: list[str] = []
    server.on_resource_subscribe(lambda uri: subscribed_uris.append(uri))

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            notifications: list[dict] = []
            client.on_notification(
                "resource_updated", lambda p: notifications.append(json.loads(p))
            )

            await client.subscribe_resource("res://counter")
            await asyncio.sleep(0.1)  # let subscription arrive at server

            assert subscribed_uris == ["res://counter"]

            server.notify_resource_updated("res://counter")
            await asyncio.sleep(0.2)

            assert len(notifications) == 1
            assert notifications[0]["uri"] == "res://counter"


@pytest.mark.asyncio
async def test_grpc_roots_roundtrip():
    """Tool calls ctx.list_roots(); client roots handler returns roots; server receives them."""
    server = FasterMCP(name="roots-server", version="0.1")

    @server.tool(description="Get roots")
    async def get_roots(ctx: Context) -> str:
        response = await ctx.list_roots()
        return ",".join(r.uri for r in response.roots)

    async with server:
        client = Client(f"localhost:{server.port}")

        async def roots_handler() -> mcp_pb2.ListRootsResponse:
            return mcp_pb2.ListRootsResponse(
                roots=[
                    mcp_pb2.Root(uri="file:///home/user/project", name="project"),
                    mcp_pb2.Root(uri="file:///home/user/docs", name="docs"),
                ]
            )

        client.set_roots_handler(roots_handler)
        await client.connect()

        try:
            result = await client.call_tool("get_roots", {})
            assert not result.is_error
            uris = set(result.content[0].text.split(","))
            assert uris == {"file:///home/user/project", "file:///home/user/docs"}
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# Resource template reading (Issue 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grpc_read_resource_via_template():
    """read_resource matches a URI against a registered template."""
    server = FasterMCP(name="tmpl-server", version="0.1")

    @server.resource_template("res://items/{item_id}", description="An item")
    async def get_item(item_id: str) -> str:
        return f"item:{item_id}"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.read_resource("res://items/42")
            assert result.content[0].text == "item:42"


@pytest.mark.asyncio
async def test_grpc_read_resource_exact_match_preferred():
    """A static resource beats a matching template for the same URI."""
    server = FasterMCP(name="tmpl-server", version="0.1")

    @server.resource(uri="res://items/special", description="Static special item")
    async def special_item() -> str:
        return "static-special"

    @server.resource_template("res://items/{item_id}", description="An item")
    async def get_item(item_id: str) -> str:
        return f"item:{item_id}"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.read_resource("res://items/special")
            assert result.content[0].text == "static-special"


@pytest.mark.asyncio
async def test_grpc_read_resource_template_multi_segment():
    """Wildcard {path*} matches multiple path segments."""
    server = FasterMCP(name="tmpl-server", version="0.1")

    @server.resource_template("res://files/{path*}", description="A file")
    async def get_file(path: str) -> str:
        return f"file:{path}"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.read_resource("res://files/a/b/c.txt")
            assert result.content[0].text == "file:a/b/c.txt"


@pytest.mark.asyncio
async def test_grpc_read_resource_template_not_found():
    """A URI that matches no template returns a 404 error."""
    server = FasterMCP(name="tmpl-server", version="0.1")

    @server.resource_template("res://items/{item_id}", description="An item")
    async def get_item(item_id: str) -> str:
        return f"item:{item_id}"

    from mcp_grpc.errors import McpError

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            with pytest.raises(McpError) as exc_info:
                await client.read_resource("res://other/99")
            assert exc_info.value.code == 404


# ---------------------------------------------------------------------------
# Error reply when no client handler registered (Issue 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sampling_no_handler_returns_error():
    """ctx.sample() gets McpError (not timeout) when client has no sampling handler."""
    server = FasterMCP(name="sampling-server", version="0.1")

    @server.tool(description="Sample")
    async def do_sample(ctx: Context) -> str:
        try:
            await ctx.sample(
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10,
            )
        except Exception:
            return "got-error"
        return "done"

    async with server:
        # Connect without a sampling handler — client will send error reply
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("do_sample", {})
            assert not result.is_error
            assert result.content[0].text == "got-error"


@pytest.mark.asyncio
async def test_elicitation_no_handler_returns_error():
    """ctx.elicit() gets McpError (not timeout) when client has no elicitation handler."""
    server = FasterMCP(name="elicit-server", version="0.1")

    @server.tool(description="Elicit")
    async def do_elicit(ctx: Context) -> str:
        try:
            await ctx.elicit(
                message="Pick one",
                schema='{"type":"object","properties":{}}',
            )
        except Exception:
            return "got-error"
        return "done"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.call_tool("do_elicit", {})
            assert not result.is_error
            assert result.content[0].text == "got-error"
