"""v2 servicer — MCP 2026-07-28 semantics, one RPC per operation.

Stateless by construction: every handler reads what it needs from the
request's own ``meta`` and from the server's registries, never from anything
an earlier request left behind.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc
from rapidmcp._utils import _invoke, _paginate
from rapidmcp._v2_errors import abort
from rapidmcp.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    UNSUPPORTED_PROTOCOL_VERSION,
    McpError,
)

if TYPE_CHECKING:
    from rapidmcp.server import RapidMCP

logger = logging.getLogger("rapidmcp.server")

SUPPORTED_VERSIONS: tuple[str, ...] = ("2026-07-28",)
_HINTS = ("read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint")


class _McpV2Servicer(mcp_v2_pb2_grpc.McpServicer):
    def __init__(self, server: RapidMCP) -> None:
        self._server = server

    # ── shared pieces ────────────────────────────────────────────────────

    def _result_meta(self) -> pb.ResultMeta:
        return pb.ResultMeta(
            server_info=pb.Implementation(name=self._server.name, version=self._server.version)
        )

    @staticmethod
    def _no_cache() -> pb.CacheHint:
        return pb.CacheHint(ttl_ms=0, scope=pb.CACHE_SCOPE_PRIVATE)

    async def _check_meta(self, request, context) -> None:
        """Reject a request whose metadata is missing or names a version we do not serve."""
        meta = request.meta
        if not meta.protocol_version or not meta.HasField("client_capabilities"):
            await abort(
                context,
                McpError(
                    INVALID_PARAMS,
                    "Request meta must carry protocol_version and client_capabilities",
                ),
            )
        if meta.protocol_version not in SUPPORTED_VERSIONS:
            await abort(
                context,
                McpError(
                    UNSUPPORTED_PROTOCOL_VERSION,
                    "Unsupported protocol version",
                    data={
                        "supported": list(SUPPORTED_VERSIONS),
                        "requested": meta.protocol_version,
                    },
                ),
            )

    # ── RPCs ─────────────────────────────────────────────────────────────

    async def Discover(self, request, context):
        await self._check_meta(request, context)
        server = self._server
        capabilities = pb.ServerCapabilities()
        if server._tools:
            capabilities.tools.CopyFrom(pb.ToolsCapability(list_changed=True))
        if server._resources or server._resource_templates:
            capabilities.resources.CopyFrom(
                pb.ResourcesCapability(list_changed=True, subscribe=True)
            )
        if server._prompts:
            capabilities.prompts.CopyFrom(pb.PromptsCapability(list_changed=True))
        return pb.DiscoverResult(
            meta=self._result_meta(),
            supported_versions=SUPPORTED_VERSIONS,
            capabilities=capabilities,
            cache=self._no_cache(),
        )

    async def ListTools(self, request, context):
        await self._check_meta(request, context)
        tools = []
        for t in self._server._tools.values():
            tool = pb.Tool(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
                output_schema=t.output_schema,
            )
            if t.annotations:
                tool.annotations.title = t.annotations.title
                # Only the hints the author set go on the wire; the rest stay unset
                # so the client applies MCP's defaults.
                for hint in _HINTS:
                    value = getattr(t.annotations, hint)
                    if value is not None:
                        setattr(tool.annotations, hint, value)
            tools.append(tool)
        page, next_cursor = _paginate(tools, request.cursor, self._server.page_size)
        return pb.ListToolsResult(
            meta=self._result_meta(), tools=page, next_cursor=next_cursor, cache=self._no_cache()
        )

    async def ListResources(self, request, context):
        await self._check_meta(request, context)
        resources = [
            pb.Resource(uri=r.uri, name=r.name, description=r.description, mime_type=r.mime_type)
            for r in self._server._resources.values()
        ]
        page, next_cursor = _paginate(resources, request.cursor, self._server.page_size)
        return pb.ListResourcesResult(
            meta=self._result_meta(),
            resources=page,
            next_cursor=next_cursor,
            cache=self._no_cache(),
        )

    async def ListResourceTemplates(self, request, context):
        await self._check_meta(request, context)
        templates = [
            pb.ResourceTemplate(
                uri_template=t.uri_template,
                name=t.name,
                description=t.description,
                mime_type=t.mime_type,
            )
            for t in self._server._resource_templates.values()
        ]
        page, next_cursor = _paginate(templates, request.cursor, self._server.page_size)
        return pb.ListResourceTemplatesResult(
            meta=self._result_meta(),
            templates=page,
            next_cursor=next_cursor,
            cache=self._no_cache(),
        )

    async def ListPrompts(self, request, context):
        await self._check_meta(request, context)
        prompts = [
            pb.Prompt(
                name=p.name,
                description=p.description,
                arguments=[pb.PromptArgument(**a) for a in p.arguments],
            )
            for p in self._server._prompts.values()
        ]
        page, next_cursor = _paginate(prompts, request.cursor, self._server.page_size)
        return pb.ListPromptsResult(
            meta=self._result_meta(), prompts=page, next_cursor=next_cursor, cache=self._no_cache()
        )

    async def Complete(self, request, context):
        await self._check_meta(request, context)
        completion = self._server._completions.get(request.ref.name)
        if not completion:
            return pb.CompleteResult(meta=self._result_meta())
        try:
            values = await _invoke(
                completion.handler, request.argument.name, request.argument.value
            )
        except Exception:
            logger.exception("Completion handler for '%s' raised", request.ref.name)
            await abort(
                context,
                McpError(INTERNAL_ERROR, f"Completion handler for '{request.ref.name}' failed"),
            )
        return pb.CompleteResult(meta=self._result_meta(), values=values, total=len(values))
