"""Client connect/close bookkeeping."""

import pytest

from rapidmcp import Client, RapidMCP


@pytest.fixture
async def server():
    srv = RapidMCP(name="lifecycle", version="0.1")
    async with srv:
        yield srv


async def test_client_reconnects_after_explicit_close_inside_async_with(server):
    client = Client(f"localhost:{server.port}")
    async with client:
        await client.close()

    async with client:
        assert client.is_connected
        assert await client.ping()


async def test_failed_connect_does_not_leave_a_half_open_client():
    client = Client("localhost:1", request_timeout=0.5)  # nothing listens here

    with pytest.raises(Exception):  # noqa: B017 - the failure type is gRPC's; cleanup is the point
        await client.connect()

    assert not client.is_connected
