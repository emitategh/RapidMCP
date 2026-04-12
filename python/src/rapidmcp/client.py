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
from rapidmcp.errors import McpError
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

    def __init__(self, target: str, token: str | None = None) -> None:
        self._target = target
        self._metadata = [("authorization", f"Bearer {token}")] if token else []
        self._pending = PendingRequests()
        self._notifications = NotificationRegistry()
        self._channel: grpc_aio.Channel | None = None
        self._stream: Any = None
        self._reader_task: asyncio.Task | None = None
        self.server_info: ServerInfo | None = None
        self._sampling_handler = None
        self._elicitation_handler = None
        self._roots_handler = None
        self._write_queue: asyncio.Queue[mcp_pb2.ClientEnvelope | None] | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._ref_count: int = 0

    def set_sampling_handler(self, handler) -> None:
        self._sampling_handler = handler

    def set_elicitation_handler(self, handler) -> None:
        self._elicitation_handler = handler

    def set_roots_handler(self, handler) -> None:
        self._roots_handler = handler

    @property
    def is_connected(self) -> bool:
        """True when a live gRPC channel and reader loop are active."""
        return (
            self._channel is not None
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def connect(self) -> None:
        logger.debug("connecting to %s", self._target)
        self._channel = grpc_aio.insecure_channel(self._target)
        stub = mcp_pb2_grpc.McpStub(self._channel)
        self._write_queue: asyncio.Queue[mcp_pb2.ClientEnvelope] = asyncio.Queue()
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

    _REQUEST_TIMEOUT = 30.0

    async def _request(self, envelope: mcp_pb2.ClientEnvelope) -> Any:
        rid = self._pending.next_id()
        envelope.request_id = rid
        msg_type = envelope.WhichOneof("message")
        logger.debug("→ %s rid=%d", msg_type, rid)
        future = self._pending.create(rid)
        t0 = time.monotonic()
        await self._send(envelope)
        try:
            result = await asyncio.wait_for(future, timeout=self._REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.warning(
                "request timed out: %s rid=%d after %.0fms (timeout=%.0fs)",
                msg_type,
                rid,
                elapsed_ms,
                self._REQUEST_TIMEOUT,
            )
            raise McpError(408, f"Request timed out: {msg_type} rid={rid}") from None
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.debug("← %s rid=%d %.1fms", msg_type, rid, elapsed_ms)
        return result

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
        env = mcp_pb2.ClientEnvelope(list_tools=mcp_pb2.ListToolsRequest(cursor=cursor or ""))
        resp = await self._request(env)
        return ListResult(
            items=[_convert_tool(t) for t in resp.tools],
            next_cursor=resp.next_cursor or None,
        )

    async def call_tool(self, name: str, arguments: dict | None = None) -> CallToolResult:
        env = mcp_pb2.ClientEnvelope(
            call_tool=mcp_pb2.CallToolRequest(
                name=name,
                arguments=json.dumps(arguments or {}),
            ),
        )
        resp = await self._request(env)
        return _convert_call_tool_result(resp)

    async def list_resources(self, cursor: str | None = None) -> ListResult:
        env = mcp_pb2.ClientEnvelope(
            list_resources=mcp_pb2.ListResourcesRequest(cursor=cursor or "")
        )
        resp = await self._request(env)
        return ListResult(
            items=[_convert_resource(r) for r in resp.resources],
            next_cursor=resp.next_cursor or None,
        )

    async def read_resource(self, uri: str) -> ReadResourceResult:
        env = mcp_pb2.ClientEnvelope(
            read_resource=mcp_pb2.ReadResourceRequest(uri=uri),
        )
        resp = await self._request(env)
        return _convert_read_resource_result(resp)

    async def subscribe_resource(self, uri: str) -> None:
        """Subscribe to updates for a specific resource URI."""
        await self._send(
            mcp_pb2.ClientEnvelope(
                request_id=0,
                subscribe_res=mcp_pb2.SubscribeResourceReq(uri=uri),
            )
        )

    async def list_prompts(self, cursor: str | None = None) -> ListResult:
        env = mcp_pb2.ClientEnvelope(list_prompts=mcp_pb2.ListPromptsRequest(cursor=cursor or ""))
        resp = await self._request(env)
        return ListResult(
            items=[_convert_prompt(p) for p in resp.prompts],
            next_cursor=resp.next_cursor or None,
        )

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> GetPromptResult:
        env = mcp_pb2.ClientEnvelope(
            get_prompt=mcp_pb2.GetPromptRequest(name=name, arguments=arguments or {}),
        )
        resp = await self._request(env)
        return _convert_get_prompt_result(resp)

    async def list_resource_templates(self, cursor: str | None = None) -> ListResult:
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
        env = mcp_pb2.ClientEnvelope(ping=mcp_pb2.PingRequest())
        await self._request(env)
        return True

    async def cancel(self, target_request_id: int) -> None:
        await self._send(
            mcp_pb2.ClientEnvelope(
                request_id=0,
                cancel=mcp_pb2.CancelRequest(target_request_id=target_request_id),
            )
        )

    async def notify_roots_list_changed(self) -> None:
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
        self._pending.cancel_all()
        if self._channel:
            await self._channel.close()
        self._ref_count = 0
        logger.debug("closed connection to %s", self._target)

    async def __aenter__(self):
        self._ref_count += 1
        if self._ref_count == 1:
            try:
                await self.connect()
            except BaseException:
                self._ref_count = 0
                raise
        return self

    async def __aexit__(self, *exc):
        self._ref_count -= 1
        if self._ref_count == 0:
            await self.close()
