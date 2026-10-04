"""request_state: signed, bound to its request and caller, and short-lived."""

import pytest

from rapidmcp._v2_state import operation_digest, principal_digest, seal, unseal
from rapidmcp.errors import McpError

SECRET = b"shared-secret"
OP = operation_digest("tools/call", "deploy", '{"service": "api"}')
ANSWERS = {"elicit-0": {"action": "accept", "content": '{"confirm": true}'}}

# Produced once by hand with HMAC-SHA256("shared-secret", payload); the TypeScript
# suite carries the same bytes, which is what keeps the two implementations compatible.
VECTOR = bytes.fromhex(
    "c16779a64fde2278de3d869db2efc16ee1cdda58fab040d26f7d9e16552ae276"
    "7b2276223a312c22657870223a343130323434343830302c226f70223a2234616632363130643935373039"
    "623365303133343562616461656538323633363032626465306431623332373337303638633138656561"
    "313936646362613831222c22737562223a22222c22616e7377657273223a7b22656c696369742d30223a"
    "7b22616374696f6e223a22616363657074222c22636f6e74656e74223a227b5c22636f6e6669726d5c22"
    "3a20747275657d227d7d7d"
)


def _rejected(**kwargs) -> str:
    with pytest.raises(McpError) as exc:
        unseal(**kwargs)
    assert exc.value.code == -32602
    return exc.value.message


def test_operation_digest_is_the_documented_hash():
    assert OP == "4af2610d95709b3e01345badaee8263602bde0d1b32737068c18eea196dcba81"


def test_known_vector_opens():
    assert unseal(SECRET, VECTOR, OP, "") == ANSWERS


def test_sealed_state_round_trips():
    state = seal(SECRET, ANSWERS, OP, principal_digest("Bearer abc"))

    assert unseal(SECRET, state, OP, principal_digest("Bearer abc")) == ANSWERS


def test_altered_state_is_rejected():
    state = bytearray(seal(SECRET, ANSWERS, OP, ""))
    state[-5] ^= 1

    assert "altered" in _rejected(secret=SECRET, state=bytes(state), operation=OP, principal="")


def test_state_signed_with_another_secret_is_rejected():
    state = seal(b"other-secret", ANSWERS, OP, "")

    assert "altered" in _rejected(secret=SECRET, state=state, operation=OP, principal="")


def test_expired_state_is_rejected():
    state = seal(SECRET, ANSWERS, OP, "", now=1_000.0)

    assert "expired" in _rejected(
        secret=SECRET, state=state, operation=OP, principal="", now=1_601.0
    )
    assert unseal(SECRET, state, OP, "", now=1_599.0) == ANSWERS


def test_state_for_another_request_is_rejected():
    other = operation_digest("tools/call", "deploy", '{"service": "db"}')
    state = seal(SECRET, ANSWERS, OP, "")

    assert "different request" in _rejected(
        secret=SECRET, state=state, operation=other, principal=""
    )


def test_state_for_another_caller_is_rejected():
    state = seal(SECRET, ANSWERS, OP, principal_digest("Bearer alice"))

    assert "different caller" in _rejected(
        secret=SECRET, state=state, operation=OP, principal=principal_digest("Bearer bob")
    )


@pytest.mark.parametrize("junk", [b"", b"short", b"x" * 32, b"x" * 32 + b"not json"])
def test_garbage_is_rejected(junk):
    _rejected(secret=SECRET, state=junk, operation=OP, principal="")


def test_no_authorization_means_an_empty_principal():
    assert principal_digest(None) == ""
    assert principal_digest("") == ""
