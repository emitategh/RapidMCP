"""Authentication helpers: token interceptor and TLS credentials."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import grpc
from grpc import aio as grpc_aio

logger = logging.getLogger("rapidmcp.auth")


@dataclass
class TLSConfig:
    """Paths to TLS certificate material for the gRPC server.

    Pass ``ca`` to enable mutual TLS — the server will require clients to
    present a certificate signed by that CA.
    """

    cert: str
    key: str
    ca: str = ""


class _AuthInterceptor(grpc_aio.ServerInterceptor):
    """gRPC server interceptor that validates a bearer token.

    Reads the ``authorization`` metadata key, strips the optional
    ``Bearer `` prefix, and calls *verify*.  Aborts with UNAUTHENTICATED
    if *verify* returns False or raises.
    """

    def __init__(self, verify: Callable[[str], bool | Awaitable[bool]]) -> None:
        self._verify = verify

    async def _check_token(self, context: grpc_aio.ServicerContext) -> bool:
        metadata = dict(context.invocation_metadata())
        raw = metadata.get("authorization", "").strip()
        if raw.lower().startswith("bearer "):
            token = raw[7:].strip()
        else:
            token = raw.strip()
        try:
            ok = self._verify(token)
            if inspect.isawaitable(ok):
                ok = await ok
        except Exception:
            logger.warning("auth verify() raised unexpectedly", exc_info=True)
            ok = False
        return bool(ok)

    async def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        if handler is None:
            return handler

        # Wrap the actual handler function (stream_stream for bidi streaming)
        if handler.stream_stream is not None:
            original = handler.stream_stream

            async def auth_stream_stream(request_iterator, context):
                if not await self._check_token(context):
                    await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
                    return
                async for msg in original(request_iterator, context):
                    yield msg

            return handler._replace(stream_stream=auth_stream_stream)

        if handler.unary_unary is not None:
            original = handler.unary_unary

            async def auth_unary_unary(request, context):
                if not await self._check_token(context):
                    await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
                    return
                return await original(request, context)

            return handler._replace(unary_unary=auth_unary_unary)

        if handler.unary_stream is not None:
            # NOTE: RapidMCP has no unary_stream RPCs. If one is added,
            # this wrapper must become an async generator: `async for msg in original(...): yield msg`
            original = handler.unary_stream

            async def auth_unary_stream(request, context):
                if not await self._check_token(context):
                    await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
                    return
                return await original(request, context)

            return handler._replace(unary_stream=auth_unary_stream)

        if handler.stream_unary is not None:
            # NOTE: RapidMCP has no stream_unary RPCs. Defensive implementation for completeness.
            original = handler.stream_unary

            async def auth_stream_unary(request_iterator, context):
                if not await self._check_token(context):
                    await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")
                    return
                return await original(request_iterator, context)

            return handler._replace(stream_unary=auth_stream_unary)

        return handler


def _build_server_credentials(tls: TLSConfig) -> grpc.ServerCredentials:
    """Build SSL server credentials from PEM file paths in *tls*."""
    with open(tls.cert, "rb") as f:
        cert_pem = f.read()
    with open(tls.key, "rb") as f:
        key_pem = f.read()
    ca_pem = None
    if tls.ca:
        with open(tls.ca, "rb") as f:
            ca_pem = f.read()
    return grpc.ssl_server_credentials(
        [(key_pem, cert_pem)],
        root_certificates=ca_pem,
        require_client_auth=bool(ca_pem),
    )


@dataclass
class ClientTLSConfig:
    """Paths to TLS certificate material for the gRPC client.

    All fields are optional:

    - Leave all empty to use system CA bundle (verify server cert, no client cert).
    - Set ``ca`` to a custom CA PEM path for private/self-signed server certs.
    - Set ``ca`` + ``cert`` + ``key`` to enable mutual TLS (mTLS).
    """

    ca: str = ""  # CA cert PEM path — empty = use system CAs
    cert: str = ""  # client cert PEM path (mTLS only)
    key: str = ""  # client private key PEM path (mTLS only)


def _build_channel_credentials(tls: ClientTLSConfig) -> grpc.ChannelCredentials:
    """Build SSL channel credentials from PEM file paths in *tls*."""
    ca_pem = None
    if tls.ca:
        with open(tls.ca, "rb") as f:
            ca_pem = f.read()
    cert_pem = None
    key_pem = None
    if tls.cert:
        with open(tls.cert, "rb") as f:
            cert_pem = f.read()
    if tls.key:
        with open(tls.key, "rb") as f:
            key_pem = f.read()
    return grpc.ssl_channel_credentials(
        root_certificates=ca_pem,
        private_key=key_pem,
        certificate_chain=cert_pem,
    )
