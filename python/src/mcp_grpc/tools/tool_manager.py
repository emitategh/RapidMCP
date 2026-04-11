# python/src/mcp_grpc/tools/tool_manager.py
"""ToolManager — registry and dispatcher for tools."""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable
from functools import partial
from typing import Any

from mcp_grpc._generated import mcp_pb2
from mcp_grpc._utils import _to_content_items
from mcp_grpc.errors import McpError
from mcp_grpc.middleware import Middleware, ToolCallContext
from mcp_grpc.tools.tool import (
    RegisteredTool,
    ToolAnnotations,
    _build_input_schema,
    _needs_context,
    _resolve_hints,
)

logger = logging.getLogger(__name__)


class ToolManager:
    """Owns the tool registry, middleware chain, and tool dispatch."""

    def __init__(self, middleware: list[Middleware] | None = None) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._middleware: list[Middleware] = list(middleware or [])

    def tool(
        self,
        *,
        description: str | None = None,
        output_schema: dict[str, Any] | None = None,
        read_only: bool = False,
        destructive: bool = False,
        idempotent: bool = False,
        open_world: bool = False,
        title: str = "",
    ) -> Callable[[Callable], Callable]:
        """Register a tool.

        Args:
            description: Human-readable description. Falls back to the
                function's docstring if omitted.
            output_schema: JSON Schema dict describing the tool's structured
                output.
            read_only: Hint that this tool does not modify state.
            destructive: Hint that this tool may perform destructive changes.
            idempotent: Hint that repeated calls produce the same result.
            open_world: Hint that this tool interacts with the external world.
            title: Short human-readable title for the tool.
        """

        def decorator(fn: Callable) -> Callable:
            desc = description or (fn.__doc__ or "").strip()
            needs_ctx = _needs_context(fn)

            ann: ToolAnnotations | None = None
            if any((read_only, destructive, idempotent, open_world, title)):
                ann = ToolAnnotations(
                    title=title,
                    read_only_hint=read_only,
                    destructive_hint=destructive,
                    idempotent_hint=idempotent,
                    open_world_hint=open_world,
                )

            self._tools[fn.__name__] = RegisteredTool(
                name=fn.__name__,
                description=desc,
                input_schema=_build_input_schema(fn),
                handler=fn,
                needs_context=needs_ctx,
                output_schema=json.dumps(output_schema) if output_schema else "",
                annotations=ann,
            )
            return fn

        return decorator

    def add_middleware(self, middleware: Middleware) -> None:
        """Append a middleware to the chain (outermost if added last)."""
        self._middleware.append(middleware)

    def list_registered_tools(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    async def _dispatch_tool(
        self,
        name: str,
        arguments: dict,
        ctx: Any,
    ) -> mcp_pb2.CallToolResponse:
        """Run a tool call through the full middleware chain."""
        registered = self._tools.get(name)
        schema_dict: dict | None = None
        if registered:
            try:
                schema_dict = json.loads(registered.input_schema)
            except (ValueError, TypeError):
                pass
        tool_ctx = ToolCallContext(
            tool_name=name, arguments=arguments, ctx=ctx, input_schema=schema_dict
        )

        async def base(tc: ToolCallContext) -> mcp_pb2.CallToolResponse:
            return await self._call_tool_with_dict(tc.tool_name, tc.arguments, tc.ctx)

        chain = base
        for mw in reversed(self._middleware):
            chain = partial(mw.on_tool_call, call_next=chain)

        return await chain(tool_ctx)

    async def _call_tool_with_dict(
        self,
        name: str,
        arguments: dict[str, Any],
        ctx: Any = None,
    ) -> mcp_pb2.CallToolResponse:
        """Invoke a tool with a pre-parsed arguments dict (base of middleware chain)."""
        from mcp_grpc.context import Context

        tool = self._tools.get(name)
        if not tool:
            raise McpError(code=404, message=f"Tool '{name}' not found")
        args = dict(arguments)
        if tool.needs_context and ctx is not None:
            hints = _resolve_hints(tool.handler)
            for param_name, hint in hints.items():
                if hint is Context:
                    args[param_name] = ctx
                    break
        try:
            result = await tool.handler(**args)
            content = _to_content_items(result)
            return mcp_pb2.CallToolResponse(content=content, is_error=False)
        except Exception:
            logger.exception("Tool '%s' raised an exception", name)
            return mcp_pb2.CallToolResponse(
                content=[mcp_pb2.ContentItem(type="text", text=traceback.format_exc())],
                is_error=True,
            )
