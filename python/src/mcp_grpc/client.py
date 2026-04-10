"""McpClient: connect to an MCP gRPC server, discover and call tools."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import grpc
from grpc import aio as grpc_aio

from mcp_grpc._generated import mcp_pb2, mcp_pb2_grpc
from mcp_grpc.errors import McpError
from mcp_grpc.session import NotificationRegistry, PendingRequests


@dataclass
class ListResult:
    """Result from a paginated list method."""
    items: list
    next_cursor: str | None


@dataclass
class ServerInfo:
    server_name: str
    server_version: str
    capabilities: mcp_pb2.ServerCapabilities


class McpClient:
    """Connect to an MCP gRPC server and interact with it."""

    def __init__(self, target: str) -> None:
        self._target = target
        self._pending = PendingRequests()
        self._notifications = NotificationRegistry()
        self._channel: grpc_aio.Channel | None = None
        self._stream: Any = None
        self._reader_task: asyncio.Task | None = None
        self.server_info: ServerInfo | None = None
        self._sampling_handler = None
        self._elicitation_handler = None
        self._roots_handler = None

    def set_sampling_handler(self, handler) -> None:
        self._sampling_handler = handler

    def set_elicitation_handler(self, handler) -> None:
        self._elicitation_handler = handler

    def set_roots_handler(self, handler) -> None:
        self._roots_handler = handler

    async def connect(self) -> None:
        self._channel = grpc_aio.insecure_channel(self._target)
        stub = mcp_pb2_grpc.McpStub(self._channel)
        self._write_queue: asyncio.Queue[mcp_pb2.ClientEnvelope] = asyncio.Queue()
        self._stream = stub.Session(self._outbound_iter())
        self._reader_task = asyncio.create_task(self._reader_loop())
        await self._initialize()

    async def _outbound_iter(self):
        while True:
            envelope = await self._write_queue.get()
            yield envelope

    async def _send(self, envelope: mcp_pb2.ClientEnvelope) -> None:
        await self._write_queue.put(envelope)

    async def _request(self, envelope: mcp_pb2.ClientEnvelope) -> Any:
        rid = self._pending.next_id()
        envelope.request_id = rid
        future = self._pending.create(rid)
        await self._send(envelope)
        return await asyncio.wait_for(future, timeout=30.0)

    async def _reader_loop(self) -> None:
        try:
            async for envelope in self._stream:
                rid = envelope.request_id
                msg_type = envelope.WhichOneof("message")

                if msg_type == "error":
                    err = envelope.error
                    self._pending.reject(rid, McpError(err.code, err.message))
                elif msg_type == "notification":
                    notif = envelope.notification
                    type_name = mcp_pb2.ServerNotification.Type.Name(notif.type).lower()
                    asyncio.create_task(
                        self._notifications.dispatch(type_name, notif.payload)
                    )
                elif msg_type in ("sampling", "elicitation", "roots_request"):
                    asyncio.create_task(self._handle_server_request(envelope))
                else:
                    inner = getattr(envelope, msg_type)
                    self._pending.resolve(rid, inner)
        except grpc.RpcError:
            self._pending.cancel_all()

    async def _handle_server_request(self, envelope: mcp_pb2.ServerEnvelope) -> None:
        rid = envelope.request_id
        msg_type = envelope.WhichOneof("message")

        try:
            if msg_type == "sampling" and self._sampling_handler:
                result = await self._sampling_handler(envelope.sampling)
                await self._send(mcp_pb2.ClientEnvelope(
                    request_id=rid, sampling_reply=result,
                ))
            elif msg_type == "elicitation" and self._elicitation_handler:
                result = await self._elicitation_handler(envelope.elicitation)
                await self._send(mcp_pb2.ClientEnvelope(
                    request_id=rid, elicitation_reply=result,
                ))
            elif msg_type == "roots_request" and self._roots_handler:
                result = await self._roots_handler()
                await self._send(mcp_pb2.ClientEnvelope(
                    request_id=rid, roots_reply=result,
                ))
        except Exception:
            pass  # handler errors silently dropped for now

    async def _initialize(self) -> None:
        env = mcp_pb2.ClientEnvelope(
            initialize=mcp_pb2.InitializeRequest(
                client_name="mcp-grpc-python",
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

    async def list_tools(self, cursor: str | None = None) -> ListResult:
        env = mcp_pb2.ClientEnvelope(
            list_tools=mcp_pb2.ListToolsRequest(cursor=cursor or "")
        )
        resp = await self._request(env)
        return ListResult(
            items=list(resp.tools),
            next_cursor=resp.next_cursor or None,
        )

    async def call_tool(
        self, name: str, arguments: dict | None = None
    ) -> mcp_pb2.CallToolResponse:
        env = mcp_pb2.ClientEnvelope(
            call_tool=mcp_pb2.CallToolRequest(
                name=name,
                arguments=json.dumps(arguments or {}),
            ),
        )
        return await self._request(env)

    async def list_resources(self, cursor: str | None = None) -> ListResult:
        env = mcp_pb2.ClientEnvelope(
            list_resources=mcp_pb2.ListResourcesRequest(cursor=cursor or "")
        )
        resp = await self._request(env)
        return ListResult(
            items=list(resp.resources),
            next_cursor=resp.next_cursor or None,
        )

    async def read_resource(self, uri: str) -> mcp_pb2.ReadResourceResponse:
        env = mcp_pb2.ClientEnvelope(
            read_resource=mcp_pb2.ReadResourceRequest(uri=uri),
        )
        return await self._request(env)

    async def list_prompts(self, cursor: str | None = None) -> ListResult:
        env = mcp_pb2.ClientEnvelope(
            list_prompts=mcp_pb2.ListPromptsRequest(cursor=cursor or "")
        )
        resp = await self._request(env)
        return ListResult(
            items=list(resp.prompts),
            next_cursor=resp.next_cursor or None,
        )

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> mcp_pb2.GetPromptResponse:
        env = mcp_pb2.ClientEnvelope(
            get_prompt=mcp_pb2.GetPromptRequest(name=name, arguments=arguments or {}),
        )
        return await self._request(env)

    async def list_resource_templates(self, cursor: str | None = None) -> ListResult:
        env = mcp_pb2.ClientEnvelope(
            list_resource_templates=mcp_pb2.ListResourceTemplatesRequest(
                cursor=cursor or "",
            )
        )
        resp = await self._request(env)
        return ListResult(
            items=list(resp.templates),
            next_cursor=resp.next_cursor or None,
        )

    async def complete(
        self, ref_type: str, ref_name: str, argument_name: str, value: str,
    ) -> mcp_pb2.CompleteResponse:
        env = mcp_pb2.ClientEnvelope(
            complete=mcp_pb2.CompleteRequest(
                ref=mcp_pb2.CompletionRef(type=ref_type, name=ref_name),
                argument=mcp_pb2.CompletionArg(name=argument_name, value=value),
            )
        )
        return await self._request(env)

    def on_notification(self, notification_type: str, handler) -> None:
        self._notifications.register(notification_type, handler)

    async def ping(self) -> None:
        env = mcp_pb2.ClientEnvelope(ping=mcp_pb2.PingRequest())
        await self._request(env)

    async def cancel(self, target_request_id: int) -> None:
        await self._send(mcp_pb2.ClientEnvelope(
            request_id=0,
            cancel=mcp_pb2.CancelRequest(target_request_id=target_request_id),
        ))

    async def notify_roots_list_changed(self) -> None:
        await self._send(mcp_pb2.ClientEnvelope(
            request_id=0,
            client_notification=mcp_pb2.ClientNotification(
                type=mcp_pb2.ClientNotification.ROOTS_LIST_CHANGED,
            ),
        ))

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        self._pending.cancel_all()
        if self._channel:
            await self._channel.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()
