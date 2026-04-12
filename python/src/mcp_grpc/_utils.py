# python/src/mcp_grpc/_utils.py
"""Shared low-level helpers used across multiple modules."""

from __future__ import annotations

import json
from typing import Any

from mcp_grpc._generated import mcp_pb2
from mcp_grpc.content import Audio, Image


def _to_content_items(result: Any) -> list[mcp_pb2.ContentItem]:
    """Convert a tool return value to a list of ContentItem protos.

    Supported types:
    * ``None``  → empty list (no content)
    * ``str``   → text content
    * ``Image`` → image content (base64-encoded bytes + mime_type)
    * ``Audio`` → audio content (base64-encoded bytes + mime_type)
    * ``dict``  → JSON-serialised text content (structured output)
    * ``list``  → each element converted recursively (flattened one level)
    * anything else → ``str()`` representation as text
    """
    if result is None:
        return []
    if isinstance(result, list):
        items: list[mcp_pb2.ContentItem] = []
        for elem in result:
            items.extend(_to_content_items(elem))
        return items
    if isinstance(result, str):
        return [mcp_pb2.ContentItem(type="text", text=result)]
    if isinstance(result, Image):
        return [mcp_pb2.ContentItem(type="image", data=result.data, mime_type=result.mime_type)]
    if isinstance(result, Audio):
        return [mcp_pb2.ContentItem(type="audio", data=result.data, mime_type=result.mime_type)]
    if isinstance(result, dict):
        return [mcp_pb2.ContentItem(type="text", text=json.dumps(result))]
    return [mcp_pb2.ContentItem(type="text", text=str(result))]


def _paginate(items: list, cursor_str: str, page_size: int | None) -> tuple[list, str]:
    """Slice *items* according to *page_size* and *cursor_str*.

    *cursor_str* is an opaque decimal integer offset (empty = 0).
    Returns ``(page, next_cursor_str)`` where *next_cursor_str* is empty
    when there are no further pages.

    Invalid cursors (non-numeric, negative) are treated as offset 0.
    """
    if page_size is None:
        return items, ""
    try:
        offset = int(cursor_str) if cursor_str else 0
    except (ValueError, TypeError):
        offset = 0
    if offset < 0:
        offset = 0
    page = items[offset : offset + page_size]
    next_offset = offset + page_size
    next_cursor = str(next_offset) if next_offset < len(items) else ""
    return page, next_cursor


def _prefix_resource_uri(uri: str, prefix: str) -> str:
    """Insert *prefix* as the first path segment after the scheme.

    "res://greeting"   -> "res://users/greeting"
    "res://items/{id}" -> "res://users/items/{id}"
    "plain/path"       -> "users/plain/path"  (no scheme)
    """
    if "://" not in uri:
        return f"{prefix}/{uri}"
    scheme, rest = uri.split("://", 1)
    return f"{scheme}://{prefix}/{rest}"
