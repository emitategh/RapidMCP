"""Client transport for the v2 protocol: one stateless RPC per operation."""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Callable

import grpc
from grpc import aio

from rapidmcp._generated import mcp_pb2, mcp_v2_pb2_grpc
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._v2_errors import error_from_rpc
from rapidmcp._version import __version__
from rapidmcp.errors import INPUT_LOOP, INTERNAL_ERROR, MISSING_CLIENT_CAPABILITY, McpError
from rapidmcp.session import NotificationRegistry
from rapidmcp.types import (
    CallToolResult,
    CompleteResult,
    ElicitRequestInfo,
    GetPromptResult,
    ListResult,
    ReadResourceResult,
    ServerInfo,
    _convert_call_tool_result_v2,
    _convert_complete_result,
    _convert_get_prompt_result,
    _convert_prompt,
    _convert_read_resource_result,
    _convert_resource,
    _convert_resource_template,
    _convert_tool_v2,
)

logger = logging.getLogger("rapidmcp.client")

PROTOCOL_VERSION = "2026-07-28"
MAX_INPUT_ROUNDS = 10


class _V2Transport:
    def __init__(
        self,
        channel: aio.Channel,
        metadata: list[tuple[str, str]],
        timeout: float,
        elicitation: Callable[[], tuple[Callable | None, bool]],
        notifications: NotificationRegistry,
    ) -> None:
        self._stub = mcp_v2_pb2_grpc.McpStub(channel)
        self._metadata = metadata
        self._timeout = timeout
        # () -> (handler or None, whether it also handles URL mode)
        self._elicitation = elicitation
        self._notifications = notifications
        self._progress_tokens = itertools.count(1)

    def _meta(self, *, events: bool = False) -> pb.RequestMeta:
        capabilities = pb.ClientCapabilities()
        handler, handles_url = self._elicitation()
        if handler is not None:
            capabilities.elicitation.CopyFrom(pb.ElicitationCapability(form=True, url=handles_url))
        meta = pb.RequestMeta(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=capabilities,
            client_info=pb.Implementation(name="rapidmcp-python", version=__version__),
        )
        if events and self._notifications.has("progress"):
            meta.progress_token = f"p{next(self._progress_tokens)}"
        if events and self._notifications.has("log"):
            meta.log_level = "debug"
        return meta

    async def _call(self, method, request):
        try:
            return await method(request, metadata=self._metadata, timeout=self._timeout)
        except aio.AioRpcError as exc:
            error = error_from_rpc(exc.code(), exc.details(), exc.trailing_metadata())
            if error is None:
                raise
            raise error from None

    async def discover(self) -> ServerInfo:
        result = await self._call(self._stub.Discover, pb.DiscoverRequest(meta=self._meta()))
        caps = result.capabilities
        return ServerInfo(
            server_name=result.meta.server_info.name,
            server_version=result.meta.server_info.version,
            # Same shape as the v1 capabilities, so callers need not care which era this is.
            capabilities=mcp_pb2.ServerCapabilities(
                tools=caps.HasField("tools"),
                tools_list_changed=caps.tools.list_changed,
                resources=caps.HasField("resources"),
                prompts=caps.HasField("prompts"),
            ),
        )

    async def list_tools(self, cursor: str | None) -> ListResult:
        result = await self._call(
            self._stub.ListTools, pb.ListToolsRequest(meta=self._meta(), cursor=cursor or "")
        )
        return ListResult(
            items=[_convert_tool_v2(t) for t in result.tools],
            next_cursor=result.next_cursor or None,
        )

    async def list_resources(self, cursor: str | None) -> ListResult:
        result = await self._call(
            self._stub.ListResources,
            pb.ListResourcesRequest(meta=self._meta(), cursor=cursor or ""),
        )
        return ListResult(
            items=[_convert_resource(r) for r in result.resources],
            next_cursor=result.next_cursor or None,
        )

    async def list_resource_templates(self, cursor: str | None) -> ListResult:
        result = await self._call(
            self._stub.ListResourceTemplates,
            pb.ListResourceTemplatesRequest(meta=self._meta(), cursor=cursor or ""),
        )
        return ListResult(
            items=[_convert_resource_template(t) for t in result.templates],
            next_cursor=result.next_cursor or None,
        )

    async def list_prompts(self, cursor: str | None) -> ListResult:
        result = await self._call(
            self._stub.ListPrompts, pb.ListPromptsRequest(meta=self._meta(), cursor=cursor or "")
        )
        return ListResult(
            items=[_convert_prompt(p) for p in result.prompts],
            next_cursor=result.next_cursor or None,
        )

    async def complete(
        self, ref_type: str, ref_name: str, argument_name: str, value: str
    ) -> CompleteResult:
        result = await self._call(
            self._stub.Complete,
            pb.CompleteRequest(
                meta=self._meta(),
                ref=pb.CompletionRef(type=ref_type, name=ref_name),
                argument=pb.CompletionArg(name=argument_name, value=value),
            ),
        )
        return _convert_complete_result(result)

    async def _notify(self, kind: str, payload: dict) -> None:
        try:
            await self._notifications.dispatch(kind, json.dumps(payload))
        except Exception:
            logger.exception("Notification handler for '%s' raised", kind)

    async def _stream(self, method, request, timeout: float | None = None):
        """Run a streaming RPC to its terminal event, feeding progress and log handlers."""
        call = method(request, metadata=self._metadata, timeout=timeout or self._timeout)
        try:
            async for event in call:
                kind = event.WhichOneof("event")
                if kind == "progress":
                    p = event.progress
                    await self._notify(
                        "progress",
                        {
                            "progress": p.progress,
                            "total": p.total if p.HasField("total") else None,
                            "message": p.message,
                            "token": p.token,
                        },
                    )
                elif kind == "log":
                    data = json.loads(event.log.data) if event.log.data else {}
                    await self._notify(
                        "log",
                        {
                            "level": event.log.level,
                            "message": data.get("message"),
                            "extra": data.get("extra"),
                        },
                    )
                elif kind == "complete":
                    return kind, event.complete
                elif kind == "input_required":
                    return kind, event.input_required
            raise McpError(INTERNAL_ERROR, "The server ended the call without a result")
        except aio.AioRpcError as exc:
            error = error_from_rpc(exc.code(), exc.details(), exc.trailing_metadata())
            if error is None:
                raise
            raise error from None
        finally:
            # A no-op once the call has finished; stops the server when we leave early
            # (the awaiting task was cancelled, or a handler error escaped).
            call.cancel()

    async def _answer(self, request: pb.InputRequest) -> pb.InputResponse:
        handler, _ = self._elicitation()
        if request.WhichOneof("request") != "elicit" or handler is None:
            raise McpError(
                MISSING_CLIENT_CAPABILITY, "The server asked for input this client cannot give"
            )
        asked = request.elicit
        mode = asked.WhichOneof("mode") or "form"
        reply = await handler(
            ElicitRequestInfo(
                message=asked.message,
                schema=asked.form.requested_schema if mode == "form" else "",
                mode=mode,
                url=asked.url.url if mode == "url" else "",
            )
        )
        return pb.InputResponse(
            elicit=pb.ElicitResult(action=reply.action, content=getattr(reply, "content", "") or "")
        )

    async def _run(self, method, build_request, timeout: float | None = None):
        """Call until the server has what it needs, answering its questions in between."""
        responses: dict[str, pb.InputResponse] = {}
        state = b""
        for round_number in range(MAX_INPUT_ROUNDS + 1):
            kind, message = await self._stream(method, build_request(responses, state), timeout)
            if kind == "complete":
                return message
            if round_number == MAX_INPUT_ROUNDS:
                break
            responses = {
                key: await self._answer(asked) for key, asked in message.input_requests.items()
            }
            state = message.request_state
        raise McpError(INPUT_LOOP, f"The server asked for input more than {MAX_INPUT_ROUNDS} times")

    async def call_tool(
        self, name: str, arguments: dict | None, timeout: float | None
    ) -> CallToolResult:
        text = json.dumps(arguments or {})  # the same text every round: the state is bound to it

        def build(responses, state):
            return pb.CallToolRequest(
                meta=self._meta(events=True),
                name=name,
                arguments=text,
                input_responses=responses,
                request_state=state,
            )

        return _convert_call_tool_result_v2(await self._run(self._stub.CallTool, build, timeout))

    async def read_resource(self, uri: str) -> ReadResourceResult:
        def build(responses, state):
            return pb.ReadResourceRequest(
                meta=self._meta(), uri=uri, input_responses=responses, request_state=state
            )

        return _convert_read_resource_result(await self._run(self._stub.ReadResource, build))

    async def get_prompt(self, name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        def build(responses, state):
            return pb.GetPromptRequest(
                meta=self._meta(),
                name=name,
                arguments=arguments or {},
                input_responses=responses,
                request_state=state,
            )

        return _convert_get_prompt_result(await self._run(self._stub.GetPrompt, build))


def is_v2_missing(exc: BaseException) -> bool:
    """True when a failed Discover means "this server does not serve v2"."""
    return isinstance(exc, aio.AioRpcError) and exc.code() is grpc.StatusCode.UNIMPLEMENTED
