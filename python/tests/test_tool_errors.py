"""What a client sees when a tool handler raises."""

from rapidmcp import RapidMCP
from rapidmcp.errors import ToolError


def _server(**kwargs) -> RapidMCP:
    server = RapidMCP(name="errors", version="0.1", **kwargs)

    @server.tool()
    async def friendly() -> str:
        raise ToolError("The order id must start with ORD-")

    @server.tool()
    async def boom() -> str:
        raise RuntimeError("db password is hunter2")

    return server


async def test_tool_error_message_is_returned_verbatim():
    result = await _server().handle_call_tool("friendly", "{}")

    assert result.is_error
    assert [c.text for c in result.content] == ["The order id must start with ORD-"]


async def test_unexpected_exception_is_reported_without_a_traceback():
    result = await _server().handle_call_tool("boom", "{}")

    assert result.is_error
    assert [c.text for c in result.content] == ["Error calling tool 'boom': db password is hunter2"]


async def test_mask_error_details_hides_the_exception_text():
    result = await _server(mask_error_details=True).handle_call_tool("boom", "{}")

    assert result.is_error
    assert [c.text for c in result.content] == ["Error calling tool 'boom'"]


async def test_mask_error_details_still_shows_tool_error_messages():
    result = await _server(mask_error_details=True).handle_call_tool("friendly", "{}")

    assert [c.text for c in result.content] == ["The order id must start with ORD-"]
