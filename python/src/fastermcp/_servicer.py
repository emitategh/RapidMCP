"""_McpServicer — gRPC bidi session handler."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING

from fastermcp._generated import mcp_pb2, mcp_pb2_grpc
from fastermcp._utils import _paginate
from fastermcp.context import Context
from fastermcp.errors import McpError
from fastermcp.resources.uri_template import match_uri_template
from fastermcp.session import PendingRequests

if TYPE_CHECKING:
    from fastermcp.server import FasterMCP

logger = logging.getLogger("fastermcp.server")


class _McpServicer(mcp_pb2_grpc.McpServicer):
    def __init__(self, server: FasterMCP) -> None:
        self._server = server

    async def Session(self, request_iterator, context):
        """Handle one client connection over the bidi stream.

        Data flow
        ---------
        Client → server:  gRPC pumps ``request_iterator``; each item is a
            ``ClientEnvelope`` dispatched by ``_handle_envelope()``.

        Server → client:  handler coroutines push ``ServerEnvelope`` objects
            into ``write_queue``.  The race loop dequeues them and ``yield``s
            them back through the gRPC stream.

        Queue roles
        -----------
        ``write_queue`` — outbound messages waiting to be yielded to the
            client.  Named from the *caller's* perspective: handlers *write*
            responses into it; the loop *reads* from it to yield.

        Race loop
        ---------
        Two futures compete on every ``asyncio.wait`` iteration:

        ``read_task``  — ``request_iterator.__anext__()``
            Resolves when the client sends a new message.
            On ``StopAsyncIteration`` (client closed the stream): sets
            ``eof = True``, waits for in-flight tool tasks to finish, then
            puts ``None`` into ``write_queue`` to trigger shutdown.

        ``write_task`` — ``write_queue.get()``
            Resolves when a handler has queued an outbound envelope.
            ``None`` is the sentinel that breaks the loop and ends the stream.

        Keeping the generator inside an active ``__anext__()`` at all times
        is what lets gRPC continue pumping inbound messages — a background
        reader task would not be visible to the pump and would deadlock.
        """
        sid = uuid.uuid4().hex[:8]
        logger.debug("session %s started", sid)
        session_start = time.monotonic()

        write_queue: asyncio.Queue[mcp_pb2.ServerEnvelope | None] = asyncio.Queue()
        server_pending = PendingRequests()
        client_capabilities = mcp_pb2.ClientCapabilities()
        _tool_tasks: dict[int, asyncio.Task] = {}

        async def _handle_envelope(envelope: mcp_pb2.ClientEnvelope) -> None:
            nonlocal client_capabilities
            rid = envelope.request_id
            msg_type = envelope.WhichOneof("message")
            logger.debug("session %s ← %s rid=%d", sid, msg_type, rid)

            if msg_type == "initialize":
                client_capabilities = envelope.initialize.capabilities
                await write_queue.put(
                    mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        initialize=mcp_pb2.InitializeResponse(
                            server_name=self._server.name,
                            server_version=self._server.version,
                            capabilities=mcp_pb2.ServerCapabilities(
                                tools=bool(self._server._tools),
                                tools_list_changed=False,
                                resources=bool(self._server._resources),
                                prompts=bool(self._server._prompts),
                            ),
                        ),
                    )
                )

            elif msg_type == "initialized":
                self._server._session_queues.append(write_queue)

            elif msg_type == "list_tools":
                cursor_str = envelope.list_tools.cursor
                all_tools = []
                for t in self._server._tools.values():
                    ann_proto = None
                    if t.annotations:
                        ann_proto = mcp_pb2.ToolAnnotations(
                            title=t.annotations.title,
                            read_only_hint=t.annotations.read_only_hint,
                            destructive_hint=t.annotations.destructive_hint,
                            idempotent_hint=t.annotations.idempotent_hint,
                            open_world_hint=t.annotations.open_world_hint,
                        )
                    all_tools.append(
                        mcp_pb2.ToolDefinition(
                            name=t.name,
                            description=t.description,
                            input_schema=t.input_schema,
                            output_schema=t.output_schema,
                            annotations=ann_proto,
                        )
                    )
                page, next_cursor = _paginate(all_tools, cursor_str, self._server.page_size)
                await write_queue.put(
                    mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        list_tools=mcp_pb2.ListToolsResponse(tools=page, next_cursor=next_cursor),
                    )
                )

            elif msg_type == "call_tool":
                req = envelope.call_tool

                async def _run_tool(_rid, _req, _caps=client_capabilities):
                    t0 = time.monotonic()
                    logger.debug("session %s tool %s started rid=%d", sid, _req.name, _rid)
                    try:
                        tool = self._server._tools.get(_req.name)
                        ctx = None
                        if tool and tool.needs_context:
                            ctx = Context(
                                client_capabilities=_caps,
                                pending=server_pending,
                                write_queue=write_queue,
                            )
                        args = json.loads(_req.arguments) if _req.arguments else {}
                        result = await self._server._dispatch_tool(_req.name, args, ctx)
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        logger.debug(
                            "session %s tool %s done rid=%d %.1fms",
                            sid,
                            _req.name,
                            _rid,
                            elapsed_ms,
                        )
                        await write_queue.put(
                            mcp_pb2.ServerEnvelope(
                                request_id=_rid,
                                call_tool=result,
                            )
                        )
                    except asyncio.CancelledError:
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        logger.warning(
                            "session %s tool %s cancelled rid=%d %.1fms",
                            sid,
                            _req.name,
                            _rid,
                            elapsed_ms,
                        )
                        await write_queue.put(
                            mcp_pb2.ServerEnvelope(
                                request_id=_rid,
                                error=mcp_pb2.ErrorResponse(
                                    code=499, message="Tool call cancelled"
                                ),
                            )
                        )
                    except McpError as e:
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        logger.warning(
                            "session %s tool %s error rid=%d code=%d %.1fms: %s",
                            sid,
                            _req.name,
                            _rid,
                            e.code,
                            elapsed_ms,
                            e.message,
                        )
                        await write_queue.put(
                            mcp_pb2.ServerEnvelope(
                                request_id=_rid,
                                error=mcp_pb2.ErrorResponse(code=e.code, message=e.message),
                            )
                        )
                    finally:
                        _tool_tasks.pop(_rid, None)

                task = asyncio.create_task(_run_tool(rid, req))
                _tool_tasks[rid] = task

            elif msg_type == "list_resources":
                cursor_str = envelope.list_resources.cursor
                all_resources = [
                    mcp_pb2.ResourceDefinition(
                        uri=r.uri,
                        name=r.name,
                        description=r.description,
                        mime_type=r.mime_type,
                    )
                    for r in self._server._resources.values()
                ]
                page, next_cursor = _paginate(all_resources, cursor_str, self._server.page_size)
                await write_queue.put(
                    mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        list_resources=mcp_pb2.ListResourcesResponse(
                            resources=page, next_cursor=next_cursor
                        ),
                    )
                )

            elif msg_type == "read_resource":
                uri = envelope.read_resource.uri
                res = self._server._resources.get(uri)
                template_params: dict[str, str] | None = None

                # Fallback: try matching against resource templates
                if not res:
                    for tmpl in self._server._resource_templates.values():
                        params = match_uri_template(uri, tmpl.uri_template)
                        if params is not None:
                            res = tmpl
                            template_params = params
                            break

                if not res:
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            error=mcp_pb2.ErrorResponse(
                                code=404,
                                message=f"Resource '{uri}' not found",
                            ),
                        )
                    )
                    return
                try:
                    if template_params is not None:
                        raw = await res.handler(**template_params)
                    else:
                        raw = await res.handler()
                except Exception:
                    logger.exception("Resource handler for '%s' raised", uri)
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            error=mcp_pb2.ErrorResponse(
                                code=500,
                                message=f"Resource handler for '{uri}' failed",
                            ),
                        )
                    )
                    return
                if isinstance(raw, bytes):
                    mime = res.mime_type
                    if mime.startswith("image/"):
                        content_item = mcp_pb2.ContentItem(type="image", data=raw, mime_type=mime)
                    elif mime.startswith("audio/"):
                        content_item = mcp_pb2.ContentItem(type="audio", data=raw, mime_type=mime)
                    else:
                        content_item = mcp_pb2.ContentItem(
                            type="resource", data=raw, mime_type=mime
                        )
                else:
                    content_item = mcp_pb2.ContentItem(type="text", text=str(raw))
                await write_queue.put(
                    mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        read_resource=mcp_pb2.ReadResourceResponse(
                            content=[content_item],
                        ),
                    )
                )

            elif msg_type == "list_resource_templates":
                cursor_str = envelope.list_resource_templates.cursor
                all_templates = [
                    mcp_pb2.ResourceTemplateDefinition(
                        uri_template=t.uri_template,
                        name=t.name,
                        description=t.description,
                        mime_type=t.mime_type,
                    )
                    for t in self._server._resource_templates.values()
                ]
                page, next_cursor = _paginate(all_templates, cursor_str, self._server.page_size)
                await write_queue.put(
                    mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        list_resource_templates=mcp_pb2.ListResourceTemplatesResponse(
                            templates=page, next_cursor=next_cursor
                        ),
                    )
                )

            elif msg_type == "list_prompts":
                cursor_str = envelope.list_prompts.cursor
                all_prompts = [
                    mcp_pb2.PromptDefinition(
                        name=p.name,
                        description=p.description,
                        arguments=[mcp_pb2.PromptArgument(**a) for a in p.arguments],
                    )
                    for p in self._server._prompts.values()
                ]
                page, next_cursor = _paginate(all_prompts, cursor_str, self._server.page_size)
                await write_queue.put(
                    mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        list_prompts=mcp_pb2.ListPromptsResponse(
                            prompts=page, next_cursor=next_cursor
                        ),
                    )
                )

            elif msg_type == "get_prompt":
                req = envelope.get_prompt
                prompt = self._server._prompts.get(req.name)
                if not prompt:
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            error=mcp_pb2.ErrorResponse(
                                code=404,
                                message=f"Prompt '{req.name}' not found",
                            ),
                        )
                    )
                    return
                try:
                    text = await prompt.handler(**dict(req.arguments))
                except Exception:
                    logger.exception("Prompt handler '%s' raised", req.name)
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            error=mcp_pb2.ErrorResponse(
                                code=500,
                                message=f"Prompt handler '{req.name}' failed",
                            ),
                        )
                    )
                    return
                await write_queue.put(
                    mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        get_prompt=mcp_pb2.GetPromptResponse(
                            messages=[
                                mcp_pb2.PromptMessage(
                                    role="assistant",
                                    content=mcp_pb2.ContentItem(type="text", text=text),
                                )
                            ],
                        ),
                    )
                )

            elif msg_type == "complete":
                req = envelope.complete
                comp = self._server._completions.get(req.ref.name)
                if not comp:
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            complete=mcp_pb2.CompleteResponse(values=[], has_more=False, total=0),
                        )
                    )
                else:
                    values = await comp.handler(req.argument.name, req.argument.value)
                    await write_queue.put(
                        mcp_pb2.ServerEnvelope(
                            request_id=rid,
                            complete=mcp_pb2.CompleteResponse(
                                values=values,
                                has_more=False,
                                total=len(values),
                            ),
                        )
                    )

            elif msg_type == "ping":
                await write_queue.put(
                    mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        pong=mcp_pb2.PingResponse(),
                    )
                )

            elif msg_type == "sampling_reply":
                server_pending.resolve(rid, envelope.sampling_reply)

            elif msg_type == "elicitation_reply":
                server_pending.resolve(rid, envelope.elicitation_reply)

            elif msg_type == "roots_reply":
                server_pending.resolve(rid, envelope.roots_reply)

            elif msg_type == "error":
                err = envelope.error
                server_pending.reject(rid, McpError(err.code, err.message))

            elif msg_type == "client_notification":
                notif = envelope.client_notification
                type_name = mcp_pb2.ClientNotification.Type.Name(notif.type).lower()
                for handler in self._server._client_notification_handlers.get(type_name, []):
                    try:
                        result = handler(notif.payload)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("Notification handler for '%s' raised", type_name)

            elif msg_type == "cancel":
                target_id = envelope.cancel.target_request_id
                task = _tool_tasks.get(target_id)
                if task and not task.done():
                    task.cancel()

            elif msg_type == "subscribe_res":
                uri = envelope.subscribe_res.uri
                for handler in self._server._subscribe_handlers:
                    try:
                        result = handler(uri)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("Subscribe handler for '%s' raised", uri)

            else:
                await write_queue.put(
                    mcp_pb2.ServerEnvelope(
                        request_id=rid,
                        error=mcp_pb2.ErrorResponse(
                            code=400,
                            message=f"Unknown message type: {msg_type}",
                        ),
                    )
                )

        read_task: asyncio.Future = asyncio.ensure_future(request_iterator.__anext__())
        write_task: asyncio.Future = asyncio.ensure_future(write_queue.get())
        eof = False

        try:
            while True:
                waiting = ({read_task} if not eof else set()) | {write_task}
                done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

                if read_task in done:
                    try:
                        envelope = read_task.result()
                        await _handle_envelope(envelope)
                        read_task = asyncio.ensure_future(request_iterator.__anext__())
                    except StopAsyncIteration:
                        eof = True
                        logger.debug(
                            "session %s client EOF, draining %d tool task(s)",
                            sid,
                            len(_tool_tasks),
                        )
                        # Wait for in-flight tool tasks before signalling EOF.
                        if _tool_tasks:
                            await asyncio.gather(*_tool_tasks.values(), return_exceptions=True)
                        await write_queue.put(None)

                if write_task in done:
                    msg = write_task.result()
                    if msg is None:
                        break
                    yield msg
                    write_task = asyncio.ensure_future(write_queue.get())
        finally:
            elapsed_s = time.monotonic() - session_start
            logger.debug("session %s closed after %.1fs", sid, elapsed_s)
            read_task.cancel()
            write_task.cancel()
            try:
                self._server._session_queues.remove(write_queue)
            except ValueError:
                pass
