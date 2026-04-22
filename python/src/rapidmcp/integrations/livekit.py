"""LiveKit integration — MCPServerGRPC for livekit-agents.

MCPServerGRPC is a livekit ``MCPServer`` backed by a RapidMCP gRPC server.
It is the gRPC counterpart to ``MCPServerHTTP`` and is used with the standard
``MCPToolset``.

Usage::

    from livekit.agents.llm.mcp import MCPToolset, MCPServerHTTP
    from rapidmcp.integrations.livekit import MCPServerGRPC

    session = AgentSession(
        tools=[
            MCPToolset(id="grpc-tools", mcp_server=MCPServerGRPC(address="mcp-server:50051")),
            MCPToolset(id="http-tools", mcp_server=MCPServerHTTP(url="http://...")),
        ],
        ...
    )

Requires: pip install livekit-agents
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from rapidmcp.auth import ClientTLSConfig
from rapidmcp.client import Client

logger = logging.getLogger(__name__)

try:
    from livekit.agents.llm.mcp import MCPServer, MCPTool
    from livekit.agents.llm.tool_context import ToolError, function_tool
except ImportError as e:
    raise ImportError(
        "livekit-agents is required for the LiveKit integration.\n"
        "Install it with: pip install 'livekit-agents'"
    ) from e


class MCPServerGRPC(MCPServer):
    """gRPC-backed MCPServer for livekit-agents.

    Use with the standard ``MCPToolset``:

        MCPToolset(id="grpc-tools", mcp_server=MCPServerGRPC(address="mcp-server:50051"))

    Args:
        address: gRPC server address, e.g. ``"mcp-server:50051"``.
        token: Optional bearer token sent as ``authorization`` metadata on every call.
        tls: Optional :class:`~rapidmcp.auth.ClientTLSConfig` for TLS/mTLS connections.
        allowed_tools: Optional allowlist of tool names. ``None`` = all tools.
    """

    def __init__(
        self,
        address: str,
        *,
        token: str | None = None,
        tls: ClientTLSConfig | None = None,
        allowed_tools: list[str] | None = None,
        client_session_timeout_seconds: float = 30,
    ) -> None:
        super().__init__(client_session_timeout_seconds=client_session_timeout_seconds)
        self._address = address
        self._grpc_client = Client(address, token=token, tls=tls)
        self._allowed_tools = set(allowed_tools) if allowed_tools else None
        self._connected = False
        self._init_lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._connected

    async def initialize(self) -> None:
        if self._connected:
            return
        if not hasattr(self, "_init_lock"):
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self._connected:
                return
            await self._grpc_client.connect()
            self._connected = True
            logger.info("MCPServerGRPC connected to %s", self._address)

    async def list_tools(self) -> list[MCPTool]:
        if not self._cache_dirty and self._lk_tools is not None:
            return self._lk_tools

        result = await self._grpc_client.list_tools()
        tools: list[MCPTool] = []
        for t in result.items:
            if self._allowed_tools and t.name not in self._allowed_tools:
                continue
            _name, _desc = t.name, t.description

            async def _call(raw_arguments: dict[str, Any], _n: str = _name) -> str:
                tool_result = await self._grpc_client.call_tool(_n, raw_arguments)
                if tool_result.is_error:
                    raise ToolError("\n".join(c.text for c in tool_result.content if c.text))
                if not tool_result.content:
                    raise ToolError(f"Tool '{_n}' returned no content")
                c0 = tool_result.content[0]
                if len(tool_result.content) == 1:
                    if c0.type == "text":
                        return c0.text
                    # image / audio — return base64 data with mime type
                    import base64
                    import json as _json

                    return _json.dumps(
                        {
                            "type": c0.type,
                            "mimeType": c0.mime_type,
                            "data": base64.b64encode(c0.data).decode(),
                        }
                    )
                import json as _json

                return _json.dumps(
                    [
                        {"type": c.type, "text": c.text}
                        if c.type == "text"
                        else {"type": c.type, "uri": c.uri}
                        if c.type == "resource"
                        else {"type": c.type, "mimeType": c.mime_type}
                        for c in tool_result.content
                    ]
                )

            tools.append(
                function_tool(
                    _call,
                    raw_schema={
                        "name": _name,
                        "description": _desc,
                        "parameters": t.input_schema,
                    },
                )
            )

        self._lk_tools = tools
        self._cache_dirty = False
        logger.info(
            "MCPServerGRPC %s — %d tool(s): %s",
            self._address,
            len(tools),
            [t.name for t in result.items],
        )
        return tools

    async def aclose(self) -> None:
        self._connected = False
        await self._grpc_client.close()
        logger.info("MCPServerGRPC disconnected from %s", self._address)

    @contextlib.asynccontextmanager
    async def client_streams(self):  # type: ignore[override]
        # Never called — initialize() and list_tools() bypass the JSON-RPC path.
        # Implemented only to satisfy the abstract base class requirement.
        raise NotImplementedError("MCPServerGRPC uses gRPC transport, not client_streams")
        yield  # pragma: no cover

    def __repr__(self) -> str:
        allowed = f", allowed_tools={list(self._allowed_tools)}" if self._allowed_tools else ""
        return f"MCPServerGRPC(address={self._address!r}{allowed})"
