"""RapidMCP: register tools, resources, prompts and serve them over gRPC."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from grpc import aio as grpc_aio

from rapidmcp._generated import mcp_pb2, mcp_pb2_grpc, mcp_v2_pb2_grpc
from rapidmcp._servicer import _McpServicer
from rapidmcp._utils import _prefix_resource_uri
from rapidmcp._v2_servicer import _McpV2Servicer
from rapidmcp.auth import TLSConfig, _AuthInterceptor, _build_server_credentials
from rapidmcp.context import Context
from rapidmcp.middleware import Middleware
from rapidmcp.prompts import PromptManager, RegisteredCompletion, RegisteredPrompt
from rapidmcp.resources import RegisteredResource, RegisteredResourceTemplate, ResourceManager
from rapidmcp.tools import RegisteredTool, ToolManager

logger = logging.getLogger("rapidmcp.server")


class RapidMCP:
    """Register tools, resources, and prompts, then serve over gRPC."""

    def __init__(
        self,
        name: str,
        version: str,
        middleware: list[Middleware] | None = None,
        page_size: int | None = None,
        auth: Callable[[str], bool | Awaitable[bool]] | None = None,
        tls: TLSConfig | None = None,
        mask_error_details: bool = False,
        host: str | None = None,
        state_secret: str | bytes | None = None,
    ) -> None:
        # Signs the request_state v2 tools hand to clients between input rounds.
        # Replicas behind one load balancer must share it.
        self._state_secret_configured = state_secret is not None
        if state_secret is None:
            self._state_secret = secrets.token_bytes(32)
        elif isinstance(state_secret, str):
            self._state_secret = state_secret.encode()
        else:
            self._state_secret = state_secret
        self._host = host
        self._bound_host = host or "[::]"
        self.name = name
        self.version = version
        self.page_size = page_size
        self._auth = auth
        self._tls = tls
        self._tool_manager = ToolManager(
            middleware=middleware, mask_error_details=mask_error_details
        )
        self._resource_manager = ResourceManager()
        self._prompt_manager = PromptManager()
        self._session_queues: list[asyncio.Queue] = []
        self._client_notification_handlers: dict[str, list[Callable]] = {}
        self._subscribe_handlers: list[Callable] = []

    @property
    def _tools(self) -> dict[str, RegisteredTool]:
        return self._tool_manager._tools

    @property
    def _resources(self) -> dict[str, RegisteredResource]:
        return self._resource_manager._resources

    @property
    def _resource_templates(self) -> dict[str, RegisteredResourceTemplate]:
        return self._resource_manager._resource_templates

    @property
    def _prompts(self) -> dict[str, RegisteredPrompt]:
        return self._prompt_manager._prompts

    @property
    def _completions(self) -> dict[str, RegisteredCompletion]:
        return self._prompt_manager._completions

    def tool(
        self,
        *,
        description: str | None = None,
        output_schema: dict[str, Any] | None = None,
        read_only: bool | None = None,
        destructive: bool | None = None,
        idempotent: bool | None = None,
        open_world: bool | None = None,
        title: str = "",
    ) -> Callable[[Callable], Callable]:
        return self._tool_manager.tool(
            description=description,
            output_schema=output_schema,
            read_only=read_only,
            destructive=destructive,
            idempotent=idempotent,
            open_world=open_world,
            title=title,
        )

    def resource(
        self,
        uri: str,
        *,
        description: str | None = None,
        mime_type: str = "text/plain",
    ) -> Callable:
        return self._resource_manager.resource(uri, description=description, mime_type=mime_type)

    def prompt(self, *, description: str | None = None) -> Callable[[Callable], Callable]:
        return self._prompt_manager.prompt(description=description)

    def completion(self, ref_name: str) -> Callable:
        return self._prompt_manager.completion(ref_name)

    def resource_template(
        self,
        uri_template: str,
        *,
        description: str | None = None,
        mime_type: str = "text/plain",
    ) -> Callable:
        return self._resource_manager.resource_template(
            uri_template, description=description, mime_type=mime_type
        )

    def list_registered_tools(self) -> list[RegisteredTool]:
        return self._tool_manager.list_registered_tools()

    def list_registered_resources(self) -> list[RegisteredResource]:
        return self._resource_manager.list_registered_resources()

    def list_registered_prompts(self) -> list[RegisteredPrompt]:
        return self._prompt_manager.list_registered_prompts()

    def list_registered_resource_templates(self) -> list[RegisteredResourceTemplate]:
        return self._resource_manager.list_registered_resource_templates()

    def add_middleware(self, middleware: Middleware) -> None:
        """Append a middleware to the chain (outermost if added last)."""
        self._tool_manager.add_middleware(middleware)

    def mount(self, sub: RapidMCP, *, prefix: str) -> None:
        """Merge all registries from *sub* into this server under *prefix*.

        Tools and prompts are registered as ``{prefix}_{name}``.
        Resources and resource templates get *prefix* inserted as the first
        path segment after the scheme (``res://x`` -> ``res://{prefix}/x``).
        Completions are re-keyed as ``{prefix}_{ref_name}``.

        Sub-server middleware, notification handlers, and subscribe handlers
        are NOT merged — *sub* acts purely as a registry donor when mounted.
        Main's middleware chain wraps all mounted tools.

        Raises ValueError on any name/URI collision. The operation is atomic:
        either all registrations succeed or none are written.
        """
        # Pass 1: compute all new keys
        new_tools = {f"{prefix}_{n}": t for n, t in sub._tools.items()}
        new_resources = {_prefix_resource_uri(u, prefix): r for u, r in sub._resources.items()}
        new_templates = {
            _prefix_resource_uri(u, prefix): t for u, t in sub._resource_templates.items()
        }
        new_prompts = {f"{prefix}_{n}": p for n, p in sub._prompts.items()}
        new_completions = {f"{prefix}_{r}": c for r, c in sub._completions.items()}

        # Pass 1: validate — no writes yet
        for k in new_tools:
            if k in self._tools:
                raise ValueError(f"mount(prefix={prefix!r}): tool collision '{k}'")
        for k in new_resources:
            if k in self._resources:
                raise ValueError(f"mount(prefix={prefix!r}): resource collision '{k}'")
        for k in new_templates:
            if k in self._resource_templates:
                raise ValueError(f"mount(prefix={prefix!r}): resource template collision '{k}'")
        for k in new_prompts:
            if k in self._prompts:
                raise ValueError(f"mount(prefix={prefix!r}): prompt collision '{k}'")
        for k in new_completions:
            if k in self._completions:
                raise ValueError(f"mount(prefix={prefix!r}): completion collision '{k}'")

        # Pass 2: write — all or nothing
        for new_name, tool in new_tools.items():
            self._tools[new_name] = replace(tool, name=new_name)
        for new_uri, res in new_resources.items():
            self._resources[new_uri] = replace(res, uri=new_uri)
        for new_tmpl, tmpl in new_templates.items():
            self._resource_templates[new_tmpl] = replace(tmpl, uri_template=new_tmpl)
        for new_name, prompt in new_prompts.items():
            self._prompts[new_name] = replace(prompt, name=new_name)
        for new_ref, comp in new_completions.items():
            self._completions[new_ref] = replace(comp, ref_name=new_ref)

    async def _dispatch_tool(
        self,
        name: str,
        arguments: dict,
        ctx: Context | None,
    ) -> mcp_pb2.CallToolResponse:
        return await self._tool_manager._dispatch_tool(name, arguments, ctx)

    def on_roots_list_changed(self, handler: Callable) -> None:
        self._client_notification_handlers.setdefault("roots_list_changed", []).append(handler)

    def on_resource_subscribe(self, handler: Callable) -> None:
        self._subscribe_handlers.append(handler)

    def _broadcast(self, notification: mcp_pb2.ServerNotification) -> None:
        """Send a notification to all active sessions."""
        envelope = mcp_pb2.ServerEnvelope(
            request_id=0,
            notification=notification,
        )
        for queue in self._session_queues:
            queue.put_nowait(envelope)

    def notify_tools_list_changed(self) -> None:
        self._broadcast(
            mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.TOOLS_LIST_CHANGED,
            )
        )

    def notify_resources_list_changed(self) -> None:
        self._broadcast(
            mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.RESOURCES_LIST_CHANGED,
            )
        )

    def notify_resource_updated(self, uri: str) -> None:
        self._broadcast(
            mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.RESOURCE_UPDATED,
                payload=json.dumps({"uri": uri}),
            )
        )

    def notify_prompts_list_changed(self) -> None:
        self._broadcast(
            mcp_pb2.ServerNotification(
                type=mcp_pb2.ServerNotification.PROMPTS_LIST_CHANGED,
            )
        )

    async def _call_tool_with_dict(
        self,
        name: str,
        arguments: dict[str, Any],
        ctx: Context | None = None,
    ) -> mcp_pb2.CallToolResponse:
        """Invoke a tool with a pre-parsed arguments dict (used by middleware chain)."""
        return await self._tool_manager._call_tool_with_dict(name, arguments, ctx)

    async def handle_call_tool(
        self,
        name: str,
        arguments_json: str,
        context: Context | None = None,
    ) -> mcp_pb2.CallToolResponse:
        """Public API: parse JSON arguments and invoke the tool."""
        args = json.loads(arguments_json) if arguments_json else {}
        return await self._call_tool_with_dict(name, args, context)

    async def _start_grpc(self, port: int, host: str | None = None) -> grpc_aio.Server:
        host = host or self._host
        interceptors = [_AuthInterceptor(self._auth)] if self._auth else []
        server_credentials = _build_server_credentials(self._tls) if self._tls else None

        async def bind(bind_host: str) -> grpc_aio.Server:
            grpc_server = grpc_aio.server(interceptors=interceptors)
            mcp_pb2_grpc.add_McpServicer_to_server(_McpServicer(self), grpc_server)
            mcp_v2_pb2_grpc.add_McpServicer_to_server(_McpV2Servicer(self), grpc_server)
            address = f"{bind_host}:{port}"
            if server_credentials:
                self._port = grpc_server.add_secure_port(address, server_credentials)
            else:
                self._port = grpc_server.add_insecure_port(address)
            await grpc_server.start()
            self._bound_host = bind_host
            return grpc_server

        if host:
            return await bind(host)
        # No host given: all interfaces. Try IPv6 first, fall back to IPv4 on Windows
        try:
            return await bind("[::]")
        except (OSError, RuntimeError) as exc:
            logger.debug("IPv6 bind failed (%s), falling back to IPv4", exc)
            return await bind("127.0.0.1")

    def _print_banner(self, port: int) -> None:
        from rapidmcp import __version__

        title = "█▀▀ ▄▀█ █▀ ▀█▀ █▀▀ █▀█   █▀▄▀█ █▀▀ █▀█"
        sub = "█▀  █▀█ ▄█  █  ██▄ █▀▄   █ ▀ █ █▄▄ █▀▀"
        server_line = f"Server:  {self.name}, {self.version}"
        version_line = f"RapidMCP {__version__}"
        transport_line = f"grpc://{self._bound_host}:{port}"

        W = 76

        def row(content: str = "") -> str:
            pad = W - 2 - len(content)
            left = pad // 2
            right = pad - left
            return f"│{' ' * left}{content}{' ' * right}│"

        lines = [
            f"╭{'─' * W}╮",
            row(),
            row(),
            row(title),
            row(sub),
            row(),
            row(),
            row(version_line),
            row(),
            row(f"🖥  {server_line}"),
            row(f"🚀 Transport:  {transport_line}"),
            row(),
            f"╰{'─' * W}╯",
        ]
        print("\n" + "\n".join(lines) + "\n", flush=True)

    def run(self, port: int = 50051, host: str | None = None) -> None:
        """Blocking entry point — starts the gRPC server.

        *host* overrides the constructor's ``host``; with neither set the
        server listens on all interfaces.
        """

        async def _run():
            grpc_server = await self._start_grpc(port, host)
            self._print_banner(self._port)
            await grpc_server.wait_for_termination()

        asyncio.run(_run())

    async def __aenter__(self):
        """Async context manager for tests — port=0 picks a free port."""
        self._grpc_server = await self._start_grpc(0)
        return self

    async def __aexit__(self, *exc):
        await self._grpc_server.stop(grace=0)

    @property
    def port(self) -> int:
        return self._port
