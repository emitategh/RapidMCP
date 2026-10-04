"""Open v2 Listen streams and what each one asked for.

The server holds a listener only while its stream is open; nothing about a
subscription survives the stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from rapidmcp._generated import mcp_v2_pb2 as pb


@dataclass(eq=False)
class _Listener:
    notifications: pb.NotificationFilter
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


class _Listeners:
    def __init__(self) -> None:
        self._open: list[_Listener] = []

    def __len__(self) -> int:
        return len(self._open)

    def add(self, notifications: pb.NotificationFilter) -> _Listener:
        listener = _Listener(notifications)
        self._open.append(listener)
        return listener

    def remove(self, listener: _Listener) -> None:
        if listener in self._open:
            self._open.remove(listener)

    def _publish(
        self, event: pb.ListenEvent, wanted: Callable[[pb.NotificationFilter], bool]
    ) -> None:
        for listener in self._open:
            if wanted(listener.notifications):
                listener.queue.put_nowait(event)

    def tools_list_changed(self) -> None:
        self._publish(
            pb.ListenEvent(tools_list_changed=pb.ToolsListChanged()),
            lambda wanted: wanted.tools_list_changed,
        )

    def prompts_list_changed(self) -> None:
        self._publish(
            pb.ListenEvent(prompts_list_changed=pb.PromptsListChanged()),
            lambda wanted: wanted.prompts_list_changed,
        )

    def resources_list_changed(self) -> None:
        self._publish(
            pb.ListenEvent(resources_list_changed=pb.ResourcesListChanged()),
            lambda wanted: wanted.resources_list_changed,
        )

    def resource_updated(self, uri: str) -> None:
        self._publish(
            pb.ListenEvent(resource_updated=pb.ResourceUpdated(uri=uri)),
            lambda wanted: uri in wanted.resource_subscriptions,
        )
