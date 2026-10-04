"""A v2 client answers input rounds with the elicitation handler it already has."""

import pytest

from rapidmcp import BoolField, Client, Context, RapidMCP, StringField
from rapidmcp._generated import mcp_pb2
from rapidmcp.errors import McpError


@pytest.fixture
async def server():
    srv = RapidMCP(name="input", version="1.0", state_secret="k")

    @srv.tool()
    async def deploy(service: str, ctx: Context) -> str:
        answer = await ctx.elicit("Deploy to production?", fields={"confirm": BoolField()})
        if not answer.accepted:
            return f"not deployed ({answer.action})"
        return f"deployed {service} confirm={answer.data.get('confirm')}"

    @srv.tool()
    async def two_questions(ctx: Context) -> str:
        first = await ctx.elicit("Name?", fields={"name": StringField()})
        second = await ctx.elicit("Colour?", fields={"colour": StringField()})
        return f"{first.data['name']} likes {second.data['colour']}"

    @srv.tool()
    async def pay(ctx: Context) -> str:
        answer = await ctx.elicit("Complete the payment", url="https://pay.example/session/42")
        return f"payment {answer.action}"

    @srv.tool()
    async def never_satisfied(ctx: Context) -> str:
        for attempt in range(100):
            await ctx.elicit("Again?", fields={"ok": BoolField()}, key=f"attempt-{attempt}")
        return "unreachable"

    async with srv:
        yield srv


def _reply(action="accept", content="{}"):
    return mcp_pb2.ElicitationResponse(action=action, content=content)


@pytest.mark.parametrize("mode", ["legacy", "modern"])
async def test_one_tool_works_on_both_protocol_versions(server, mode):
    client = Client(f"localhost:{server.port}", mode=mode)
    seen: list[str] = []

    async def handler(request):
        seen.append(request.message)
        return _reply(content='{"confirm": true}')

    client.set_elicitation_handler(handler)
    async with client:
        result = await client.call_tool("deploy", {"service": "api"})

    assert result.content[0].text == "deployed api confirm=True"
    assert seen == ["Deploy to production?"]


async def test_handler_gets_the_form_schema_on_v2(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    requests = []

    async def handler(request):
        requests.append(request)
        return _reply()

    client.set_elicitation_handler(handler)
    async with client:
        await client.call_tool("deploy", {"service": "api"})

    assert (requests[0].mode, requests[0].url) == ("form", "")
    assert '"confirm"' in requests[0].schema


async def test_two_questions_are_asked_in_order(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    asked: list[str] = []

    async def handler(request):
        asked.append(request.message)
        content = '{"name": "Ada"}' if request.message == "Name?" else '{"colour": "green"}'
        return _reply(content=content)

    client.set_elicitation_handler(handler)
    async with client:
        result = await client.call_tool("two_questions")

    assert asked == ["Name?", "Colour?"]
    assert result.content[0].text == "Ada likes green"


async def test_declining_reaches_the_tool(server):
    client = Client(f"localhost:{server.port}", mode="modern")

    async def handler(request):
        return _reply(action="decline", content="")

    client.set_elicitation_handler(handler)
    async with client:
        result = await client.call_tool("deploy", {"service": "api"})

    assert result.content[0].text == "not deployed (decline)"


async def test_url_mode_reaches_a_handler_that_declared_it(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    requests = []

    async def handler(request):
        requests.append(request)
        return _reply(content="")

    client.set_elicitation_handler(handler, url=True)
    async with client:
        result = await client.call_tool("pay")

    assert (requests[0].mode, requests[0].url) == ("url", "https://pay.example/session/42")
    assert result.content[0].text == "payment accept"


async def test_url_mode_without_declaring_it_is_a_missing_capability(server):
    client = Client(f"localhost:{server.port}", mode="modern")

    async def handler(request):
        return _reply()

    client.set_elicitation_handler(handler)
    async with client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("pay")

    assert exc.value.code == -32021
    assert exc.value.data == {"required_capabilities": ["elicitation.url"]}


async def test_no_handler_is_a_missing_capability(server):
    async with Client(f"localhost:{server.port}", mode="modern") as client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("deploy", {"service": "api"})

    assert exc.value.code == -32021


async def test_client_gives_up_on_a_server_that_never_stops_asking(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    rounds = 0

    async def handler(request):
        nonlocal rounds
        rounds += 1
        return _reply()

    client.set_elicitation_handler(handler)
    async with client:
        with pytest.raises(McpError) as exc:
            await client.call_tool("never_satisfied")

    assert exc.value.code == 508
    assert rounds == 10
