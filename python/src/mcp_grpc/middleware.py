"""Middleware system for FasterMCP tool call interception."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcp_grpc._generated import mcp_pb2

if TYPE_CHECKING:
    # Context is imported only for type-checking to avoid a circular import:
    # server.py imports Middleware; middleware.py needs Context for ToolCallContext.
    # With `from __future__ import annotations`, all annotations are strings at
    # runtime so Python never resolves this import during normal execution.
    from mcp_grpc.server import Context

# Type alias for the next handler in the chain.
CallNext = Callable[["ToolCallContext"], Awaitable[mcp_pb2.CallToolResponse]]


@dataclass
class ToolCallContext:
    """Passed to every middleware on each tool invocation.

    ctx is None when the tool handler did not declare `ctx: Context` in its
    signature. FasterMCP constructs Context explicitly per-call (not via a
    ContextVar), so middleware only receives it when the tool opted in.

    input_schema is the parsed JSON Schema dict for the tool (the same object
    stored in RegisteredTool.input_schema, pre-parsed for convenience). It is
    None only when the server could not locate the tool definition, which in
    practice means ValidationMiddleware would let the call through.
    """

    tool_name: str
    arguments: dict[str, Any]
    ctx: Context | None
    input_schema: dict[str, Any] | None = None


class Middleware:
    """Base class for FasterMCP middleware.

    Override on_tool_call to intercept tool invocations.
    The default passes through to the next handler unchanged.
    """

    async def on_tool_call(
        self,
        tool_ctx: ToolCallContext,
        call_next: CallNext,
    ) -> mcp_pb2.CallToolResponse:
        return await call_next(tool_ctx)


class TimingMiddleware(Middleware):
    """Logs elapsed wall-clock time for every tool call.

    Default logger: ``mcp_grpc.timing`` at INFO level.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        log_level: int = logging.INFO,
    ) -> None:
        self._logger = logger or logging.getLogger("mcp_grpc.timing")
        self._log_level = log_level

    async def on_tool_call(
        self,
        tool_ctx: ToolCallContext,
        call_next: CallNext,
    ) -> mcp_pb2.CallToolResponse:
        start = time.perf_counter()
        result = await call_next(tool_ctx)
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._logger.log(
            self._log_level,
            "%s completed in %.2fms",
            tool_ctx.tool_name,
            elapsed_ms,
        )
        return result


class LoggingMiddleware(Middleware):
    """Logs tool name + arguments before, and is_error status after, every call.

    Default logger: ``mcp_grpc.requests`` at INFO level.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        log_level: int = logging.INFO,
    ) -> None:
        self._logger = logger or logging.getLogger("mcp_grpc.requests")
        self._log_level = log_level

    async def on_tool_call(
        self,
        tool_ctx: ToolCallContext,
        call_next: CallNext,
    ) -> mcp_pb2.CallToolResponse:
        self._logger.log(
            self._log_level,
            "tool=%s args=%r",
            tool_ctx.tool_name,
            tool_ctx.arguments,
        )
        result = await call_next(tool_ctx)
        self._logger.log(
            self._log_level,
            "tool=%s is_error=%s",
            tool_ctx.tool_name,
            result.is_error,
        )
        return result


class TimeoutMiddleware(Middleware):
    """Enforces a wall-clock deadline on every tool call.

    If a tool does not complete within the allotted time it is cancelled and
    an error response is returned — the gRPC stream stays alive.

    Args:
        default_timeout: Seconds to allow for any tool not listed in
            *per_tool*. Defaults to 30.0.
        per_tool: Optional mapping of tool name → timeout in seconds that
            overrides *default_timeout* for specific tools.

    Example::

        TimeoutMiddleware(default_timeout=10.0, per_tool={"slow_export": 120.0})
    """

    def __init__(
        self,
        default_timeout: float = 30.0,
        per_tool: dict[str, float] | None = None,
    ) -> None:
        self._default = default_timeout
        self._per_tool: dict[str, float] = per_tool or {}

    async def on_tool_call(
        self,
        tool_ctx: ToolCallContext,
        call_next: CallNext,
    ) -> mcp_pb2.CallToolResponse:
        timeout = self._per_tool.get(tool_ctx.tool_name, self._default)
        try:
            return await asyncio.wait_for(call_next(tool_ctx), timeout=timeout)
        except asyncio.TimeoutError:
            return mcp_pb2.CallToolResponse(
                content=[
                    mcp_pb2.ContentItem(
                        type="text",
                        text=f"Tool '{tool_ctx.tool_name}' timed out after {timeout}s",
                    )
                ],
                is_error=True,
            )


class ValidationMiddleware(Middleware):
    """Validates tool arguments against the tool's JSON Schema before dispatch.

    Uses only the stdlib — no jsonschema dependency required. Checks:

    * All fields listed in ``required`` are present in the arguments.
    * No argument keys outside ``properties`` are passed (unknown fields).

    If the tool's schema is unavailable (``tool_ctx.input_schema`` is None)
    the call is forwarded unchanged — validation is best-effort.

    Returns an error response (``is_error=True``) without calling the tool
    when validation fails, so the handler never sees invalid input.
    """

    async def on_tool_call(
        self,
        tool_ctx: ToolCallContext,
        call_next: CallNext,
    ) -> mcp_pb2.CallToolResponse:
        schema = tool_ctx.input_schema
        if schema is None:
            return await call_next(tool_ctx)

        properties: dict[str, Any] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])
        args = tool_ctx.arguments

        # Check required fields
        missing = [f for f in required if f not in args]
        if missing:
            return mcp_pb2.CallToolResponse(
                content=[
                    mcp_pb2.ContentItem(
                        type="text",
                        text=(
                            f"Tool '{tool_ctx.tool_name}' missing required "
                            f"argument(s): {', '.join(missing)}"
                        ),
                    )
                ],
                is_error=True,
            )

        # Check for unknown fields
        if properties:
            unknown = [k for k in args if k not in properties]
            if unknown:
                return mcp_pb2.CallToolResponse(
                    content=[
                        mcp_pb2.ContentItem(
                            type="text",
                            text=(
                                f"Tool '{tool_ctx.tool_name}' received unknown "
                                f"argument(s): {', '.join(unknown)}"
                            ),
                        )
                    ],
                    is_error=True,
                )

        return await call_next(tool_ctx)
