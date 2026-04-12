"""Resource domain objects."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class RegisteredResource:
    uri: str
    name: str
    description: str
    mime_type: str
    handler: Callable[..., Awaitable[Any]]


@dataclass
class RegisteredResourceTemplate:
    uri_template: str
    name: str
    description: str
    mime_type: str
    handler: Callable[..., Awaitable[Any]]
