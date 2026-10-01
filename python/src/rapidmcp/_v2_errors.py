"""Carry MCP errors over gRPC: a status tooling understands, plus the exact
MCP code (and any structured data) in trailing metadata."""

from __future__ import annotations

from typing import NoReturn

import grpc

from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    MISSING_CLIENT_CAPABILITY,
    NOT_CONNECTED,
    REQUEST_TIMEOUT,
    UNSUPPORTED_PROTOCOL_VERSION,
    McpError,
)

CODE_KEY = "mcp-error-code"
DATA_KEY = "mcp-error-data-bin"

_STATUS = {
    INVALID_PARAMS: grpc.StatusCode.INVALID_ARGUMENT,
    METHOD_NOT_FOUND: grpc.StatusCode.UNIMPLEMENTED,
    INTERNAL_ERROR: grpc.StatusCode.INTERNAL,
    MISSING_CLIENT_CAPABILITY: grpc.StatusCode.FAILED_PRECONDITION,
    UNSUPPORTED_PROTOCOL_VERSION: grpc.StatusCode.FAILED_PRECONDITION,
}


def status_for(code: int) -> grpc.StatusCode:
    return _STATUS.get(code, grpc.StatusCode.UNKNOWN)


def trailers_for(error: McpError) -> tuple[tuple[str, str | bytes], ...]:
    trailers: list[tuple[str, str | bytes]] = [(CODE_KEY, str(error.code))]
    if error.data:
        data = pb.ErrorData(
            supported_versions=error.data.get("supported", []),
            requested_version=error.data.get("requested", ""),
            required_capabilities=error.data.get("required_capabilities", []),
        )
        trailers.append((DATA_KEY, data.SerializeToString()))
    return tuple(trailers)


async def abort(context, error: McpError) -> NoReturn:
    """End the RPC with *error*. Never returns: ``context.abort`` raises."""
    context.set_trailing_metadata(trailers_for(error))
    await context.abort(status_for(error.code), error.message)
    raise AssertionError("context.abort returned")  # pragma: no cover


def _data_from(raw: bytes) -> dict | None:
    parsed = pb.ErrorData.FromString(raw)
    data: dict = {}
    if parsed.supported_versions:
        data["supported"] = list(parsed.supported_versions)
    if parsed.requested_version:
        data["requested"] = parsed.requested_version
    if parsed.required_capabilities:
        data["required_capabilities"] = list(parsed.required_capabilities)
    return data or None


def error_from_rpc(status: grpc.StatusCode, details: str | None, trailers) -> McpError | None:
    """The McpError a failed RPC stands for, or None when it is not one.

    None means "re-raise the gRPC error as it is" — an auth failure, or an
    UNIMPLEMENTED from a server that does not serve v2 at all.
    """
    found = dict(trailers or ())
    if CODE_KEY in found:
        raw = found.get(DATA_KEY)
        return McpError(int(found[CODE_KEY]), details or "", data=_data_from(raw) if raw else None)
    if status is grpc.StatusCode.DEADLINE_EXCEEDED:
        return McpError(REQUEST_TIMEOUT, details or "Request timed out")
    if status is grpc.StatusCode.UNAVAILABLE:
        return McpError(NOT_CONNECTED, details or "Server unavailable")
    return None
