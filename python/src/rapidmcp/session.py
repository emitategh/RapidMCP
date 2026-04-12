"""Shared session primitives: request_id generation and pending-request tracking."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class PendingRequests:
    """Track in-flight outbound requests and correlate responses by request_id."""

    def __init__(self) -> None:
        self._counter: int = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}

    def next_id(self) -> int:
        self._counter += 1
        return self._counter

    def create(self, request_id: int) -> asyncio.Future[Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        return future

    def resolve(self, request_id: int, result: Any) -> None:
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(result)

    def reject(self, request_id: int, error: Exception) -> None:
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_exception(error)

    def cancel_all(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    def reject_all(self, error: Exception) -> None:
        """Reject all pending futures with the given exception."""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


class NotificationRegistry:
    """Registry for notification callbacks keyed by notification type name."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}

    def register(self, notification_type: str, handler: Callable) -> None:
        self._handlers.setdefault(notification_type, []).append(handler)

    async def dispatch(self, notification_type: str, payload: str) -> None:
        for handler in self._handlers.get(notification_type, []):
            result = handler(payload)
            if asyncio.iscoroutine(result):
                await result
