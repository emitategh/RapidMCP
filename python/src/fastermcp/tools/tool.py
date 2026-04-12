# python/src/fastermcp/tools/tool.py
"""Tool domain objects and registration helpers."""

from __future__ import annotations

import inspect
import json
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolAnnotations:
    """Behavioural hints for a tool, surfaced to MCP clients.

    All fields are optional. Clients use these to decide how to present or
    invoke the tool (e.g. warn the user before calling a destructive tool).
    """

    title: str = ""
    read_only_hint: bool = False
    destructive_hint: bool = False
    idempotent_hint: bool = False
    open_world_hint: bool = False


@dataclass
class RegisteredTool:
    name: str
    description: str
    input_schema: str
    handler: Callable[..., Awaitable[Any]]
    needs_context: bool = False
    output_schema: str = ""  # JSON schema string; empty = no structured output
    annotations: ToolAnnotations | None = None


def _resolve_hints(fn: Callable) -> dict[str, Any]:
    """Resolve type hints for *fn*, handling ``from __future__ import annotations``.

    Returns the mapping from ``typing.get_type_hints`` when possible.
    Falls back to raw ``inspect.signature`` annotations so that
    un-importable forward references don't crash registration.
    """
    try:
        return typing.get_type_hints(fn)
    except Exception:
        return {
            name: p.annotation
            for name, p in inspect.signature(fn).parameters.items()
            if p.annotation is not inspect.Parameter.empty
        }


def _needs_context(fn: Callable) -> bool:
    """Return True if *fn* declares a ``ctx: Context`` parameter."""
    from fastermcp.context import Context

    hints = _resolve_hints(fn)
    return any(v is Context for v in hints.values())


def _build_input_schema(fn: Callable) -> str:
    """Build a JSON Schema from function type hints."""
    from fastermcp.context import Context

    hints = _resolve_hints(fn)
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}

    for param_name, param in sig.parameters.items():
        annotation = hints.get(param_name, param.annotation)
        if annotation is Context:
            continue  # skip DI parameters
        json_type = type_map.get(annotation, "string")
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return json.dumps(schema)
