"""Client-side parsed result types.

These dataclasses are what the Client returns from every method.
Field names mirror the proto message fields so existing call sites
(``result.content[0].text``, ``result.is_error``, ``result.items[0].name``)
continue to work without modification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


@dataclass
class ContentItem:
    """A single content block in a tool result or resource/prompt message.

    ``type`` is one of ``"text"``, ``"image"``, ``"audio"``, ``"resource"``.
    Binary content (images, audio) is in ``data``; ``mime_type`` identifies
    the format.  Plain text is in ``text``.  Resource references use ``uri``.
    """

    type: str
    text: str = ""
    data: bytes = b""
    mime_type: str = ""
    uri: str = ""


# ---------------------------------------------------------------------------
# Tool call
# ---------------------------------------------------------------------------


@dataclass
class CallToolResult:
    """Parsed result from a ``call_tool`` request."""

    content: list[ContentItem]
    is_error: bool = False


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------


@dataclass
class ToolAnnotationInfo:
    """Optional hints about a tool's behaviour."""

    title: str = ""
    read_only_hint: bool = False
    destructive_hint: bool = False
    idempotent_hint: bool = False
    open_world_hint: bool = False


@dataclass
class Tool:
    """A tool exposed by the server."""

    name: str
    description: str
    input_schema: dict[str, Any]  # already parsed from JSON
    output_schema: dict[str, Any] | None = None  # None when absent
    annotations: ToolAnnotationInfo = field(default_factory=ToolAnnotationInfo)


# ---------------------------------------------------------------------------
# Resource listing / reading
# ---------------------------------------------------------------------------


@dataclass
class Resource:
    """A resource exposed by the server."""

    uri: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class ResourceTemplate:
    """A URI-template resource exposed by the server."""

    uri_template: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class ReadResourceResult:
    """Parsed result from a ``read_resource`` request."""

    content: list[ContentItem]


# ---------------------------------------------------------------------------
# Prompt listing / retrieval
# ---------------------------------------------------------------------------


@dataclass
class PromptArgument:
    """An argument declared by a prompt."""

    name: str
    description: str = ""
    required: bool = False


@dataclass
class Prompt:
    """A prompt exposed by the server."""

    name: str
    description: str = ""
    arguments: list[PromptArgument] = field(default_factory=list)


@dataclass
class PromptMessage:
    """A single message in a rendered prompt."""

    role: str
    content: ContentItem


@dataclass
class GetPromptResult:
    """Parsed result from a ``get_prompt`` request."""

    messages: list[PromptMessage]


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


@dataclass
class CompleteResult:
    """Parsed result from a ``complete`` request."""

    values: list[str]
    has_more: bool = False
    total: int = 0


# ---------------------------------------------------------------------------
# Pagination wrapper (lives here so types.py is the single import for callers)
# ---------------------------------------------------------------------------


@dataclass
class ListResult:
    """Result from a paginated list method."""

    items: list
    next_cursor: str | None


@dataclass
class ServerInfo:
    """Server identity and capabilities received during initialization."""

    server_name: str
    server_version: str
    capabilities: Any  # mcp_pb2.ServerCapabilities — kept as proto to avoid circular import


# ---------------------------------------------------------------------------
# Proto → dataclass converters (private)
# ---------------------------------------------------------------------------


def _convert_content_item(p) -> ContentItem:
    return ContentItem(
        type=p.type,
        text=p.text,
        data=bytes(p.data),
        mime_type=p.mime_type,
        uri=p.uri,
    )


def _convert_call_tool_result(p) -> CallToolResult:
    return CallToolResult(
        content=[_convert_content_item(c) for c in p.content],
        is_error=p.is_error,
    )


def _convert_tool(p) -> Tool:
    input_schema: dict[str, Any] = json.loads(p.input_schema) if p.input_schema else {}
    output_schema: dict[str, Any] | None = json.loads(p.output_schema) if p.output_schema else None
    a = p.annotations
    annotations = ToolAnnotationInfo(
        title=a.title,
        read_only_hint=a.read_only_hint,
        destructive_hint=a.destructive_hint,
        idempotent_hint=a.idempotent_hint,
        open_world_hint=a.open_world_hint,
    )
    return Tool(
        name=p.name,
        description=p.description,
        input_schema=input_schema,
        output_schema=output_schema,
        annotations=annotations,
    )


def _convert_resource(p) -> Resource:
    return Resource(
        uri=p.uri,
        name=p.name,
        description=p.description,
        mime_type=p.mime_type,
    )


def _convert_resource_template(p) -> ResourceTemplate:
    return ResourceTemplate(
        uri_template=p.uri_template,
        name=p.name,
        description=p.description,
        mime_type=p.mime_type,
    )


def _convert_read_resource_result(p) -> ReadResourceResult:
    return ReadResourceResult(content=[_convert_content_item(c) for c in p.content])


def _convert_prompt_argument(p) -> PromptArgument:
    return PromptArgument(name=p.name, description=p.description, required=p.required)


def _convert_prompt(p) -> Prompt:
    return Prompt(
        name=p.name,
        description=p.description,
        arguments=[_convert_prompt_argument(a) for a in p.arguments],
    )


def _convert_get_prompt_result(p) -> GetPromptResult:
    return GetPromptResult(
        messages=[
            PromptMessage(role=m.role, content=_convert_content_item(m.content)) for m in p.messages
        ]
    )


def _convert_complete_result(p) -> CompleteResult:
    return CompleteResult(
        values=list(p.values),
        has_more=p.has_more,
        total=p.total,
    )
