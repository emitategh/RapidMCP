"""LiveKit integration — MCPServerGRPC adapter for livekit-agents.

Usage:
    from mcp_grpc.integrations.livekit import MCPServerGRPC

    session = AgentSession(
        mcp_servers=[MCPServerGRPC(address="mcp-server:50051")],
        ...
    )

Requires: pip install livekit-agents
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp_grpc.client import McpClient

logger = logging.getLogger(__name__)

try:
    from livekit.agents.llm.mcp import MCPServer, MCPTool, MCPToolResultContext
    from livekit.agents.llm.tool_context import ToolError, function_tool
except ImportError as e:
    raise ImportError(
        "livekit-agents is required for the LiveKit integration but is not installed.\n"
        "Install it with: pip install 'livekit-agents'"
    ) from e


class MCPServerGRPC(MCPServer):
    """LiveKit-compatible MCP server backed by FasterMCP's gRPC transport.

    Plugs into LiveKit's ``mcp_servers=[]`` parameter on ``AgentSession``.
    Tools are discovered and called over gRPC via ``McpClient``.
    """

    def __init__(
        self,
        address: str,
        allowed_tools: list[str] | None = None,
        client_session_timeout_seconds: float = 30,
    ) -> None:
        super().__init__(client_session_timeout_seconds=client_session_timeout_seconds)
        self._address = address
        self._grpc_client = McpClient(address)
        self._allowed_tools = set(allowed_tools) if allowed_tools else None
        self._connected = False

    @property
    def initialized(self) -> bool:
        return self._connected

    async def initialize(self) -> None:
        if self._connected:
            return
        await self._grpc_client.connect()
        self._connected = True
        logger.info(f"MCPServerGRPC connected to {self._address}")

    async def list_tools(self) -> list[MCPTool]:
        if not self._connected:
            raise RuntimeError("MCPServerGRPC isn't initialized — call initialize() first")

        result = await self._grpc_client.list_tools()
        tools: list[MCPTool] = []

        for t in result.items:
            schema = json.loads(t.input_schema) if t.input_schema else {}
            name = t.name
            description = t.description

            if self._allowed_tools and name not in self._allowed_tools:
                continue

            async def _tool_called(raw_arguments: dict[str, Any], _name: str = name) -> str:
                import time
                if not self._connected:
                    raise ToolError(
                        "Tool invocation failed: gRPC connection is closed. "
                        "Check that the MCPServerGRPC is still running."
                    )

                t0 = time.perf_counter()
                tool_result = await self._grpc_client.call_tool(_name, raw_arguments)
                ms = (time.perf_counter() - t0) * 1000
                logger.info(f"[gRPC] {_name} — {ms:.2f}ms")

                if tool_result.is_error:
                    error_str = "\n".join(
                        item.text for item in tool_result.content if item.text
                    )
                    raise ToolError(error_str)

                if not tool_result.content:
                    raise ToolError(
                        f"Tool '{_name}' completed without producing a result."
                    )

                if len(tool_result.content) == 1:
                    return tool_result.content[0].text
                return json.dumps([
                    {"type": item.type, "text": item.text} for item in tool_result.content
                ])

            tools.append(function_tool(
                _tool_called,
                raw_schema={
                    "name": name,
                    "description": description,
                    "parameters": schema,
                },
            ))

        self._lk_tools = tools
        self._cache_dirty = False
        return tools

    async def aclose(self) -> None:
        if self._connected:
            await self._grpc_client.close()
            self._connected = False
            logger.info(f"MCPServerGRPC disconnected from {self._address}")

    def client_streams(self):
        raise NotImplementedError(
            "MCPServerGRPC uses gRPC transport directly, not MCP client streams"
        )

    def __repr__(self) -> str:
        allowed = f", allowed_tools={list(self._allowed_tools)}" if self._allowed_tools else ""
        return f"MCPServerGRPC(address={self._address}{allowed})"
