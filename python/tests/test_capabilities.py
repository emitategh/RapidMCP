"""Capabilities announced during initialize."""

from rapidmcp import Client, RapidMCP


async def _capabilities(server: RapidMCP) -> dict[str, bool]:
    async with server, Client(f"localhost:{server.port}") as client:
        caps = client.server_info.capabilities
        return {
            "tools": caps.tools,
            "tools_list_changed": caps.tools_list_changed,
            "resources": caps.resources,
            "prompts": caps.prompts,
        }


async def test_resource_templates_alone_count_as_resources():
    server = RapidMCP(name="caps", version="0.1")

    @server.resource_template("res://items/{item_id}")
    async def item(item_id: str) -> str:
        return item_id

    assert await _capabilities(server) == {
        "tools": False,
        "tools_list_changed": True,
        "resources": True,
        "prompts": False,
    }


async def test_tools_list_changed_is_announced_because_the_server_can_send_it():
    server = RapidMCP(name="caps", version="0.1")

    @server.tool()
    async def echo(text: str) -> str:
        return text

    assert await _capabilities(server) == {
        "tools": True,
        "tools_list_changed": True,
        "resources": False,
        "prompts": False,
    }
