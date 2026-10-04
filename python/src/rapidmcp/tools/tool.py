# python/src/rapidmcp/tools/tool.py
"""Tool domain objects and registration helpers."""

from __future__ import annotations

import enum
import inspect
import json
import types
import typing
from collections.abc import Awaitable, Callable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolAnnotations:
    """Behavioural hints for a tool, surfaced to MCP clients.

    All fields are optional; a hint left as ``None`` was not set by the author
    and is not asserted to clients. Clients use these to decide how to present or
    invoke the tool (e.g. warn the user before calling a destructive tool).
    """

    title: str = ""
    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    idempotent_hint: bool | None = None
    open_world_hint: bool | None = None


@dataclass
class RegisteredTool:
    name: str
    description: str
    input_schema: str
    handler: Callable[..., Awaitable[Any]]
    needs_context: bool = False
    output_schema: str = ""  # JSON schema string; empty = no structured output
    annotations: ToolAnnotations | None = None
    icons: list = field(default_factory=list)


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
    from rapidmcp.context import Context

    hints = _resolve_hints(fn)
    return any(v is Context for v in hints.values())


_PRIMITIVE_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    type(None): "null",
}
_ARRAY_ORIGINS = (list, tuple, set, frozenset, Sequence, Set)
_OBJECT_ORIGINS = (dict, Mapping)


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    """Map a parameter annotation to JSON Schema.

    Anything not recognised (``Any``, missing annotations, custom classes)
    yields ``{}`` — "no constraint" — rather than a wrong type.
    """
    if annotation in _PRIMITIVE_TYPES:
        return {"type": _PRIMITIVE_TYPES[annotation]}
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return {"enum": [member.value for member in annotation]}

    origin = typing.get_origin(annotation) or annotation
    args = typing.get_args(annotation)

    if origin is typing.Literal:
        return {"enum": list(args)}
    if origin is typing.Union or origin is types.UnionType:
        return {"anyOf": [_annotation_to_schema(a) for a in args]}
    if origin in _ARRAY_ORIGINS:
        schema: dict[str, Any] = {"type": "array"}
        item_args = [a for a in args if a is not Ellipsis]
        if len(item_args) == 1:
            schema["items"] = _annotation_to_schema(item_args[0])
        return schema
    if origin in _OBJECT_ORIGINS:
        schema = {"type": "object"}
        if len(args) == 2:
            schema["additionalProperties"] = _annotation_to_schema(args[1])
        return schema
    return {}


def _build_input_schema(fn: Callable) -> str:
    """Build a JSON Schema from function type hints."""
    from rapidmcp.context import Context

    hints = _resolve_hints(fn)
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue  # *args / **kwargs are not addressable as named arguments
        annotation = hints.get(param_name, param.annotation)
        if annotation is Context:
            continue  # skip DI parameters
        properties[param_name] = _annotation_to_schema(annotation)
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return json.dumps(schema)
