"""Client transport for the v2 protocol: one stateless RPC per operation."""

from __future__ import annotations

from collections.abc import Callable

import grpc
from grpc import aio

from rapidmcp._generated import mcp_pb2, mcp_v2_pb2_grpc
from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._v2_errors import error_from_rpc
from rapidmcp._version import __version__
from rapidmcp.types import (
    CompleteResult,
    ListResult,
    ServerInfo,
    _convert_complete_result,
    _convert_prompt,
    _convert_resource,
    _convert_resource_template,
    _convert_tool_v2,
)

PROTOCOL_VERSION = "2026-07-28"


class _V2Transport:
    def __init__(
        self,
        channel: aio.Channel,
        metadata: list[tuple[str, str]],
        timeout: float,
        supports_elicitation: Callable[[], bool],
    ) -> None:
        self._stub = mcp_v2_pb2_grpc.McpStub(channel)
        self._metadata = metadata
        self._timeout = timeout
        self._supports_elicitation = supports_elicitation

    def _meta(self) -> pb.RequestMeta:
        capabilities = pb.ClientCapabilities()
        if self._supports_elicitation():
            capabilities.elicitation.CopyFrom(pb.ElicitationCapability(form=True))
        return pb.RequestMeta(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=capabilities,
            client_info=pb.Implementation(name="rapidmcp-python", version=__version__),
        )

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


def is_v2_missing(exc: BaseException) -> bool:
    """True when a failed Discover means "this server does not serve v2"."""
    return isinstance(exc, aio.AioRpcError) and exc.code() is grpc.StatusCode.UNIMPLEMENTED
