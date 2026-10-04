"""Prompt and completion domain objects."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RegisteredPrompt:
    name: str
    description: str
    arguments: list[dict[str, Any]]
    handler: Callable[..., Awaitable[Any]]
    icons: list = field(default_factory=list)


@dataclass
class RegisteredCompletion:
    ref_name: str
    handler: Callable[..., Awaitable[list[str]]]
