"""MCP errors survive a trip through a gRPC status and its trailing metadata."""

import grpc
import pytest

from rapidmcp._v2_errors import error_from_rpc, status_for, trailers_for
from rapidmcp.errors import McpError


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (-32602, grpc.StatusCode.INVALID_ARGUMENT),
        (-32601, grpc.StatusCode.UNIMPLEMENTED),
        (-32603, grpc.StatusCode.INTERNAL),
        (-32021, grpc.StatusCode.FAILED_PRECONDITION),
        (-32022, grpc.StatusCode.FAILED_PRECONDITION),
        (1234, grpc.StatusCode.UNKNOWN),
    ],
)
def test_status_for(code, status):
    assert status_for(code) is status


def test_error_round_trips_with_its_exact_code_and_data():
    sent = McpError(
        -32022,
        "Unsupported protocol version",
        data={"supported": ["2026-07-28"], "requested": "1900-01-01"},
    )

    received = error_from_rpc(status_for(sent.code), sent.message, trailers_for(sent))

    assert (received.code, received.message) == (-32022, "Unsupported protocol version")
    assert received.data == {"supported": ["2026-07-28"], "requested": "1900-01-01"}


def test_error_without_data_round_trips_with_no_data():
    received = error_from_rpc(
        grpc.StatusCode.INVALID_ARGUMENT, "bad", trailers_for(McpError(-32602, "bad"))
    )

    assert (received.code, received.data) == (-32602, None)


def test_transport_failures_become_local_client_errors():
    timeout = error_from_rpc(grpc.StatusCode.DEADLINE_EXCEEDED, "Deadline Exceeded", ())
    down = error_from_rpc(grpc.StatusCode.UNAVAILABLE, "failed to connect", ())

    assert (timeout.code, down.code) == (408, 503)


def test_other_grpc_failures_are_not_mcp_errors():
    assert error_from_rpc(grpc.StatusCode.UNAUTHENTICATED, "Invalid token", ()) is None
    assert error_from_rpc(grpc.StatusCode.UNIMPLEMENTED, "Method not found!", None) is None
