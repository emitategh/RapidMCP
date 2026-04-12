"""Docker-based stress tests.

These tests run the gRPC server inside a Docker container so client and server
have completely separate processes, event loops, and grpc.aio thread pools,
communicating over real TCP sockets.

This catches deadlock classes that same-process tests miss:
  - TCP backpressure: client receive buffer fills while server writes
  - grpc.aio thread races: each side has its own C completion queue
  - Sampling round-trip under real network latency / buffering

Requirements:
  - Docker daemon running and `docker` CLI on PATH
  - Image `fastermcp-test-server` built:
      cd python && docker build -t fastermcp-test-server .

Each test wraps the client side in asyncio.timeout() so a deadlock surfaces
as TimeoutError rather than a hung test runner. The container is removed in
teardown regardless of whether the test passed or failed.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import time

import pytest

from fastermcp import Client
from fastermcp._generated import mcp_pb2

IMAGE = "fastermcp-test-server"
CONTAINER_PORT = 50051


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _DockerServer:
    """Context manager: start a named server script in a Docker container.

    Usage::

        with _DockerServer("echo.py") as srv:
            # srv.port is the host port mapped to the container
            async with Client(f"localhost:{srv.port}") as client:
                ...

    The container is started with ``--rm`` so Docker removes it automatically
    on exit. We also call ``docker stop`` in __exit__ for an immediate kill.
    Readiness is detected by polling the TCP port until it accepts a
    connection, avoiding any shared-memory synchronisation primitives.
    """

    def __init__(self, server_script: str, *, startup_timeout: float = 15.0):
        self.port = _free_port()
        self._script = server_script
        self._startup_timeout = startup_timeout
        self._container_id: str | None = None

    def __enter__(self) -> "_DockerServer":
        result = subprocess.run(
            [
                "docker", "run",
                "--rm",             # auto-remove on stop
                "--detach",         # return container ID immediately
                "-p", f"{self.port}:{CONTAINER_PORT}",
                IMAGE,
                f"tests/servers/{self._script}", str(CONTAINER_PORT),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self._container_id = result.stdout.strip()
        self._wait_for_port()
        return self

    def __exit__(self, *_) -> None:
        if self._container_id:
            subprocess.run(
                ["docker", "stop", self._container_id],
                capture_output=True,
                check=False,        # don't raise if already stopped
            )
            self._container_id = None

    def _wait_for_port(self) -> None:
        """Poll localhost:port until gRPC is ready to accept connections.

        We need two passes: the first TCP success proves the port is bound,
        but grpc.aio may need a moment more to finish its HTTP/2 initialisation.
        A second successful connect after a short pause is a reliable signal
        that the server is fully ready.
        """
        deadline = time.monotonic() + self._startup_timeout
        passes = 0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    passes += 1
                    if passes >= 2:
                        return  # stable: two successful TCP connects
                    time.sleep(0.3)  # brief pause before second probe
                    continue
            except OSError:
                passes = 0
                time.sleep(0.1)
        raise RuntimeError(
            f"Docker server ({self._script}) did not accept connections "
            f"on port {self.port} within {self._startup_timeout}s"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docker_concurrent_tool_calls():
    """50 concurrent calls against a server running in Docker."""
    with _DockerServer("echo.py") as srv:
        async with asyncio.timeout(15):
            async with Client(f"localhost:{srv.port}") as client:
                results = await asyncio.gather(
                    *[client.call_tool("echo", {"text": str(i)}) for i in range(50)]
                )

    assert len(results) == 50
    texts = {r.content[0].text for r in results}
    assert texts == {str(i) for i in range(50)}


@pytest.mark.asyncio
async def test_docker_concurrent_clients():
    """20 clients connecting to a Docker server simultaneously."""
    with _DockerServer("echo.py") as srv:
        async def one_client(i: int) -> str:
            async with Client(f"localhost:{srv.port}") as client:
                r = await client.call_tool("echo", {"text": str(i)})
                return r.content[0].text

        async with asyncio.timeout(15):
            results = await asyncio.gather(*[one_client(i) for i in range(20)])

    assert sorted(results, key=int) == [str(i) for i in range(20)]


@pytest.mark.asyncio
async def test_docker_concurrent_sampling_no_deadlock():
    """10 concurrent sampling round-trips across the Docker network boundary.

    This is the highest-value Docker test: each sampling request leaves the
    test process, crosses the TCP socket into the container, the server awaits
    a future, the response travels back over the socket to the client's reader
    loop (a different asyncio task), the handler runs, and the reply is sent
    back — all while 9 other tool tasks are simultaneously waiting for their
    own sampling futures.  A deadlock here would surface as a TimeoutError.
    """
    async def sampling_handler(req: mcp_pb2.SamplingRequest) -> mcp_pb2.SamplingResponse:
        await asyncio.sleep(0.01)
        text = req.messages[0].content[0].text
        return mcp_pb2.SamplingResponse(
            role="assistant",
            content=[mcp_pb2.ContentItem(type="text", text=f"reply:{text}")],
        )

    with _DockerServer("sampling.py") as srv:
        async with asyncio.timeout(15):
            client = Client(f"localhost:{srv.port}")
            client.set_sampling_handler(sampling_handler)
            await client.connect()
            try:
                results = await asyncio.gather(
                    *[
                        client.call_tool("do_sample", {"n": str(i)})
                        for i in range(10)
                    ]
                )
            finally:
                await client.close()

    assert len(results) == 10
    for r in results:
        assert not r.is_error
        assert r.content[0].text.startswith("reply:msg ")


@pytest.mark.asyncio
async def test_docker_fast_not_blocked_by_slow():
    """Fast tool must complete well before the slow tool — real TCP, Docker."""
    with _DockerServer("mixed.py") as srv:
        async with asyncio.timeout(10):
            async with Client(f"localhost:{srv.port}") as client:
                slow_task = asyncio.create_task(
                    client.call_tool("slow", {"x": "slow"})
                )
                await asyncio.sleep(0.05)

                t0 = time.monotonic()
                fast_result = await client.call_tool("fast", {"x": "fast"})
                elapsed = time.monotonic() - t0

                slow_result = await slow_task

    assert fast_result.content[0].text == "fast"
    assert slow_result.content[0].text == "slow"
    assert elapsed < 0.3, f"fast tool took {elapsed:.3f}s — blocked by slow tool"


@pytest.mark.asyncio
async def test_docker_pending_requests_no_leak():
    """200 sequential calls — PendingRequests must be empty after all calls."""
    with _DockerServer("echo.py") as srv:
        async with asyncio.timeout(30):
            async with Client(f"localhost:{srv.port}") as client:
                for i in range(200):
                    r = await client.call_tool("echo", {"text": str(i)})
                    assert r.content[0].text == str(i)
                assert len(client._pending._pending) == 0
