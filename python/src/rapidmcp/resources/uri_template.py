"""URI template matching (RFC 6570 subset).

Ported from FastMCP's ``fastmcp/resources/template.py``.  Uses only stdlib
(``re`` + ``urllib.parse``).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote


def _extract_query_params(uri_template: str) -> set[str]:
    """Extract query parameter names from RFC 6570 ``{?param1,param2}`` syntax."""
    match = re.search(r"\{\?([^}]+)\}", uri_template)
    if match:
        return {p.strip() for p in match.group(1).split(",")}
    return set()


def _build_regex(template: str) -> re.Pattern[str] | None:
    """Build a regex pattern for *template*.

    Supports:
    - ``{var}``  — single path segment (``[^/]+``)
    - ``{var*}`` — wildcard, multiple segments (``.+``)
    - ``{?v1,v2}`` — query params (stripped before path matching)

    Returns ``None`` if the template produces an invalid regex.
    """
    # Remove query parameter syntax for path matching
    clean = re.sub(r"\{\?[^}]+\}", "", template)

    parts = re.split(r"(\{[^}]+\})", clean)
    pattern = ""
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            name = part[1:-1]
            if name.endswith("*"):
                name = name[:-1]
                pattern += f"(?P<{name}>.+)"
            else:
                pattern += f"(?P<{name}>[^/]+)"
        else:
            pattern += re.escape(part)
    try:
        return re.compile(f"^{pattern}$")
    except re.error:
        return None


def _is_safe_value(value: str, *, single_segment: bool) -> bool:
    """Reject values that would let a URI reach outside what the template names.

    ``{var}`` promises one path segment, so a decoded separator (``%2F``,
    ``%5C``) must not turn it into several. No variable, wildcard included,
    may contain a ``.`` / ``..`` segment or a NUL byte.
    """
    if "\x00" in value:
        return False
    segments = re.split(r"[/\\]", value)
    if single_segment and len(segments) > 1:
        return False
    return not any(segment in (".", "..") for segment in segments)


def match_uri_template(uri: str, uri_template: str) -> dict[str, str] | None:
    """Match *uri* against *uri_template* and extract parameters.

    Returns a ``dict`` of extracted path and query parameters on success,
    or ``None`` when the URI does not match the template.

    Examples::

        match_uri_template("res://items/42", "res://items/{id}")
        # -> {"id": "42"}

        match_uri_template("res://files/a/b.txt", "res://files/{path*}")
        # -> {"path": "a/b.txt"}
    """
    # Split URI into path and query parts
    uri_path, _, query_string = uri.partition("?")

    regex = _build_regex(uri_template)
    if regex is None:
        return None
    match = regex.match(uri_path)
    if not match:
        return None

    wildcards = set(re.findall(r"\{([^{}?]+)\*\}", uri_template))
    params: dict[str, str] = {}
    for name, raw in match.groupdict().items():
        value = unquote(raw)
        if not _is_safe_value(value, single_segment=name not in wildcards):
            return None
        params[name] = value

    # Merge query parameters declared in the template
    if query_string:
        query_param_names = _extract_query_params(uri_template)
        parsed_query = parse_qs(query_string)
        for name in query_param_names:
            if name in parsed_query:
                params[name] = parsed_query[name][0]

    return params
