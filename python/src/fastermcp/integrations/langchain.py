"""LangChain integration — MCPToolkit for FasterMCP gRPC servers.

``MCPToolkit`` fetches tools from a FasterMCP gRPC server and returns them as
LangChain ``StructuredTool`` instances ready for use with any LangChain agent.

Usage::

    from fastermcp.integrations.langchain import MCPToolkit

    async with MCPToolkit("mcp-server:50051") as toolkit:
        tools = await toolkit.aget_tools()
        agent = create_react_agent(llm, tools)
        result = await agent.ainvoke({"messages": [("human", "...")]})

    # Or manage the lifecycle explicitly:
    toolkit = MCPToolkit("mcp-server:50051")
    await toolkit.client.connect()
    tools = await toolkit.aget_tools()
    ...
    await toolkit.client.close()

Requires: pip install langchain-core
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from fastermcp.client import Client
from fastermcp.types import CallToolResult, Tool

logger = logging.getLogger(__name__)

try:
    from langchain_core.tools import BaseTool, StructuredTool
    from pydantic import BaseModel, Field, create_model
except ImportError as e:
    raise ImportError(
        "langchain-core and pydantic are required for the LangChain integration.\n"
        "Install them with: pip install 'langchain-core'"
    ) from e

# ---------------------------------------------------------------------------
# JSON Schema → Pydantic model
# ---------------------------------------------------------------------------

_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _json_schema_to_model(tool_name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Build a Pydantic model from a JSON Schema *object* definition.

    Only top-level ``properties`` are mapped; nested object schemas and exotic
    keywords (``anyOf``, ``$ref``, etc.) fall back to ``Any``.  This covers the
    vast majority of real-world MCP tool schemas without a full JSON-Schema
    implementation dependency.
    """
    properties: dict[str, Any] = schema.get("properties") or {}
    required: set[str] = set(schema.get("required") or [])

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        raw_type = prop_schema.get("type")
        py_type: type = _JSON_TYPE_MAP.get(raw_type, Any)  # type: ignore[arg-type]
        description: str = prop_schema.get("description", "")

        if prop_name in required:
            fields[prop_name] = (py_type, Field(description=description))
        else:
            fields[prop_name] = (
                py_type | None,  # type: ignore[operator]
                Field(default=None, description=description),
            )

    return create_model(f"{tool_name}Schema", **fields)


# ---------------------------------------------------------------------------
# Content types
# ---------------------------------------------------------------------------

# What goes into ToolMessage.content — shown to the LLM.
ContentBlock = dict[str, Any]
ToolContent = str | list[ContentBlock]

# What goes into ToolMessage.artifact — raw binary data for downstream use.
# Each entry: {"type": "image"|"audio", "mime_type": str, "data": "<base64>"}
ToolArtifact = list[dict[str, Any]] | None


# ---------------------------------------------------------------------------
# CallToolResult → (content, artifact)
# ---------------------------------------------------------------------------


