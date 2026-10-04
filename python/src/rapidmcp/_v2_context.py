"""Context for a v2 tool call: everything goes onto the call's own stream."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp.context import Context
from rapidmcp.elicitation import ElicitationResult, build_elicitation_schema
from rapidmcp.errors import INTERNAL_ERROR, METHOD_NOT_FOUND, MISSING_CLIENT_CAPABILITY, McpError

# RFC 5424 severities, lowest first.
LOG_LEVELS = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")


class _NeedsInput(McpError):
    """Raised by ``ctx.elicit()`` to end the call with ``input_required``.

    An ``McpError`` so the tool manager lets it through instead of turning it
    into tool output; the v2 servicer catches it before it could become an error.
    """

    def __init__(self, requests: dict[str, pb.InputRequest]) -> None:
        super().__init__(INTERNAL_ERROR, "The tool needs input from the client")
        self.requests = requests


def _to_result(answer: dict[str, str]) -> ElicitationResult:
    data: dict = {}
    if answer.get("content"):
        try:
            parsed = json.loads(answer["content"])
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            pass
    return ElicitationResult(action=answer.get("action", ""), data=data)


class _V2Context(Context):
    """Same surface as the v1 ``Context``, stateless underneath.

    Progress and log messages are emitted only when the request asked for them
    in its ``meta``. Requests from the server to the client do not exist on v2.
    """

    def __init__(
        self,
        meta: pb.RequestMeta,
        emit: Callable[[pb.CallToolEvent], Awaitable[None]],
        answers: dict[str, dict[str, str]] | None = None,
        trace_context: dict[str, str] | None = None,
    ) -> None:
        self._meta = meta
        self._emit = emit
        self._answers = answers or {}
        self._elicit_calls = 0
        self.trace_context = dict(trace_context or {})

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

    @property
    def input_responses(self) -> dict[str, ElicitationResult]:
        """The answers this request carries, by key."""
        return {key: _to_result(answer) for key, answer in self._answers.items()}

    async def elicit(
        self,
        message: str,
        schema: str | None = None,
        fields: dict | None = None,
        timeout: float | None = None,
        key: str | None = None,
        url: str | None = None,
    ) -> ElicitationResult:
        """Ask the user a question.

        The first time, this ends the call: the client asks the user and calls
        the tool again, and the tool **runs again from the top**. On that run
        this returns the answer. So ask before doing anything with side effects.
        ``timeout`` is ignored; the client decides how long the user gets.
        """
        if url is not None and (schema is not None or fields is not None):
            raise ValueError("Provide 'url' or a form ('schema' / 'fields'), not both")
        if fields is not None and schema is not None:
            raise ValueError("Provide either 'schema' or 'fields', not both")

        mode = "url" if url is not None else "form"
        capabilities = self._meta.client_capabilities
        if not capabilities.HasField("elicitation") or not getattr(capabilities.elicitation, mode):
            raise McpError(
                MISSING_CLIENT_CAPABILITY,
                f"Client does not support {mode} elicitation",
                data={"required_capabilities": [f"elicitation.{mode}"]},
            )

        name = key or f"elicit-{self._elicit_calls}"
        self._elicit_calls += 1
        answer = self._answers.get(name)
        if answer is not None:
            return _to_result(answer)

        request = pb.ElicitRequest(message=message)
        if url is not None:
            request.url.CopyFrom(pb.ElicitUrl(url=url))
        else:
            resolved = schema or (build_elicitation_schema(fields) if fields else "")
            request.form.CopyFrom(pb.ElicitForm(requested_schema=resolved))
        raise _NeedsInput({name: pb.InputRequest(elicit=request)})
