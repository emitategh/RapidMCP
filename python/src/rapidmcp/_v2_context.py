"""Context for a v2 tool call: everything goes onto the call's own stream."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp.context import Context
from rapidmcp.errors import METHOD_NOT_FOUND, McpError

# RFC 5424 severities, lowest first.
LOG_LEVELS = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")


class _V2Context(Context):
    """Same surface as the v1 ``Context``, stateless underneath.

    Progress and log messages are emitted only when the request asked for them
    in its ``meta``. Requests from the server to the client do not exist on v2.
    """

    def __init__(
        self, meta: pb.RequestMeta, emit: Callable[[pb.CallToolEvent], Awaitable[None]]
    ) -> None:
        self._meta = meta
        self._emit = emit

    async def report_progress(
        self, progress: float, total: float | None = None, message: str = ""
    ) -> None:
        if not self._meta.HasField("progress_token"):
            return
        event = pb.Progress(token=self._meta.progress_token, progress=progress, message=message)
        if total is not None:
            event.total = total
        await self._emit(pb.CallToolEvent(progress=event))

    async def _log(self, level: str, message: str, extra: dict | None) -> None:
        if not self._meta.HasField("log_level"):
            return
        if LOG_LEVELS.index(level) < LOG_LEVELS.index(self._meta.log_level):
            return
        data = json.dumps({"message": message, "extra": extra})
        await self._emit(pb.CallToolEvent(log=pb.LogMessage(level=level, data=data)))

    async def sample(self, *args, **kwargs):
        raise McpError(
            METHOD_NOT_FOUND,
            "Sampling is not available on the v2 protocol; call your LLM provider directly",
        )

    async def list_roots(self, *args, **kwargs):
        raise McpError(
            METHOD_NOT_FOUND,
            "Roots are not available on the v2 protocol; take paths as tool arguments",
        )

    async def elicit(self, *args, **kwargs):
        raise McpError(METHOD_NOT_FOUND, "ctx.elicit() is not available on the v2 protocol yet")
