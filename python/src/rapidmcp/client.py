"""Client: connect to an MCP gRPC server, discover and call tools."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Literal

import grpc
from grpc import aio as grpc_aio

from rapidmcp._generated import mcp_pb2, mcp_pb2_grpc
from rapidmcp._v2_client import _V2Transport, is_v2_missing
from rapidmcp.auth import ClientTLSConfig, _build_channel_credentials
from rapidmcp.errors import (
    METHOD_NOT_FOUND,
    NOT_CONNECTED,
    REQUEST_CANCELLED,
    REQUEST_TIMEOUT,
    McpError,
)
from rapidmcp.session import NotificationRegistry, PendingRequests
from rapidmcp.types import (
    CallToolResult,
    CompleteResult,
    GetPromptResult,
    ListResult,
    ReadResourceResult,
    ServerInfo,
    _convert_call_tool_result,
    _convert_complete_result,
    _convert_get_prompt_result,
    _convert_prompt,
    _convert_read_resource_result,
    _convert_resource,
    _convert_resource_template,
    _convert_tool,
)

logger = logging.getLogger("rapidmcp.client")


class Client:
    """Connect to an MCP gRPC server and interact with it.

    Supports reentrant ``async with`` usage — multiple nested or concurrent
    ``async with client:`` blocks share one connection.  The underlying gRPC
    channel is opened on the first entry and closed on the last exit.

    Direct ``connect()`` / ``close()`` calls bypass ref-counting and are still
    supported for explicit lifecycle management.
    """

    def __init__(
        self,
        target: str,
        token: str | None = None,
        tls: ClientTLSConfig | None = None,
        request_timeout: float = 30.0,
        mode: Literal["legacy", "modern", "auto"] = "legacy",
    ) -> None:
        if mode not in ("legacy", "modern", "auto"):
            raise ValueError(f"mode must be 'legacy', 'modern' or 'auto', not {mode!r}")
        self._mode = mode
        self._v2: _V2Transport | None = None
        self._target = target
        self._request_timeout = request_timeout
        self._metadata = [("authorization", f"Bearer {token}")] if token is not None else []
        self._tls = tls
        self._pending = PendingRequests()
        self._notifications = NotificationRegistry()
        self._channel: grpc_aio.Channel | None = None
        self._stream: Any = None
        self._reader_task: asyncio.Task | None = None
        self.server_info: ServerInfo | None = None
        self._sampling_handler = None
        self._elicitation_handler = None
        self._elicitation_url = False
        self._roots_handler = None
        self._write_queue: asyncio.Queue[mcp_pb2.ClientEnvelope | None] | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._ref_count: int = 0
        self._connect_lock = asyncio.Lock()

    def set_sampling_handler(self, handler) -> None:
        self._sampling_handler = handler

    def set_elicitation_handler(self, handler, *, url: bool = False) -> None:
        """Answer the server's questions.

        *handler* receives the request (``.message``, ``.schema``; on v2 also
        ``.mode`` and ``.url``) and returns an object with ``.action`` and
        ``.content``. Pass ``url=True`` if it can also send the user to a URL.
        """
        self._elicitation_handler = handler
        self._elicitation_url = url

    def set_roots_handler(self, handler) -> None:
        self._roots_handler = handler

    @property
    def protocol(self) -> str | None:
        """``"v2"`` or ``"v1"`` once connected, ``None`` before."""
        if self._v2 is not None:
            return "v2"
        return "v1" if self._reader_task is not None else None

    @property
    def is_connected(self) -> bool:
        """True when a live gRPC channel (and, on v1, its reader loop) is active."""
        if self._v2 is not None:
            return self._channel is not None
        return (
            self._channel is not None
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def connect(self) -> None:
        logger.debug("connecting to %s (mode=%s)", self._target, self._mode)
        if self._tls:
            self._channel = grpc_aio.secure_channel(
                self._target, _build_channel_credentials(self._tls)
            )
        else:
            self._channel = grpc_aio.insecure_channel(self._target)
        try:
            if self._mode != "legacy" and await self._connect_v2():
                return
            await self._connect_v1()
        except BaseException:
            await self.close()  # don't leave the channel and reader task behind
            raise

    async def _connect_v2(self) -> bool:
        """Try the v2 service. False means "old server, use v1" (auto mode only)."""
        transport = _V2Transport(
            self._channel,
            self._metadata,
            self._request_timeout,
            elicitation=lambda: (self._elicitation_handler, self._elicitation_url),
            notifications=self._notifications,
        )
        try:
            self.server_info = await transport.discover()
        except grpc_aio.AioRpcError as exc:
            if self._mode == "auto" and is_v2_missing(exc):
                logger.debug("%s does not serve v2; falling back to v1", self._target)
                return False
            raise
        self._v2 = transport
        return True

    async def _connect_v1(self) -> None:
        stub = mcp_pb2_grpc.McpStub(self._channel)
        self._write_queue = asyncio.Queue()
        self._stream = stub.Session(self._outbound_iter(), metadata=self._metadata)
        self._reader_task = asyncio.create_task(self._reader_loop())
        await self._initialize()
        logger.debug(
            "connected to %s  server=%s %s",
            self._target,
            self.server_info.server_name if self.server_info else "?",
            self.server_info.server_version if self.server_info else "?",
        )

    async def _outbound_iter(self):
        while True:
            envelope = await self._write_queue.get()
            if envelope is None:
                break
            yield envelope

    async def _send(self, envelope: mcp_pb2.ClientEnvelope) -> None:
        await self._write_queue.put(envelope)

    async def _request(self, envelope: mcp_pb2.ClientEnvelope, timeout: float | None = None) -> Any:
        if timeout is None:
            timeout = self._request_timeout
        if self._reader_task is None or self._reader_task.done():
            # Nothing is reading replies any more — fail now instead of at the timeout.
            raise McpError(NOT_CONNECTED, f"Not connected to {self._target}")
        rid = self._pending.next_id()
        envelope.request_id = rid
        msg_type = envelope.WhichOneof("message")
        logger.debug("→ %s rid=%d", msg_type, rid)
        future = self._pending.create(rid)
        t0 = time.monotonic()
        await self._send(envelope)
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.warning(
                "request timed out: %s rid=%d after %.0fms (timeout=%.1fs)",
                msg_type,
                rid,
                elapsed_ms,
                timeout,
            )
            self._pending.discard(rid)
            if msg_type == "call_tool":
                # We stopped waiting — don't leave the tool running on the server.
                await self.cancel(rid)
            raise McpError(REQUEST_TIMEOUT, f"Request timed out: {msg_type} rid={rid}") from None
        except asyncio.CancelledError:
            # The caller gave up (task cancelled, or an outer wait_for expired).
            self._pending.discard(rid)
            if msg_type == "call_tool" and self._write_queue is not None:
                await self.cancel(rid)
            raise
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.debug("← %s rid=%d %.1fms", msg_type, rid, elapsed_ms)
        return result

    def _v1_only(self, operation: str) -> None:
        """Raise for operations the v2 protocol does not carry yet."""
        if self._v2 is not None:
            raise McpError(
                METHOD_NOT_FOUND,
                f"{operation} is not available on the v2 protocol yet; use mode='legacy'",
            )

    async def _reader_loop(self) -> None:
        logger.debug("reader loop started for %s", self._target)
        try:
            async for envelope in self._stream:
                rid = envelope.request_id
                msg_type = envelope.WhichOneof("message")
                logger.debug("← server %s rid=%d", msg_type, rid)

                if msg_type == "error":
                    err = envelope.error
                    logger.debug("server error rid=%d code=%d: %s", rid, err.code, err.message)
                    self._pending.reject(rid, McpError(err.code, err.message))
                elif msg_type == "notification":
                    notif = envelope.notification
                    type_name = mcp_pb2.ServerNotification.Type.Name(notif.type).lower()
                    task = asyncio.create_task(
                        self._notifications.dispatch(type_name, notif.payload)
                    )
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                elif msg_type in ("sampling", "elicitation", "roots_request"):
                    task = asyncio.create_task(self._handle_server_request(envelope))
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                else:
                    inner = getattr(envelope, msg_type)
                    self._pending.resolve(rid, inner)
            logger.debug("reader loop ended normally for %s", self._target)
        except grpc.RpcError as exc:
            logger.warning("gRPC stream error for %s: %s %s", self._target, type(exc).__name__, exc)
            self._pending.reject_all(exc)

    async def _handle_server_request(self, envelope: mcp_pb2.ServerEnvelope) -> None:
        rid = envelope.request_id
        msg_type = envelope.WhichOneof("message")

        try:
            if msg_type == "sampling" and self._sampling_handler:
                result = await self._sampling_handler(envelope.sampling)
                await self._send(
                    mcp_pb2.ClientEnvelope(
                        request_id=rid,
                        sampling_reply=result,
                    )
                )
            elif msg_type == "elicitation" and self._elicitation_handler:
                result = await self._elicitation_handler(envelope.elicitation)
                await self._send(
                    mcp_pb2.ClientEnvelope(
                        request_id=rid,
                        elicitation_reply=result,
                    )
                )
            elif msg_type == "roots_request" and self._roots_handler:
                result = await self._roots_handler()
                await self._send(
                    mcp_pb2.ClientEnvelope(
                        request_id=rid,
                        roots_reply=result,
                    )
                )
            else:
                logger.warning(
                    "No handler for server request '%s' rid=%d, sending error",
                    msg_type,
                    rid,
                )
                await self._send(
                    mcp_pb2.ClientEnvelope(
                        request_id=rid,
                        error=mcp_pb2.ErrorResponse(
                            code=-32600,
                            message=f"{msg_type} not supported by this client",
                        ),
                    )
                )
        except Exception:
            logger.exception("Handler for server request '%s' raised", msg_type)
            await self._send(
                mcp_pb2.ClientEnvelope(
                    request_id=rid,
                    error=mcp_pb2.ErrorResponse(
                        code=-32603,
                        message=f"Handler for '{msg_type}' failed",
                    ),
                )
            )

    async def _initialize(self) -> None:
        env = mcp_pb2.ClientEnvelope(
            initialize=mcp_pb2.InitializeRequest(
                client_name="rapidmcp-python",
                client_version="0.1.0",
                capabilities=mcp_pb2.ClientCapabilities(
                    sampling=self._sampling_handler is not None,
                    elicitation=self._elicitation_handler is not None,
                    roots=self._roots_handler is not None,
                ),
            ),
        )
        resp = await self._request(env)
        self.server_info = ServerInfo(
            server_name=resp.server_name,
            server_version=resp.server_version,
            capabilities=resp.capabilities,
        )
        await self._send(
            mcp_pb2.ClientEnvelope(
                request_id=0,
                initialized=mcp_pb2.InitializedAck(),
            )
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_tools(self, cursor: str | None = None) -> ListResult:
        if self._v2 is not None:
            return await self._v2.list_tools(cursor)
        env = mcp_pb2.ClientEnvelope(list_tools=mcp_pb2.ListToolsRequest(cursor=cursor or ""))
        resp = await self._request(env)
        return ListResult(
            items=[_convert_tool(t) for t in resp.tools],
            next_cursor=resp.next_cursor or None,
        )

    async def call_tool(
        self, name: str, arguments: dict | None = None, *, timeout: float | None = None
    ) -> CallToolResult:
        """Call a tool. *timeout* (seconds) overrides the client's ``request_timeout``."""
        if self._v2 is not None:
            return await self._v2.call_tool(name, arguments, timeout)
        env = mcp_pb2.ClientEnvelope(
            call_tool=mcp_pb2.CallToolRequest(
                name=name,
                arguments=json.dumps(arguments or {}),
            ),
        )
        resp = await self._request(env, timeout=timeout)
        return _convert_call_tool_result(resp)

    async def list_resources(self, cursor: str | None = None) -> ListResult:
        if self._v2 is not None:
            return await self._v2.list_resources(cursor)
        env = mcp_pb2.ClientEnvelope(
            list_resources=mcp_pb2.ListResourcesRequest(cursor=cursor or "")
        )
        resp = await self._request(env)
        return ListResult(
            items=[_convert_resource(r) for r in resp.resources],
            next_cursor=resp.next_cursor or None,
        )

    async def read_resource(self, uri: str) -> ReadResourceResult:
        if self._v2 is not None:
            return await self._v2.read_resource(uri)
        env = mcp_pb2.ClientEnvelope(
            read_resource=mcp_pb2.ReadResourceRequest(uri=uri),
        )
        resp = await self._request(env)
        return _convert_read_resource_result(resp)

    async def subscribe_resource(self, uri: str) -> None:
        """Subscribe to updates for a specific resource URI."""
        self._v1_only("subscribe_resource")
        await self._send(
            mcp_pb2.ClientEnvelope(
                request_id=0,
                subscribe_res=mcp_pb2.SubscribeResourceReq(uri=uri),
            )
        )

    async def list_prompts(self, cursor: str | None = None) -> ListResult:
        if self._v2 is not None:
            return await self._v2.list_prompts(cursor)
        env = mcp_pb2.ClientEnvelope(list_prompts=mcp_pb2.ListPromptsRequest(cursor=cursor or ""))
        resp = await self._request(env)
        return ListResult(
            items=[_convert_prompt(p) for p in resp.prompts],
            next_cursor=resp.next_cursor or None,
        )

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> GetPromptResult:
        if self._v2 is not None:
            return await self._v2.get_prompt(name, arguments)
        env = mcp_pb2.ClientEnvelope(
            get_prompt=mcp_pb2.GetPromptRequest(name=name, arguments=arguments or {}),
        )
        resp = await self._request(env)
        return _convert_get_prompt_result(resp)

    async def list_resource_templates(self, cursor: str | None = None) -> ListResult:
        if self._v2 is not None:
            return await self._v2.list_resource_templates(cursor)
        env = mcp_pb2.ClientEnvelope(
            list_resource_templates=mcp_pb2.ListResourceTemplatesRequest(
                cursor=cursor or "",
            )
        )
        resp = await self._request(env)
        return ListResult(
            items=[_convert_resource_template(t) for t in resp.templates],
            next_cursor=resp.next_cursor or None,
        )

    async def complete(
        self,
        ref_type: Literal["ref/prompt", "ref/resource"],
        ref_name: str,
        argument_name: str,
        value: str,
    ) -> CompleteResult:
        if self._v2 is not None:
            return await self._v2.complete(ref_type, ref_name, argument_name, value)
        env = mcp_pb2.ClientEnvelope(
            complete=mcp_pb2.CompleteRequest(
                ref=mcp_pb2.CompletionRef(type=ref_type, name=ref_name),
                argument=mcp_pb2.CompletionArg(name=argument_name, value=value),
            )
        )
        resp = await self._request(env)
        return _convert_complete_result(resp)

    def on_notification(self, notification_type: str, handler) -> None:
        self._notifications.register(notification_type, handler)

    async def ping(self) -> bool:
        """Ping the server. Returns True on success, raises McpError on failure."""
        if self._v2 is not None:
            await self._v2.discover()
            return True
        env = mcp_pb2.ClientEnvelope(ping=mcp_pb2.PingRequest())
        await self._request(env)
        return True

    async def cancel(self, target_request_id: int) -> None:
        """Stop waiting for a request and tell the server to stop working on it.

        The pending call fails here with ``McpError(499)``; the server sends no
        response for a cancelled request.
        """
        self._v1_only("cancel")
        self._pending.reject(target_request_id, McpError(REQUEST_CANCELLED, "Request cancelled"))
        await self._send(
            mcp_pb2.ClientEnvelope(
                request_id=0,
                cancel=mcp_pb2.CancelRequest(target_request_id=target_request_id),
            )
        )

    async def notify_roots_list_changed(self) -> None:
        self._v1_only("notify_roots_list_changed")
        await self._send(
            mcp_pb2.ClientEnvelope(
                request_id=0,
                client_notification=mcp_pb2.ClientNotification(
                    type=mcp_pb2.ClientNotification.ROOTS_LIST_CHANGED,
                ),
            )
        )

    async def close(self) -> None:
        logger.debug("closing connection to %s", self._target)
        # Signal outbound iterator to stop
        if self._write_queue is not None:
            await self._write_queue.put(None)
            self._write_queue = None
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        self._pending.cancel_all()
        if self._channel:
            await self._channel.close()
            self._channel = None
        self._v2 = None
        self._ref_count = 0
        logger.debug("closed connection to %s", self._target)

    async def __aenter__(self):
        self._ref_count += 1
        try:
            # One entrant connects; the others wait here instead of running
            # against a client that is still mid-handshake.
            async with self._connect_lock:
                if self.protocol is None:
                    await self.connect()
        except BaseException:
            self._ref_count = max(0, self._ref_count - 1)
            raise
        return self

    async def __aexit__(self, *exc):
        # close() may already have reset the count from inside the block.
        self._ref_count = max(0, self._ref_count - 1)
        if self._ref_count == 0:
            await self.close()
