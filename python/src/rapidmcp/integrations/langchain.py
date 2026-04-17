"""LangChain integration — RapidMCPClient for RapidMCP gRPC servers.

``RapidMCPClient`` aggregates tools, resources, and prompts from one or more
RapidMCP gRPC servers and exposes them as LangChain-compatible objects.

Usage::

    from rapidmcp.integrations.langchain import RapidMCPClient

    async with RapidMCPClient({"docs": {"address": "docs:50051"}}) as rc:
        tools = await rc.get_tools()

Requires: pip install langchain-core
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from rapidmcp.auth import ClientTLSConfig
from rapidmcp.client import Client
from rapidmcp.types import CallToolResult, GetPromptResult, ReadResourceResult, Tool

logger = logging.getLogger(__name__)

try:
    from langchain_core.document_loaders.blob_loaders import Blob
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
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
# ReadResourceResult → Blob
# ---------------------------------------------------------------------------


def _read_resource_to_blob(uri: str, result: ReadResourceResult) -> Blob:
    """Flatten a ``ReadResourceResult`` into a single LangChain ``Blob``.

    Multiple content items are rare. Text items are concatenated; binary
    items are appended; the first non-empty mime_type wins.
    """
    text_parts: list[str] = []
    binary: bytes | None = None
    mime: str = ""

    for c in result.content:
        if c.mime_type and not mime:
            mime = c.mime_type
        if c.type == "text":
            text_parts.append(c.text)
        elif c.data:
            binary = (binary or b"") + c.data

    if binary is not None:
        return Blob(data=binary, mimetype=mime or "application/octet-stream", metadata={"uri": uri})
    return Blob(
        data="".join(text_parts).encode(), mimetype=mime or "text/plain", metadata={"uri": uri}
    )


# ---------------------------------------------------------------------------
# GetPromptResult → LangChain messages
# ---------------------------------------------------------------------------

_ROLE_TO_MESSAGE: dict[str, type[BaseMessage]] = {
    "user": HumanMessage,
    "human": HumanMessage,
    "assistant": AIMessage,
    "ai": AIMessage,
    "system": SystemMessage,
}


def _get_prompt_to_messages(result: GetPromptResult) -> list[BaseMessage]:
    """Convert an MCP ``GetPromptResult`` to a list of LangChain messages.

    Non-text content items are serialised as ``[image: <mime>]`` etc. —
    LangChain's agent loop feeds these verbatim into the LLM.
    """
    out: list[BaseMessage] = []
    for pm in result.messages:
        cls = _ROLE_TO_MESSAGE.get(pm.role, HumanMessage)
        c = pm.content
        if c.type == "text":
            body: str = c.text
        elif c.type == "image":
            body = f"[image: {c.mime_type}, {len(c.data)} bytes]"
        elif c.type == "audio":
            body = f"[audio: {c.mime_type}, {len(c.data)} bytes]"
        else:
            body = f"[resource: {c.uri}]"
        out.append(cls(content=body))
    return out


@dataclass(frozen=True)
class _ServerConfig:
    address: str
    token: str | None = None
    tls: ClientTLSConfig | None = None
    allowed_tools: frozenset[str] | None = None


class RapidMCPClient:
    """Multi-server LangChain adapter for RapidMCP gRPC servers.

    Mirrors ``langchain_mcp_adapters.client.MultiServerMCPClient``'s surface
    over gRPC. Accepts a mapping of ``{server_name: {address, token, tls,
    allowed_tools}}`` and aggregates tools/resources/prompts across servers.

    Example::

        async with RapidMCPClient({
            "docs":  {"address": "docs:50051"},
            "sql":   {"address": "sql:50051",  "token": "..."},
        }) as rc:
            tools = await rc.get_tools()
    """

    def __init__(self, servers: dict[str, dict[str, Any]]) -> None:
        if not servers:
            raise ValueError("RapidMCPClient requires at least one server config")
        self._configs: dict[str, _ServerConfig] = {}
        self._clients: dict[str, Client] = {}
        for name, cfg in servers.items():
            sc = _ServerConfig(
                address=cfg["address"],
                token=cfg.get("token"),
                tls=cfg.get("tls"),
                allowed_tools=(
                    frozenset(cfg["allowed_tools"]) if cfg.get("allowed_tools") else None
                ),
            )
            self._configs[name] = sc
            self._clients[name] = Client(sc.address, token=sc.token, tls=sc.tls)

    @property
    def servers(self) -> list[str]:
        """Names of the configured servers, in insertion order."""
        return list(self._configs)

    def client(self, server_name: str) -> Client:
        """Return the underlying :class:`~rapidmcp.client.Client` for one server."""
        try:
            return self._clients[server_name]
        except KeyError as exc:
            raise KeyError(
                f"Unknown server {server_name!r}. Configured: {sorted(self._configs)}"
            ) from exc

    async def connect(self) -> None:
        """Open gRPC streams to every configured server, concurrently."""
        await asyncio.gather(*(c.connect() for c in self._clients.values()))

    async def close(self) -> None:
        """Close every underlying Client, concurrently. Exceptions surface."""
        await asyncio.gather(*(c.close() for c in self._clients.values()))

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        """Fetch tools from one or all servers.

        Args:
            server_name: If given, only return tools from that server. Otherwise
                tools from every configured server are aggregated.

        Tool-name collisions across servers are NOT deduplicated — configure
        ``allowed_tools`` per server if you expect overlap.
        """
        names = [server_name] if server_name is not None else list(self._configs)
        for n in names:
            if n not in self._configs:
                raise KeyError(f"Unknown server {n!r}")

        results = await asyncio.gather(*(self._list_all_tools(n) for n in names))

        lc_tools: list[BaseTool] = []
        for name, mcp_tools in zip(names, results, strict=True):
            allowed = self._configs[name].allowed_tools
            client = self._clients[name]
            for mcp_tool in mcp_tools:
                if allowed is not None and mcp_tool.name not in allowed:
                    continue
                lc_tools.append(_make_tool(client, mcp_tool))
        return lc_tools

    async def _list_all_tools(self, server_name: str) -> list[Tool]:
        client = self._clients[server_name]
        items: list[Tool] = []
        cursor: str | None = None
        while True:
            result = await client.list_tools(cursor=cursor)
            items.extend(result.items)
            if not result.next_cursor:
                return items
            cursor = result.next_cursor

    async def get_resources(
        self,
        server_name: str,
        *,
        uris: list[str] | None = None,
    ) -> list[Blob]:
        """Read resources from one server as LangChain ``Blob`` objects.

        Args:
            server_name: Which configured server to read from.
            uris: If given, read exactly these URIs. Otherwise, list every
                resource the server exposes (with pagination) and read them all.
        """
        client = self.client(server_name)
        if uris is None:
            uris = []
            cursor: str | None = None
            while True:
                listing = await client.list_resources(cursor=cursor)
                uris.extend(r.uri for r in listing.items)
                if not listing.next_cursor:
                    break
                cursor = listing.next_cursor

        reads = await asyncio.gather(*(client.read_resource(u) for u in uris))
        return [_read_resource_to_blob(u, r) for u, r in zip(uris, reads, strict=True)]

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        *,
        arguments: dict[str, str] | None = None,
    ) -> list[BaseMessage]:
        """Fetch and render a prompt from one server as LangChain messages."""
        result = await self.client(server_name).get_prompt(prompt_name, arguments or {})
        return _get_prompt_to_messages(result)

    @contextlib.asynccontextmanager
    async def session(self, server_name: str) -> AsyncIterator[Client]:
        """Async context manager yielding the raw :class:`Client` for one server.

        Useful for features not wrapped by this adapter — ``ping``, resource
        subscription, sampling handlers, completion, etc. Lifecycle is owned
        by the :class:`RapidMCPClient`; this does not open or close the stream.
        """
        yield self.client(server_name)

    async def __aenter__(self) -> RapidMCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
