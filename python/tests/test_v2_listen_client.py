"""on_notification / subscribe_resource behave the same on both protocol versions."""

import asyncio
import json

import pytest

from rapidmcp import Client, RapidMCP


@pytest.fixture
async def server():
    srv = RapidMCP(name="listen", version="1.0")
    async with srv:
        yield srv


async def _until(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition never became true"
        await asyncio.sleep(0.02)


@pytest.mark.parametrize("mode", ["legacy", "modern"])
async def test_list_changed_notifications_reach_handlers(server, mode):
    client = Client(f"localhost:{server.port}", mode=mode)
    seen: list[tuple[str, str]] = []
    for kind in ("tools_list_changed", "prompts_list_changed", "resources_list_changed"):
        client.on_notification(kind, lambda payload, kind=kind: seen.append((kind, payload)))

    async with client:
        await client.ping()  # on v1 this proves the session is registered for broadcasts
        server.notify_tools_list_changed()
        server.notify_prompts_list_changed()
        server.notify_resources_list_changed()
        await _until(lambda: len(seen) == 3)

    assert seen == [
        ("tools_list_changed", ""),
        ("prompts_list_changed", ""),
        ("resources_list_changed", ""),
    ]


@pytest.mark.parametrize("mode", ["legacy", "modern"])
async def test_subscribed_resource_updates_reach_the_handler(server, mode):
    client = Client(f"localhost:{server.port}", mode=mode)
    updates: list[dict] = []
    client.on_notification("resource_updated", lambda payload: updates.append(json.loads(payload)))

    async with client:
        await client.subscribe_resource("res://mine")
        await client.ping()
        server.notify_resource_updated("res://mine")
        await _until(lambda: len(updates) == 1)

    assert updates == [{"uri": "res://mine"}]


async def test_modern_client_hears_only_about_uris_it_subscribed_to(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    updates: list[str] = []
    client.on_notification(
        "resource_updated", lambda payload: updates.append(json.loads(payload)["uri"])
    )

    async with client:
        await client.subscribe_resource("res://mine")
        server.notify_resource_updated("res://other")
        server.notify_resource_updated("res://mine")
        await _until(lambda: len(updates) == 1)

    assert updates == ["res://mine"]


async def test_modern_client_without_handlers_opens_no_stream(server):
    async with Client(f"localhost:{server.port}", mode="modern") as client:
        await client.ping()

        assert len(server._v2_listeners) == 0


async def test_a_handler_registered_after_connecting_starts_receiving(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    seen: list[str] = []

    async with client:
        client.on_notification("tools_list_changed", lambda payload: seen.append("tools"))
        await _until(lambda: len(server._v2_listeners) == 1)
        server.notify_tools_list_changed()
        await _until(lambda: seen == ["tools"])


async def test_closing_the_client_ends_its_subscription(server):
    client = Client(f"localhost:{server.port}", mode="modern")
    client.on_notification("tools_list_changed", lambda payload: None)

    async with client:
        assert len(server._v2_listeners) == 1

    await _until(lambda: len(server._v2_listeners) == 0)
