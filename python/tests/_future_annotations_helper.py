"""Helper module that uses ``from __future__ import annotations``.

Used by test_server.py to verify that context injection works even when
the tool-defining module has PEP 563 deferred annotations enabled.
"""

from __future__ import annotations

from rapidmcp.context import Context
from rapidmcp.server import RapidMCP


def register_tool_with_future_annotations(app: RapidMCP) -> None:
    """Register a tool whose annotations are strings at runtime."""

    @app.tool(description="Tool defined with future annotations")
    async def future_tool(text: str, ctx: Context) -> str:
        return f"got ctx: {ctx is not None}, text: {text}"