def _convert_result(result: CallToolResult) -> tuple[ToolContent, ToolArtifact]:
    """Convert a ``CallToolResult`` to a ``(content, artifact)`` tuple.

    ``content`` is placed in ``ToolMessage.content`` and shown to the LLM:

    - ``text``     → plain string (single) or ``{"type": "text", "text": ...}`` block
    - ``image``    → ``{"type": "image_url", "image_url": {"url": "data:<mime>;base64,..."}}``
    - ``audio``    → text description (no standard LangChain block for audio)
    - ``resource`` → ``{"type": "text", "text": "[resource: <uri>]"}``
    - error result → ``"Error: <message>"`` — never raises, lets the LLM observe the failure

    ``artifact`` is placed in ``ToolMessage.artifact`` and contains the raw
    base64-encoded binary payloads for image/audio items so downstream code
    can decode and use them without re-fetching.
    """
    if result.is_error:
        error_text = " ".join(c.text for c in result.content if c.text)
        return f"Error: {error_text or 'Tool returned an error with no message'}", None

    if not result.content:
        return "", None

    blocks: list[ContentBlock] = []
    artifacts: list[dict[str, Any]] = []

    for c in result.content:
        if c.type == "text":
            blocks.append({"type": "text", "text": c.text})

        elif c.type == "image" and c.data:
            b64 = base64.b64encode(c.data).decode()
            # image_url is the standard LangChain multi-modal content block;
            # langchain_anthropic converts it to Anthropic's native format.
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{c.mime_type};base64,{b64}"},
                }
            )
            artifacts.append({"type": "image", "mime_type": c.mime_type, "data": b64})

        elif c.type == "audio" and c.data:
            b64 = base64.b64encode(c.data).decode()
            # No standard LangChain content block for audio — describe it as text
            # and park the raw data in the artifact for downstream use.
            blocks.append(
                {
                    "type": "text",
                    "text": f"[audio: {c.mime_type}, {len(c.data)} bytes]",
                }
            )
            artifacts.append({"type": "audio", "mime_type": c.mime_type, "data": b64})

        elif c.type == "resource":
            blocks.append({"type": "text", "text": f"[resource: {c.uri}]"})

    artifact: ToolArtifact = artifacts or None

    # Single text block → plain string (simpler, avoids unnecessary list wrapping)
    if len(blocks) == 1 and blocks[0]["type"] == "text":
        return blocks[0]["text"], artifact

    return blocks, artifact


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def _make_tool(client: Client, mcp_tool: Tool) -> BaseTool:
    """Wrap a single MCP ``Tool`` as a LangChain ``StructuredTool``."""
    schema_model = _json_schema_to_model(mcp_tool.name, mcp_tool.input_schema)
    _name = mcp_tool.name

    async def _arun(**kwargs: Any) -> tuple[ToolContent, ToolArtifact]:
        result = await client.call_tool(_name, kwargs)
        return _convert_result(result)

    return StructuredTool(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        args_schema=schema_model,
        coroutine=_arun,
        response_format="content_and_artifact",
    )


# ---------------------------------------------------------------------------
# Public toolkit
# ---------------------------------------------------------------------------


class MCPToolkit:
    """LangChain toolkit backed by a FasterMCP gRPC server.

    Fetches the server's tool list (with automatic pagination) and returns each
    tool as a LangChain ``StructuredTool``.  Use as an async context manager to
    manage the underlying gRPC connection lifetime automatically.

    Args:
        address: gRPC server address, e.g. ``"mcp-server:50051"``.
        allowed_tools: Optional allowlist of tool names.  ``None`` = all tools.

    Example::

        async with MCPToolkit("localhost:50051") as toolkit:
            tools = await toolkit.aget_tools()
            # pass `tools` to any LangChain agent
    """

    def __init__(
        self,
        address: str,
        *,
        allowed_tools: list[str] | None = None,
    ) -> None:
        self._address = address
        self._client = Client(address)
        self._allowed_tools = set(allowed_tools) if allowed_tools else None

    @property
    def client(self) -> Client:
        """The underlying :class:`~fastermcp.client.Client` instance."""
        return self._client

    async def aget_tools(self) -> list[BaseTool]:
        """Fetch all tools from the server and return them as LangChain tools.

        Follows pagination automatically so all tools are returned even when
        the server uses cursors.
        """
        lc_tools: list[BaseTool] = []
        cursor: str | None = None

        while True:
            result = await self._client.list_tools(cursor=cursor)
            for mcp_tool in result.items:
                if self._allowed_tools and mcp_tool.name not in self._allowed_tools:
                    continue
                lc_tools.append(_make_tool(self._client, mcp_tool))
            if not result.next_cursor:
                break
            cursor = result.next_cursor

        logger.info(
            "MCPToolkit %s — %d tool(s): %s",
            self._address,
            len(lc_tools),
            [t.name for t in lc_tools],
        )
        return lc_tools

    async def __aenter__(self) -> MCPToolkit:
        await self._client.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.close()

    def __repr__(self) -> str:
        allowed = f", allowed_tools={sorted(self._allowed_tools)}" if self._allowed_tools else ""
        return f"MCPToolkit(address={self._address!r}{allowed})"
