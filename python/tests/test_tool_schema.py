"""JSON Schema advertised for a tool's parameters."""

import enum
import json
from typing import Any, Literal, Optional

import pytest

from rapidmcp import Context, RapidMCP


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


def _schema(fn) -> dict:
    server = RapidMCP(name="schema", version="0.1")
    server.tool()(fn)
    return json.loads(server.list_registered_tools()[0].input_schema)


async def _list_of_str(v: list[str]) -> str: ...
async def _bare_list(v: list) -> str: ...
async def _dict_of_int(v: dict[str, int]) -> str: ...
async def _bare_dict(v: dict) -> str: ...
async def _optional_int(v: Optional[int]) -> str: ...  # noqa: UP045
async def _int_or_none(v: int | None) -> str: ...
async def _int_or_str(v: int | str) -> str: ...
async def _literal(v: Literal["asc", "desc"]) -> str: ...
async def _enum(v: Color) -> str: ...
async def _nested(v: list[dict[str, float]]) -> str: ...
async def _any(v: Any) -> str: ...
async def _unannotated(v) -> str: ...
async def _tuple(v: tuple[int, ...]) -> str: ...
async def _set(v: set[str]) -> str: ...


@pytest.mark.parametrize(
    ("fn", "want"),
    [
        (_list_of_str, {"type": "array", "items": {"type": "string"}}),
        (_bare_list, {"type": "array"}),
        (_dict_of_int, {"type": "object", "additionalProperties": {"type": "integer"}}),
        (_bare_dict, {"type": "object"}),
        (_optional_int, {"anyOf": [{"type": "integer"}, {"type": "null"}]}),
        (_int_or_none, {"anyOf": [{"type": "integer"}, {"type": "null"}]}),
        (_int_or_str, {"anyOf": [{"type": "integer"}, {"type": "string"}]}),
        (_literal, {"enum": ["asc", "desc"]}),
        (_enum, {"enum": ["red", "blue"]}),
        (
            _nested,
            {
                "type": "array",
                "items": {"type": "object", "additionalProperties": {"type": "number"}},
            },
        ),
        (_any, {}),
        (_unannotated, {}),
        (_tuple, {"type": "array", "items": {"type": "integer"}}),
        (_set, {"type": "array", "items": {"type": "string"}}),
    ],
)
def test_parameter_schema(fn, want):
    assert _schema(fn)["properties"]["v"] == want


def test_primitives_defaults_and_context_are_unchanged():
    async def tool(a: str, b: int, ctx: Context, c: float = 1.0, d: bool = False) -> str: ...

    assert _schema(tool) == {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
            "c": {"type": "number"},
            "d": {"type": "boolean"},
        },
        "required": ["a", "b"],
    }


def test_var_args_are_not_advertised_as_parameters():
    async def tool(a: str, *args: int, **kwargs: str) -> str: ...

    assert _schema(tool) == {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
