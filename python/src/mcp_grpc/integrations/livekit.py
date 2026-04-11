"""LiveKit integration — MCPToolsetGRPC adapter for livekit-agents.

MCPToolsetGRPC is a livekit ``Toolset`` backed by a FasterMCP gRPC server.
It owns the ``Client`` lifecycle and is the gRPC counterpart to livekit's
built-in ``MCPToolset(MCPServerHTTP(url=...))``.

Usage::

    from livekit.agents.llm.mcp import MCPToolset, MCPServerHTTP
    from mcp_grpc.integrations.livekit import MCPToolsetGRPC

    session = AgentSession(
        tools=[
            MCPToolsetGRPC(address="mcp-server:50051"),
            MCPToolset(id="http-tools", mcp_server=MCPServerHTTP(url="http://...")),
        ],
        ...
    )

Requires: pip install livekit-agents
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp_grpc.client import Client

logger = logging.getLogger(__name__)

try:
    from livekit.agents.llm.tool_context import ToolError, Toolset, function_tool
    from typing_extensions import Self
except ImportError as e:
    raise ImportError(
        "livekit-agents is required for the LiveKit integration.\n"
        "Install it with: pip install 'livekit-agents'"
    ) from e


class MCPToolsetGRPC(Toolset):
    """LiveKit Toolset backed by a FasterMCP gRPC server.

    Owns the ``Client`` connection lifecycle: connects on ``setup()``,
    closes on ``aclose()``. Tools are fetched once on setup and cached.

    Args:
        address: gRPC server address, e.g. ``"mcp-server:50051"``.
        allowed_tools: Optional allowlist of tool names. ``None`` = all tools.
        id: Toolset ID (defaults to the server address).
    """

    def __init__(
        self,
        address: str,
        *,
        allowed_tools: list[str] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id or f"grpc:{address}")
        self._address = address
        self._allowed = set(allowed_tools) if allowed_tools else None
        self._client = Client(address)

    async def setup(self, *, reload: bool = False) -> Self:
        await super().setup()
        if self._tools and not reload:
            return self

        await self._client.connect()
        result = await self._client.list_tools()

        tools = []
        for t in result.items:
            if self._allowed and t.name not in self._allowed:
                continue
            schema = json.loads(t.input_schema) if t.input_schema else {}
            _name, _desc = t.name, t.description

            async def _call(raw_arguments: dict[str, Any], _n: str = _name) -> str:
                tool_result = await self._client.call_tool(_n, raw_arguments)
                if tool_result.is_error:
                    raise ToolError(
                        "\n".join(i.text for i in tool_result.content if i.text)
                    )
                if not tool_result.content:
                    raise ToolError(f"Tool '{_n}' returned no content")
                if len(tool_result.content) == 1:
                    return tool_result.content[0].text
                return json.dumps(
                    [{"type": i.type, "text": i.text} for i in tool_result.content]
                )

            tools.append(function_tool(_call, raw_schema={
                "name": _name,
                "description": _desc,
                "parameters": schema,
            }))

        self._tools = tools
        logger.info(
            "MCPToolsetGRPC connected to %s — %d tool(s): %s",
            self._address, len(tools), [t.name for t in result.items],
        )
        return self

    async def aclose(self) -> None:
        await super().aclose()
        await self._client.close()
        logger.info("MCPToolsetGRPC disconnected from %s", self._address)

    def __repr__(self) -> str:
        allowed = f", allowed={list(self._allowed)}" if self._allowed else ""
        return f"MCPToolsetGRPC(address={self._address!r}{allowed})"
