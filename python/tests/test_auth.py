import grpc
import pytest

from rapidmcp import Client, RapidMCP
from rapidmcp.auth import TLSConfig


def test_tls_config_required_fields():
    cfg = TLSConfig(cert="server.crt", key="server.key")
    assert cfg.cert == "server.crt"
    assert cfg.key == "server.key"
    assert cfg.ca == ""


def test_tls_config_with_ca():
    cfg = TLSConfig(cert="s.crt", key="s.key", ca="ca.crt")
    assert cfg.ca == "ca.crt"


@pytest.fixture
async def auth_server():
    server = RapidMCP(
        name="auth-test",
        version="0.1",
        auth=lambda token: token == "secret",
    )

    @server.tool(description="noop")
    async def noop() -> str:
        return "ok"

    async with server:
        yield server


@pytest.mark.asyncio
async def test_auth_accepts_valid_token(auth_server):
    """A client presenting the correct token can list tools."""
    async with Client(f"localhost:{auth_server.port}", token="secret") as client:
        result = await client.list_tools()
        assert len(result.items) == 1
        assert result.items[0].name == "noop"


@pytest.mark.asyncio
async def test_auth_rejects_wrong_token(auth_server):
    """A client with a wrong token gets UNAUTHENTICATED."""
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        async with Client(f"localhost:{auth_server.port}", token="wrong") as client:
            await client.list_tools()
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_auth_rejects_missing_token(auth_server):
    """A client with no token gets UNAUTHENTICATED."""
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        async with Client(f"localhost:{auth_server.port}") as client:
            await client.list_tools()
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_auth_async_verify():
    """An async verify callable is awaited and respected."""

    async def verify(token: str) -> bool:
        return token == "async-secret"

    server = RapidMCP(name="s", version="1.0", auth=verify)

    @server.tool(description="noop")
    async def noop() -> str:
        return "ok"

    async with server:
        async with Client(f"localhost:{server.port}", token="async-secret") as client:
            result = await client.list_tools()
            assert len(result.items) == 1


@pytest.mark.asyncio
async def test_auth_verify_raises_treated_as_rejected():
    """An exception in verify aborts the call (no server crash)."""

    def verify(token: str) -> bool:
        raise RuntimeError("db is down")

    server = RapidMCP(name="s", version="1.0", auth=verify)

    @server.tool(description="noop")
    async def noop() -> str:
        return "ok"

    async with server:
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            async with Client(f"localhost:{server.port}", token="any") as client:
                await client.list_tools()
        assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_no_auth_backward_compat():
    """A server with auth=None accepts all connections (existing behaviour)."""
    server = RapidMCP(name="s", version="1.0")

    @server.tool(description="noop")
    async def noop() -> str:
        return "ok"

    async with server:
        async with Client(f"localhost:{server.port}") as client:
            result = await client.list_tools()
            assert len(result.items) == 1


def test_tls_config_importable_from_rapidmcp():
    from rapidmcp import TLSConfig  # noqa: F401
