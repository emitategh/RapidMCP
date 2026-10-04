"""v2 servicer — MCP 2026-07-28 semantics, one RPC per operation.

Stateless by construction: every handler reads what it needs from the
request's own ``meta`` and from the server's registries, never from anything
an earlier request left behind.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import aclosing
from typing import TYPE_CHECKING

from rapidmcp._generated import mcp_v2_pb2 as pb
from rapidmcp._generated import mcp_v2_pb2_grpc
from rapidmcp._utils import _invoke, _paginate, _parse_tool_arguments, _resource_content_fields
from rapidmcp._v2_context import LOG_LEVELS, _NeedsInput, _V2Context
from rapidmcp._v2_errors import abort
from rapidmcp._v2_state import operation_digest, principal_digest, seal, unseal_state
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
_TRACE_KEYS = ("traceparent", "tracestate", "baggage")


def _icons(icons) -> list[pb.Icon]:
    return [
        pb.Icon(src=i.src, mime_type=i.mime_type, sizes=list(i.sizes), theme=i.theme) for i in icons
    ]


def _structured_text(ctx, result) -> str:
    """The structured result as JSON text, taken from what the middleware chain let through.

    A tool that returned an object produced one JSON text item. If that is still
    what the response holds, it is the structured content; if middleware replaced
    it with something else, there is none.
    """
    if ctx._structured_content is None or result.is_error or len(result.content) != 1:
        return ""
    text = result.content[0].text
    try:
        parsed = json.loads(text)
    except ValueError:
        return ""
    return text if isinstance(parsed, dict) else ""


async def _discard(event) -> None:
    """The emit of a call that asked for no progress and no logs."""


class _McpV2Servicer(mcp_v2_pb2_grpc.McpServicer):
    def __init__(self, server: RapidMCP) -> None:
        self._server = server
        self._warned_about_secret = False

    # ── shared pieces ────────────────────────────────────────────────────

    def _result_meta(self) -> pb.ResultMeta:
        return pb.ResultMeta(
            server_info=pb.Implementation(name=self._server.name, version=self._server.version)
        )

    def _cache_hint(self) -> pb.CacheHint:
        public = self._server._cache_scope == "public"
        return pb.CacheHint(
            ttl_ms=int(self._server._cache_ttl * 1000),
            scope=pb.CACHE_SCOPE_PUBLIC if public else pb.CACHE_SCOPE_PRIVATE,
        )

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
        if meta.HasField("log_level") and meta.log_level not in LOG_LEVELS:
            await abort(context, McpError(INVALID_PARAMS, f"Unknown log level '{meta.log_level}'"))

    def _principal(self, context) -> str:
        """Who a request_state belongs to: the caller's credentials, when the server checks them."""
        if self._server._auth is None:
            return ""
        return principal_digest(dict(context.invocation_metadata()).get("authorization"))

    def _seal(self, answers: dict, operation: str, principal: str, asked: list[str]) -> bytes:
        if not self._server._state_secret_configured and not self._warned_about_secret:
            self._warned_about_secret = True
            logger.warning(
                "Issuing request_state signed with a secret generated at start-up. "
                "Set RapidMCP(state_secret=...) so every replica can verify it."
            )
        return seal(self._server._state_secret, answers, operation, principal, asked=asked)

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
            meta=pb.ResultMeta(
                server_info=pb.Implementation(
                    name=server.name, version=server.version, icons=_icons(server.icons)
                )
            ),
            supported_versions=SUPPORTED_VERSIONS,
            capabilities=capabilities,
            cache=self._cache_hint(),
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
                icons=_icons(t.icons),
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
            meta=self._result_meta(), tools=page, next_cursor=next_cursor, cache=self._cache_hint()
        )

    async def ListResources(self, request, context):
        await self._check_meta(request, context)
        resources = [
            pb.Resource(
                uri=r.uri,
                name=r.name,
                description=r.description,
                mime_type=r.mime_type,
                icons=_icons(r.icons),
            )
            for r in self._server._resources.values()
        ]
        page, next_cursor = _paginate(resources, request.cursor, self._server.page_size)
        return pb.ListResourcesResult(
            meta=self._result_meta(),
            resources=page,
            next_cursor=next_cursor,
            cache=self._cache_hint(),
        )

    async def ListResourceTemplates(self, request, context):
        await self._check_meta(request, context)
        templates = [
            pb.ResourceTemplate(
                uri_template=t.uri_template,
                name=t.name,
                description=t.description,
                mime_type=t.mime_type,
                icons=_icons(t.icons),
            )
            for t in self._server._resource_templates.values()
        ]
        page, next_cursor = _paginate(templates, request.cursor, self._server.page_size)
        return pb.ListResourceTemplatesResult(
            meta=self._result_meta(),
            templates=page,
            next_cursor=next_cursor,
            cache=self._cache_hint(),
        )

    async def ListPrompts(self, request, context):
        await self._check_meta(request, context)
        prompts = [
            pb.Prompt(
                name=p.name,
                description=p.description,
                arguments=[pb.PromptArgument(**a) for a in p.arguments],
                icons=_icons(p.icons),
            )
            for p in self._server._prompts.values()
        ]
        page, next_cursor = _paginate(prompts, request.cursor, self._server.page_size)
        return pb.ListPromptsResult(
            meta=self._result_meta(),
            prompts=page,
            next_cursor=next_cursor,
            cache=self._cache_hint(),
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
        except McpError as error:
            await abort(context, error)
        except Exception:
            logger.exception("Completion handler for '%s' raised", request.ref.name)
            await abort(
                context,
                McpError(INTERNAL_ERROR, f"Completion handler for '{request.ref.name}' failed"),
            )
        return pb.CompleteResult(meta=self._result_meta(), values=values, total=len(values))

    # ── streaming RPCs ───────────────────────────────────────────────────

    @staticmethod
    async def _stream(work):
        """Yield whatever *work* emits while it runs, then its final event.

        *work* is ``async (emit) -> terminal event``. When the RPC is cancelled
        (the client went away, or its deadline passed) the work is cancelled too.
        """
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.ensure_future(work(queue.put))
        getter: asyncio.Future | None = None
        try:
            while not task.done():
                getter = asyncio.ensure_future(queue.get())
                await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter.done():
                    yield getter.result()
                else:
                    getter.cancel()
            while not queue.empty():
                yield queue.get_nowait()
            yield task.result()
        finally:
            if getter is not None and not getter.done():
                getter.cancel()
            if not task.done():
                task.cancel()

    async def CallTool(self, request, context):
        await self._check_meta(request, context)
        name = request.name

        async def work(emit):
            arguments = _parse_tool_arguments(name, request.arguments)
            operation = operation_digest("tools/call", name, request.arguments)
            principal = self._principal(context)

            # Earlier answers come back inside the state; the latest ones in the request.
            # An answer counts only if a state this server signed says it was asked for.
            answers: dict[str, dict[str, str]] = {}
            asked: list[str] = []
            if request.request_state:
                answers, asked = unseal_state(
                    self._server._state_secret, request.request_state, operation, principal
                )
            for key, response in request.input_responses.items():
                if key in asked and response.HasField("elicit"):
                    answers[key] = {
                        "action": response.elicit.action,
                        "content": response.elicit.content,
                    }

            trace = {
                key: value for key, value in context.invocation_metadata() if key in _TRACE_KEYS
            }
            ctx = _V2Context(request.meta, emit, answers, trace)
            try:
                result = await self._server._dispatch_tool(name, arguments, ctx)
            except _NeedsInput as need:
                return pb.CallToolEvent(
                    input_required=pb.InputRequired(
                        input_requests=need.requests,
                        request_state=self._seal(
                            answers, operation, principal, list(need.requests)
                        ),
                    )
                )
            return pb.CallToolEvent(
                complete=pb.CallToolResult(
                    meta=self._result_meta(),
                    content=[
                        pb.ContentItem(
                            type=c.type, text=c.text, data=c.data, mime_type=c.mime_type, uri=c.uri
                        )
                        for c in result.content
                    ],
                    is_error=result.is_error,
                    structured_content=_structured_text(ctx, result),
                )
            )

        try:
            if not request.meta.HasField("progress_token") and not request.meta.HasField(
                "log_level"
            ):
                # Nothing can be emitted before the result, so there is nothing to
                # multiplex: run the work on this task and yield what it returns.
                yield await work(_discard)
            else:
                # aclosing: if this RPC is cancelled while suspended at the yield below,
                # the inner generator is closed at once and its tool task cancelled.
                async with aclosing(self._stream(work)) as events:
                    async for event in events:
                        yield event
        except McpError as error:
            await abort(context, error)
        except Exception:
            logger.exception("Tool call '%s' failed outside the handler", name)
            await abort(context, McpError(INTERNAL_ERROR, f"Tool call '{name}' failed"))

    async def ReadResource(self, request, context):
        await self._check_meta(request, context)
        uri = request.uri
        found = self._server._resource_manager.resolve(uri)
        if found is None:
            await abort(context, McpError(INVALID_PARAMS, f"Resource '{uri}' not found"))
        resource, params = found
        try:
            raw = await _invoke(resource.handler, **params)
        except McpError as error:
            await abort(context, error)
        except Exception:
            logger.exception("Resource handler for '%s' raised", uri)
            await abort(context, McpError(INTERNAL_ERROR, f"Resource handler for '{uri}' failed"))
        yield pb.ReadResourceEvent(
            complete=pb.ReadResourceResult(
                meta=self._result_meta(),
                content=[
                    pb.ContentItem(uri=uri, **_resource_content_fields(raw, resource.mime_type))
                ],
                cache=self._cache_hint(),
            )
        )

    async def GetPrompt(self, request, context):
        await self._check_meta(request, context)
        name = request.name
        prompt = self._server._prompts.get(name)
        if prompt is None:
            await abort(context, McpError(INVALID_PARAMS, f"Prompt '{name}' not found"))
        arguments = dict(request.arguments)
        declared = [a["name"] for a in prompt.arguments]
        missing = [
            a["name"] for a in prompt.arguments if a["required"] and a["name"] not in arguments
        ]
        if missing:
            await abort(
                context,
                McpError(
                    INVALID_PARAMS,
                    f"Missing required argument(s) for prompt '{name}': {', '.join(missing)}",
                ),
            )
        unknown = [key for key in arguments if key not in declared]
        if unknown:
            await abort(
                context,
                McpError(
                    INVALID_PARAMS,
                    f"Unknown argument(s) for prompt '{name}': {', '.join(unknown)}",
                ),
            )
        try:
            text = await _invoke(prompt.handler, **arguments)
        except McpError as error:
            await abort(context, error)
        except Exception:
            logger.exception("Prompt handler '%s' raised", name)
            await abort(context, McpError(INTERNAL_ERROR, f"Prompt handler '{name}' failed"))
        yield pb.GetPromptEvent(
            complete=pb.GetPromptResult(
                meta=self._result_meta(),
                messages=[
                    pb.PromptMessage(role="user", content=pb.ContentItem(type="text", text=text))
                ],
            )
        )

    async def Listen(self, request, context):
        await self._check_meta(request, context)
        wanted = request.notifications
        listener = self._server._v2_listeners.add(wanted)
        try:
            yield pb.ListenEvent(acknowledged=wanted)
            for uri in wanted.resource_subscriptions:
                for handler in self._server._subscribe_handlers:
                    try:
                        await _invoke(handler, uri)
                    except Exception:
                        logger.exception("Subscribe handler for '%s' raised", uri)
            while True:
                yield await listener.queue.get()
        finally:
            # Cancelled by the client, or the connection dropped: forget the listener.
            self._server._v2_listeners.remove(listener)
