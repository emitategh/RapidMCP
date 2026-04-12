"""Docker-based TLS integration tests.

Generates a PKI (CA + server cert + client cert) on the host, mounts the cert
directory read-only into Docker containers, and verifies TLS handshakes over a
real TCP network boundary.

Requirements:
  - Docker daemon running and `docker` CLI on PATH
  - Image `rapidmcp-test-server` built: cd python && docker build -t rapidmcp-test-server .
  - cryptography package installed: cd python && uv sync --extra dev
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import grpc
import pytest

cryptography = pytest.importorskip("cryptography", reason="cryptography not installed")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from rapidmcp import Client  # noqa: E402
from rapidmcp.auth import ClientTLSConfig  # noqa: E402

IMAGE = "rapidmcp-test-server"
CONTAINER_PORT = 50051


# ---------------------------------------------------------------------------
# PKI helpers
# ---------------------------------------------------------------------------


def _make_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_key(key: rsa.RSAPrivateKey, path: Path) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _write_cert(cert: x509.Certificate, path: Path) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _make_ca(key: rsa.RSAPrivateKey) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )


def _make_server_cert(
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    server_key: rsa.RSAPrivateKey,
) -> x509.Certificate:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def _make_client_cert(
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    client_key: rsa.RSAPrivateKey,
) -> x509.Certificate:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-client")]))
        .issuer_name(ca_cert.subject)
        .public_key(client_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(ca_key, hashes.SHA256())
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class PKI:
    cert_dir: str  # host path — mounted as /certs in Docker
    ca_cert: str  # host path to CA cert PEM
    server_cert: str  # host path to server cert PEM
    server_key: str  # host path to server key PEM
    client_cert: str  # host path to client cert PEM
    client_key: str  # host path to client key PEM
    wrong_ca_cert: str  # host path to an untrusted CA cert PEM


@pytest.fixture(scope="session")
def pki(tmp_path_factory) -> PKI:
    """Generate a full PKI into a session-scoped temp dir."""
    d = tmp_path_factory.mktemp("pki")

    ca_key = _make_key()
    ca_cert = _make_ca(ca_key)
    _write_key(ca_key, d / "ca.key")
    _write_cert(ca_cert, d / "ca.crt")

    server_key = _make_key()
    server_cert = _make_server_cert(ca_key, ca_cert, server_key)
    _write_key(server_key, d / "server.key")
    _write_cert(server_cert, d / "server.crt")

    client_key = _make_key()
    client_cert = _make_client_cert(ca_key, ca_cert, client_key)
    _write_key(client_key, d / "client.key")
    _write_cert(client_cert, d / "client.crt")

    wrong_ca_key = _make_key()
    wrong_ca_cert = _make_ca(wrong_ca_key)
    _write_cert(wrong_ca_cert, d / "wrong_ca.crt")

    return PKI(
        cert_dir=str(d),
        ca_cert=str(d / "ca.crt"),
        server_cert=str(d / "server.crt"),
        server_key=str(d / "server.key"),
        client_cert=str(d / "client.crt"),
        client_key=str(d / "client.key"),
        wrong_ca_cert=str(d / "wrong_ca.crt"),
    )


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=5)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker daemon not available")


class _DockerTLSServer:
    """Start a TLS server script in Docker with the PKI cert dir mounted at /certs.

    The server script always receives:
      tests/servers/<script> <port> /certs/server.crt /certs/server.key [extra_args...]

    Pass extra_args to enable mTLS or token auth:
      []                        -> server-only TLS
      ["/certs/ca.crt"]         -> mTLS  (tls_echo.py reads 4th arg as CA)
      ["secret"]                -> token (tls_auth_echo.py reads 4th arg as expected token)

    Usage::

        with _DockerTLSServer("tls_echo.py", pki, []) as srv:
            async with Client(f"localhost:{srv.port}", tls=...) as client:
                ...
    """

    def __init__(
        self,
        server_script: str,
        pki: PKI,
        extra_args: list[str],
        *,
        startup_timeout: float = 15.0,
    ):
        self.port = _free_port()
        self._script = server_script
        self._pki = pki
        self._extra_args = extra_args
        self._startup_timeout = startup_timeout
        self._container_id: str | None = None

    def __enter__(self) -> _DockerTLSServer:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--detach",
                "-p",
                f"{self.port}:{CONTAINER_PORT}",
                "-v",
                f"{self._pki.cert_dir}:/certs:ro",
                IMAGE,
                f"tests/servers/{self._script}",
                str(CONTAINER_PORT),
                "/certs/server.crt",
                "/certs/server.key",
                *self._extra_args,
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
                check=False,
            )
            self._container_id = None

    def _wait_for_port(self) -> None:
        """Poll until the port accepts two TCP connections (same logic as _DockerServer)."""
        deadline = time.monotonic() + self._startup_timeout
        passes = 0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    passes += 1
                    if passes >= 2:
                        return
                    time.sleep(0.3)
                    continue
            except OSError:
                passes = 0
                time.sleep(0.1)
        raise RuntimeError(
            f"Docker TLS server ({self._script}) did not accept connections "
            f"on port {self.port} within {self._startup_timeout}s"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docker_tls_client_connects(pki):
    """Client with matching CA cert connects to TLS server in Docker and calls a tool."""
    with _DockerTLSServer("tls_echo.py", pki, []) as srv:
        tls = ClientTLSConfig(ca=pki.ca_cert)
        async with asyncio.timeout(15):
            async with Client(f"localhost:{srv.port}", tls=tls) as client:
                result = await client.call_tool("echo", {"text": "hi"})
    assert result.content[0].text == "hi"


@pytest.mark.asyncio
async def test_docker_tls_wrong_ca_rejected(pki):
    """Client using a wrong CA cannot verify the server cert — connection fails."""
    with _DockerTLSServer("tls_echo.py", pki, []) as srv:
        tls = ClientTLSConfig(ca=pki.wrong_ca_cert)
        with pytest.raises(grpc.aio.AioRpcError):
            async with asyncio.timeout(15):
                async with Client(f"localhost:{srv.port}", tls=tls) as client:
                    await client.call_tool("echo", {"text": "hi"})


@pytest.mark.asyncio
async def test_docker_tls_insecure_client_rejected(pki):
    """Plain insecure client cannot connect to a TLS-only server."""
    with _DockerTLSServer("tls_echo.py", pki, []) as srv:
        with pytest.raises(grpc.aio.AioRpcError):
            async with asyncio.timeout(15):
                async with Client(f"localhost:{srv.port}") as client:
                    await client.call_tool("echo", {"text": "hi"})


@pytest.mark.asyncio
async def test_docker_mtls_valid_client_cert_accepted(pki):
    """Client with CA + client cert+key connects to mTLS server."""
    with _DockerTLSServer("tls_echo.py", pki, ["/certs/ca.crt"]) as srv:
        tls = ClientTLSConfig(ca=pki.ca_cert, cert=pki.client_cert, key=pki.client_key)
        async with asyncio.timeout(15):
            async with Client(f"localhost:{srv.port}", tls=tls) as client:
                result = await client.call_tool("echo", {"text": "mtls"})
    assert result.content[0].text == "mtls"


@pytest.mark.asyncio
async def test_docker_mtls_no_client_cert_rejected(pki):
    """Client without a client cert is rejected by mTLS server."""
    with _DockerTLSServer("tls_echo.py", pki, ["/certs/ca.crt"]) as srv:
        tls = ClientTLSConfig(ca=pki.ca_cert)  # CA only — no cert/key
        with pytest.raises(grpc.aio.AioRpcError):
            async with asyncio.timeout(15):
                async with Client(f"localhost:{srv.port}", tls=tls) as client:
                    await client.call_tool("echo", {"text": "hi"})


@pytest.mark.asyncio
async def test_docker_tls_and_token_auth(pki):
    """TLS + token auth: correct token succeeds; wrong token gets UNAUTHENTICATED."""
    with _DockerTLSServer("tls_auth_echo.py", pki, ["secret"]) as srv:
        tls = ClientTLSConfig(ca=pki.ca_cert)

        # Correct token — should succeed
        async with asyncio.timeout(15):
            async with Client(f"localhost:{srv.port}", tls=tls, token="secret") as client:
                result = await client.call_tool("echo", {"text": "hi"})
        assert result.content[0].text == "hi"

        # Wrong token — should get UNAUTHENTICATED (TLS handshake succeeds; token rejected)
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            async with asyncio.timeout(15):
                async with Client(f"localhost:{srv.port}", tls=tls, token="wrong") as client:
                    await client.call_tool("echo", {"text": "hi"})
        assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED
