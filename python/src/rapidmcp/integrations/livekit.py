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
import base64
import logging
from typing import Any

from rapidmcp.auth import ClientTLSConfig
from rapidmcp.client import Client
from rapidmcp.types import CallToolResult as RapidCallToolResult

logger = logging.getLogger(__name__)

try:
    from livekit.agents.llm.mcp import (
        MCPServer,
        MCPTool,
        MCPToolResultContext,
        MCPToolResultResolver,
    )
    from livekit.agents.llm.tool_context import ToolError, function_tool
except ImportError as e:
    raise ImportError(
        "livekit-agents is required for the LiveKit integration.\n"
        "Install it with: pip install 'livekit-agents'"
    ) from e


def _to_mcp_call_result(res: RapidCallToolResult) -> Any:
    """Convert rapidmcp.types.CallToolResult to mcp.types.CallToolResult so
    that a user-supplied MCPToolResultResolver receives the same type it
    would from MCPServerHTTP."""
    import mcp.types as mcp_types

    parts: list[Any] = []
    for c in res.content:
        if c.type == "text":
            parts.append(mcp_types.TextContent(type="text", text=c.text))
        elif c.type == "image":
            parts.append(
                mcp_types.ImageContent(
                    type="image",
                    data=base64.b64encode(c.data).decode(),
                    mimeType=c.mime_type,
                )
            )
        elif c.type == "audio":
            parts.append(
                mcp_types.AudioContent(
                    type="audio",
                    data=base64.b64encode(c.data).decode(),
                    mimeType=c.mime_type,
                )
            )
        elif c.type == "resource":
            parts.append(
                mcp_types.ResourceLink(
                    type="resource_link",
                    uri=c.uri,  # type: ignore[arg-type]
                    name=c.uri.rsplit("/", 1)[-1] or c.uri,
                )
            )
    return mcp_types.CallToolResult(content=parts, isError=res.is_error)


class MCPServerGRPC(MCPServer):
    """gRPC-backed MCPServer for livekit-agents.

    Use with the standard ``MCPToolset``:

        MCPToolset(id="grpc-tools", mcp_server=MCPServerGRPC(address="mcp-server:50051"))

    Args:
        address: gRPC server address, e.g. ``"mcp-server:50051"``.
        token: Optional bearer token sent as ``authorization`` metadata on every call.
        tls: Optional :class:`~rapidmcp.auth.ClientTLSConfig` for TLS/mTLS connections.
        allowed_tools: Optional allowlist of tool names. ``None`` = all tools.
        client_session_timeout_seconds: Timeout for individual client sessions.
        tool_result_resolver: Optional callable to transform tool results before returning
            them to the agent. Receives an :class:`MCPToolResultContext` and returns any value.
            Defaults to the library's built-in resolver (returns JSON-serialized text for
            single-content results, a JSON list for multi-content).
    """

    def __init__(
        self,
        address: str,
        *,
        token: str | None = None,
        tls: ClientTLSConfig | None = None,
        allowed_tools: list[str] | None = None,
        client_session_timeout_seconds: float = 30,
        tool_result_resolver: MCPToolResultResolver | None = None,
    ) -> None:
        super().__init__(
            client_session_timeout_seconds=client_session_timeout_seconds,
            tool_result_resolver=tool_result_resolver,
        )
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

            async def _call(raw_arguments: dict[str, Any], _n: str = _name) -> Any:
                tool_result = await self._grpc_client.call_tool(_n, raw_arguments)
                if tool_result.is_error:
                    parts: list[str] = []
                    for c in tool_result.content:
                        if c.type == "text" and c.text:
                            parts.append(c.text)
                        elif c.type in ("image", "audio"):
                            parts.append(f"[{c.type}: {c.mime_type}, {len(c.data)} bytes]")
                        elif c.type == "resource":
                            parts.append(f"[resource: {c.uri}]")
                    raise ToolError(
                        "\n".join(parts) if parts else f"Tool '{_n}' failed without a message"
                    )

                mcp_result = _to_mcp_call_result(tool_result)
                ctx = MCPToolResultContext(tool_name=_n, arguments=raw_arguments, result=mcp_result)
                resolved = self._tool_result_resolver(ctx)
                if asyncio.iscoroutine(resolved):
                    resolved = await resolved
                return resolved

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

    def client_streams(self):  # type: ignore[override]
        # MCPServerGRPC bypasses the JSON-RPC ClientSession path entirely —
        # initialize() and list_tools() talk to the gRPC Client directly.
        raise NotImplementedError("MCPServerGRPC uses gRPC transport, not client_streams")

    def __repr__(self) -> str:
        allowed = f", allowed_tools={list(self._allowed_tools)}" if self._allowed_tools else ""
        return f"MCPServerGRPC(address={self._address!r}{allowed})"
