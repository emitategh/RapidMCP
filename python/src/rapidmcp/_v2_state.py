"""request_state — the server's memory of earlier answers, carried by the client.

v2 is stateless, so when a tool asks a second question the first answer has to
come back with the retry. It travels in ``request_state``, which passes through
the client and is therefore attacker-controlled: it is signed, bound to the
operation and the caller, and expires.

Wire format (the same in the TypeScript implementation):
32 bytes of HMAC-SHA256, then the UTF-8 JSON payload the MAC covers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from rapidmcp.errors import INVALID_PARAMS, McpError

STATE_TTL_SECONDS = 600
_MAC_BYTES = 32


def operation_digest(method: str, name: str, arguments_text: str) -> str:
    """Identify the request a state belongs to."""
    return hashlib.sha256(f"{method}\n{name}\n{arguments_text}".encode()).hexdigest()


def principal_digest(authorization: str | None) -> str:
    """Identify the caller a state belongs to; empty when the request carries no credentials."""
    return hashlib.sha256(authorization.encode()).hexdigest() if authorization else ""


def seal(
    secret: bytes,
    answers: dict[str, dict[str, str]],
    operation: str,
    principal: str,
    *,
    now: float | None = None,
    ttl: float = STATE_TTL_SECONDS,
) -> bytes:
    issued = time.time() if now is None else now
    payload = json.dumps(
        {"v": 1, "exp": int(issued + ttl), "op": operation, "sub": principal, "answers": answers},
        separators=(",", ":"),
    ).encode()
    return hmac.new(secret, payload, hashlib.sha256).digest() + payload


def _rejected(reason: str) -> McpError:
    return McpError(INVALID_PARAMS, f"Invalid request_state: {reason}")


def unseal(
    secret: bytes,
    state: bytes,
    operation: str,
    principal: str,
    *,
    now: float | None = None,
) -> dict[str, dict[str, str]]:
    """The answers inside *state*, or ``McpError(-32602)`` if it cannot be trusted."""
    mac, payload = state[:_MAC_BYTES], state[_MAC_BYTES:]
    expected = hmac.new(secret, payload, hashlib.sha256).digest()
    if len(mac) != _MAC_BYTES or not hmac.compare_digest(mac, expected):
        raise _rejected("it was not issued by this server, or has been altered")
    try:
        data = json.loads(payload)
        expires, op, sub, answers = data["exp"], data["op"], data["sub"], data["answers"]
    except (ValueError, KeyError, TypeError):
        raise _rejected("it is malformed") from None
    if expires < (time.time() if now is None else now):
        raise _rejected("it has expired")
    if op != operation:
        raise _rejected("it belongs to a different request")
    if sub != principal:
        raise _rejected("it belongs to a different caller")
    return answers
