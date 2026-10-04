"""Icons: visual identifiers for tools, resources, prompts and the server."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Icon:
    """An icon a client may show next to an item.

    ``src`` is an ``https:`` URL or a ``data:`` URI. ``sizes`` are strings such
    as ``"48x48"`` or ``"any"``; ``theme`` is ``"light"``, ``"dark"`` or empty.
    """

    src: str
    mime_type: str = ""
    sizes: tuple[str, ...] = ()
    theme: str = ""


def _checked_icons(icons: Iterable[Icon] | None) -> list[Icon]:
    """*icons* as a list, refusing sources a client must not be asked to load."""
    result = list(icons or [])
    for icon in result:
        if not icon.src.lower().startswith(("https://", "data:")):
            raise ValueError(f"Icon src must be an https: or data: URI, got {icon.src!r}")
    return result
