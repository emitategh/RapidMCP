"""Tests that token and tls params are correctly forwarded to Client in integrations."""

from unittest.mock import patch

import pytest

from rapidmcp.auth import ClientTLSConfig

# ---------------------------------------------------------------------------
# LiveKit
# ---------------------------------------------------------------------------


def test_mcp_server_grpc_forwards_token():
    """MCPServerGRPC(token=...) passes token to its internal Client."""
    try:
        from rapidmcp.integrations.livekit import MCPServerGRPC
    except ImportError:
        pytest.skip("livekit-agents not installed")

    with patch("rapidmcp.integrations.livekit.Client") as MockClient:
        MCPServerGRPC("host:50051", token="mytoken")
        MockClient.assert_called_once_with("host:50051", token="mytoken", tls=None)


def test_mcp_server_grpc_forwards_tls():
    """MCPServerGRPC(tls=...) passes tls to its internal Client."""
    try:
        from rapidmcp.integrations.livekit import MCPServerGRPC
    except ImportError:
        pytest.skip("livekit-agents not installed")

    tls = ClientTLSConfig(ca="ca.crt")
    with patch("rapidmcp.integrations.livekit.Client") as MockClient:
        MCPServerGRPC("host:50051", tls=tls)
        MockClient.assert_called_once_with("host:50051", token=None, tls=tls)


def test_mcp_server_grpc_forwards_token_and_tls():
    """MCPServerGRPC(token=..., tls=...) passes both to its internal Client."""
    try:
        from rapidmcp.integrations.livekit import MCPServerGRPC
    except ImportError:
        pytest.skip("livekit-agents not installed")

    tls = ClientTLSConfig(ca="ca.crt", cert="c.crt", key="c.key")
    with patch("rapidmcp.integrations.livekit.Client") as MockClient:
        MCPServerGRPC("host:50051", token="tok", tls=tls)
        MockClient.assert_called_once_with("host:50051", token="tok", tls=tls)


def test_mcp_server_grpc_no_auth_unchanged():
    """MCPServerGRPC() with no auth args passes token=None, tls=None (backward compat)."""
    try:
        from rapidmcp.integrations.livekit import MCPServerGRPC
    except ImportError:
        pytest.skip("livekit-agents not installed")

    with patch("rapidmcp.integrations.livekit.Client") as MockClient:
        MCPServerGRPC("host:50051")
        MockClient.assert_called_once_with("host:50051", token=None, tls=None)


# ---------------------------------------------------------------------------
# LangChain — RapidMCPClient multi-server
# ---------------------------------------------------------------------------


def test_rapidmcp_client_forwards_per_server_token_and_tls():
    try:
        from rapidmcp.integrations.langchain import RapidMCPClient
    except ImportError:
        pytest.skip("langchain-core not installed")

    tls = ClientTLSConfig(ca="ca.crt")
    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        RapidMCPClient(
            {
                "a": {"address": "host-a:50051", "token": "tok-a"},
                "b": {"address": "host-b:50051", "tls": tls},
            }
        )
        assert MockClient.call_args_list[0].args == ("host-a:50051",)
        assert MockClient.call_args_list[0].kwargs == {"token": "tok-a", "tls": None}
        assert MockClient.call_args_list[1].args == ("host-b:50051",)
        assert MockClient.call_args_list[1].kwargs == {"token": None, "tls": tls}
