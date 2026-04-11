"""Elicitation schema builders and result type for FasterMCP."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Field descriptors
# ---------------------------------------------------------------------------


@dataclass
class StringField:
    """A required/optional string form field."""

    title: str = ""
    description: str = ""
    required: bool = True
    default: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None

    def _to_property(self) -> dict[str, Any]:
        prop: dict[str, Any] = {"type": "string"}
        if self.title:
            prop["title"] = self.title
        if self.description:
            prop["description"] = self.description
        if self.default is not None:
            prop["default"] = self.default
        if self.min_length is not None:
            prop["minLength"] = self.min_length
        if self.max_length is not None:
            prop["maxLength"] = self.max_length
        if self.pattern is not None:
            prop["pattern"] = self.pattern
        return prop


@dataclass
class BoolField:
    """A required/optional boolean form field."""

    title: str = ""
    description: str = ""
    required: bool = True
    default: bool | None = None

    def _to_property(self) -> dict[str, Any]:
        prop: dict[str, Any] = {"type": "boolean"}
        if self.title:
            prop["title"] = self.title
        if self.description:
            prop["description"] = self.description
        if self.default is not None:
            prop["default"] = self.default
        return prop


@dataclass
class IntField:
    """A required/optional integer form field."""

    title: str = ""
    description: str = ""
    required: bool = True
    default: int | None = None
    minimum: int | None = None
    maximum: int | None = None

    def _to_property(self) -> dict[str, Any]:
        prop: dict[str, Any] = {"type": "integer"}
        if self.title:
            prop["title"] = self.title
        if self.description:
            prop["description"] = self.description
        if self.default is not None:
            prop["default"] = self.default
        if self.minimum is not None:
            prop["minimum"] = self.minimum
        if self.maximum is not None:
            prop["maximum"] = self.maximum
        return prop


@dataclass
class FloatField:
    """A required/optional number form field."""

    title: str = ""
    description: str = ""
    required: bool = True
    default: float | None = None
    minimum: float | None = None
    maximum: float | None = None

    def _to_property(self) -> dict[str, Any]:
        prop: dict[str, Any] = {"type": "number"}
        if self.title:
            prop["title"] = self.title
        if self.description:
            prop["description"] = self.description
        if self.default is not None:
            prop["default"] = self.default
        if self.minimum is not None:
            prop["minimum"] = self.minimum
        if self.maximum is not None:
            prop["maximum"] = self.maximum
        return prop


@dataclass
class EnumField:
    """A required/optional single-choice enum form field."""

    choices: list[str]
    title: str = ""
    description: str = ""
    required: bool = True
    default: str | None = None

    def _to_property(self) -> dict[str, Any]:
        prop: dict[str, Any] = {"type": "string", "enum": list(self.choices)}
        if self.title:
            prop["title"] = self.title
        if self.description:
            prop["description"] = self.description
        if self.default is not None:
            prop["default"] = self.default
        return prop


# Union type for all field descriptors
ElicitationField = StringField | BoolField | IntField | FloatField | EnumField


# ---------------------------------------------------------------------------
# Schema builder
# ---------------------------------------------------------------------------


def build_elicitation_schema(fields: dict[str, ElicitationField]) -> str:
    """Convert field descriptors to a JSON Schema string.

    MCP elicitation schemas must be flat objects with primitive-typed properties.
    All field names become properties; fields with ``required=True`` are listed
    in the ``required`` array.

    Returns the schema serialised as a JSON string, ready to pass to
    ``ctx.elicit(schema=...)``.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, f in fields.items():
        properties[name] = f._to_property()
        if f.required:
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required

    return json.dumps(schema)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ElicitationResult:
    """Result returned by ``ctx.elicit()``.

    Attributes:
        action: One of ``"accept"``, ``"decline"``, or ``"cancel"``.
        data:   Filled form values as a dict.  Empty when *action* is not
                ``"accept"`` or when the client returned no structured content.
    """

    action: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        """True when the user accepted the elicitation."""
        return self.action == "accept"

    @property
    def declined(self) -> bool:
        """True when the user explicitly declined."""
        return self.action == "decline"

    @property
    def cancelled(self) -> bool:
        """True when the user cancelled without a choice."""
        return self.action == "cancel"
