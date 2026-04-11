"""Context: dependency-injection handle for tool handlers."""

from __future__ import annotations

import asyncio
import json

from mcp_grpc._generated import mcp_pb2
from mcp_grpc.elicitation import ElicitationResult, build_elicitation_schema
from mcp_grpc.errors import McpError
from mcp_grpc.session import PendingRequests

_DEFAULT_TIMEOUT: float = 30.0


class Context:
    """Provides sampling and elicitation to tool handlers via dependency injection."""

    def __init__(
        self,
        client_capabilities: mcp_pb2.ClientCapabilities,
        pending: PendingRequests,
        write_queue: asyncio.Queue,
    ) -> None:
        self._capabilities = client_capabilities
        self._pending = pending
        self._write_queue = write_queue

    async def sample(
        self,
        messages: list,
        max_tokens: int,
        system_prompt: str | None = None,
        model_preferences: dict | mcp_pb2.ModelPreferences | None = None,
        tools: list | None = None,
        tool_choice: str = "",
    ) -> mcp_pb2.SamplingResponse:
        """Request LLM completion from the client.

        Args:
            messages: List of ``mcp_pb2.SamplingMessage`` protos or dicts with
                ``{"role": str, "content": str | list}`` keys.
            max_tokens: Maximum tokens for the completion.
            system_prompt: Optional system prompt.
            model_preferences: Optional model selection hints.  Accepts an
                ``mcp_pb2.ModelPreferences`` proto or a dict with optional keys
                ``hints`` (list of model-name strings), ``cost_priority``,
                ``speed_priority``, ``intelligence_priority`` (each 0-1 float).
            tools: Optional list of tools available during sampling.  Each
                element may be an ``mcp_pb2.SamplingTool`` proto or a dict with
                ``name``, ``description``, and ``input_schema`` keys.
            tool_choice: How the model may use tools — ``"auto"``,
                ``"required"``, ``"none"``, or ``""`` (server default).
        """
        if not self._capabilities.sampling:
            raise McpError(400, "Client does not support sampling")
        rid = self._pending.next_id()
        future = self._pending.create(rid)

        # --- build SamplingMessages (content is now repeated ContentItem) ---
        sampling_messages: list[mcp_pb2.SamplingMessage] = []
        for msg in messages:
            if isinstance(msg, mcp_pb2.SamplingMessage):
                sampling_messages.append(msg)
            else:
                raw_content = msg.get("content", "")
                if isinstance(raw_content, str):
                    content_items = [mcp_pb2.ContentItem(type="text", text=raw_content)]
                elif isinstance(raw_content, list):
                    content_items = []
                    for item in raw_content:
                        if isinstance(item, mcp_pb2.ContentItem):
                            content_items.append(item)
                        elif isinstance(item, str):
                            content_items.append(mcp_pb2.ContentItem(type="text", text=item))
                        else:
                            content_items.append(
                                mcp_pb2.ContentItem(
                                    type=item.get("type", "text"),
                                    text=item.get("text", ""),
                                )
                            )
                else:
                    content_items = [mcp_pb2.ContentItem(type="text", text=str(raw_content))]
                sampling_messages.append(
                    mcp_pb2.SamplingMessage(
                        role=msg.get("role", "user"),
                        content=content_items,
                    )
                )

        # --- build ModelPreferences ---
        model_prefs_proto: mcp_pb2.ModelPreferences | None = None
        if isinstance(model_preferences, mcp_pb2.ModelPreferences):
            model_prefs_proto = model_preferences
        elif isinstance(model_preferences, dict):
            hints = [
                mcp_pb2.ModelHint(name=h)
                if isinstance(h, str)
                else mcp_pb2.ModelHint(name=h.get("name", ""))
                for h in model_preferences.get("hints", [])
            ]
            model_prefs_proto = mcp_pb2.ModelPreferences(
                hints=hints,
                cost_priority=float(model_preferences.get("cost_priority", 0.0)),
                speed_priority=float(model_preferences.get("speed_priority", 0.0)),
                intelligence_priority=float(model_preferences.get("intelligence_priority", 0.0)),
            )

        # --- build SamplingTools ---
        sampling_tools: list[mcp_pb2.SamplingTool] = []
        for t in tools or []:
            if isinstance(t, mcp_pb2.SamplingTool):
                sampling_tools.append(t)
            else:
                sampling_tools.append(
                    mcp_pb2.SamplingTool(
                        name=t.get("name", ""),
                        description=t.get("description", ""),
                        input_schema=t.get("input_schema", ""),
                    )
                )

        req = mcp_pb2.SamplingRequest(
            messages=sampling_messages,
            system_prompt=system_prompt or "",
            max_tokens=max_tokens,
            tool_choice=tool_choice,
        )
        if model_prefs_proto is not None:
            req.model_preferences.CopyFrom(model_prefs_proto)
        if sampling_tools:
            req.tools.extend(sampling_tools)

        envelope = mcp_pb2.ServerEnvelope(request_id=rid, sampling=req)
        await self._write_queue.put(envelope)
        return await asyncio.wait_for(future, timeout=_DEFAULT_TIMEOUT)

    async def elicit(
        self,
        message: str,
        schema: str | None = None,
        fields: dict | None = None,
    ) -> ElicitationResult:
        """Request user input from the client.

        Args:
            message: Human-readable prompt shown to the user.
            schema:  Raw JSON Schema string describing the form.  Mutually
                exclusive with *fields*.
            fields:  Mapping of field name → field descriptor
                (``StringField``, ``BoolField``, ``IntField``, ``FloatField``,
                ``EnumField``).  Automatically serialised to a valid MCP
                elicitation schema.

        Returns:
            An :class:`ElicitationResult` with ``action`` (``"accept"``,
            ``"decline"``, or ``"cancel"``) and ``data`` (dict of filled values
            when accepted).
        """
        if not self._capabilities.elicitation:
            raise McpError(400, "Client does not support elicitation")
        if fields is not None and schema is not None:
            raise ValueError("Provide either 'schema' or 'fields', not both")

        resolved_schema = schema or (build_elicitation_schema(fields) if fields else "")

        rid = self._pending.next_id()
        future = self._pending.create(rid)
        envelope = mcp_pb2.ServerEnvelope(
            request_id=rid,
            elicitation=mcp_pb2.ElicitationRequest(
                message=message,
                schema=resolved_schema,
            ),
        )
        await self._write_queue.put(envelope)
        raw: mcp_pb2.ElicitationResponse = await asyncio.wait_for(future, timeout=_DEFAULT_TIMEOUT)
        data: dict = {}
        if raw.content:
            try:
                parsed = json.loads(raw.content)
                if isinstance(parsed, dict):
                    data = parsed
            except (ValueError, TypeError):
                pass
        return ElicitationResult(action=raw.action, data=data)

    async def _log(self, level: str, message: str, extra: dict | None) -> None:
        await self._write_queue.put(
            mcp_pb2.ServerEnvelope(
                request_id=0,
                notification=mcp_pb2.ServerNotification(
                    type=mcp_pb2.ServerNotification.LOG,
                    payload=json.dumps({"level": level, "message": message, "extra": extra}),
                ),
            )
        )

    async def info(self, message: str, extra: dict | None = None) -> None:
        """Send an info-level log to this session's client."""
        await self._log("info", message, extra)

    async def debug(self, message: str, extra: dict | None = None) -> None:
        """Send a debug-level log to this session's client."""
        await self._log("debug", message, extra)

    async def warning(self, message: str, extra: dict | None = None) -> None:
        """Send a warning-level log to this session's client."""
        await self._log("warning", message, extra)

    async def error(self, message: str, extra: dict | None = None) -> None:
        """Send an error-level log to this session's client."""
        await self._log("error", message, extra)

    async def report_progress(self, progress: float, total: float | None = None) -> None:
        """Report progress to this session's client."""
        await self._write_queue.put(
            mcp_pb2.ServerEnvelope(
                request_id=0,
                notification=mcp_pb2.ServerNotification(
                    type=mcp_pb2.ServerNotification.PROGRESS,
                    payload=json.dumps({"progress": progress, "total": total}),
                ),
            )
        )

    async def list_roots(self) -> mcp_pb2.ListRootsResponse:
        """Request the client's registered root URIs."""
        if not self._capabilities.roots:
            raise McpError(400, "Client does not support roots")
        rid = self._pending.next_id()
        future = self._pending.create(rid)
        await self._write_queue.put(
            mcp_pb2.ServerEnvelope(
                request_id=rid,
                roots_request=mcp_pb2.ListRootsRequest(),
            )
        )
        return await asyncio.wait_for(future, timeout=_DEFAULT_TIMEOUT)
