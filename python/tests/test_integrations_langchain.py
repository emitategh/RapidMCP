"""Unit tests for the LangChain integration — mock the Client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("langchain_core")


def test_rapidmcp_client_importable():
    from rapidmcp.integrations.langchain import RapidMCPClient  # noqa: F401


def test_rapidmcp_client_accepts_multi_server_dict():
    from rapidmcp.auth import ClientTLSConfig
    from rapidmcp.integrations.langchain import RapidMCPClient

    tls = ClientTLSConfig(ca="ca.crt")
    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        client = RapidMCPClient(
            {
                "a": {"address": "host-a:50051", "token": "tok-a"},
                "b": {"address": "host-b:50051", "tls": tls},
            }
        )
        assert set(client.servers) == {"a", "b"}
        assert MockClient.call_args_list[0].args == ("host-a:50051",)
        assert MockClient.call_args_list[0].kwargs == {"token": "tok-a", "tls": None}
        assert MockClient.call_args_list[1].args == ("host-b:50051",)
        assert MockClient.call_args_list[1].kwargs == {"token": None, "tls": tls}


def test_rapidmcp_client_rejects_empty_dict():
    from rapidmcp.integrations.langchain import RapidMCPClient

    with pytest.raises(ValueError, match="at least one server"):
        RapidMCPClient({})


@pytest.mark.asyncio
async def test_rapidmcp_client_connect_closes_all_servers():
    from rapidmcp.integrations.langchain import RapidMCPClient

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()

        rc = RapidMCPClient({"a": {"address": "a:1"}, "b": {"address": "b:1"}})
        await rc.connect()
        await rc.close()

        assert MockClient.return_value.connect.await_count == 2
        assert MockClient.return_value.close.await_count == 2


@pytest.mark.asyncio
async def test_rapidmcp_client_works_as_async_context_manager():
    from rapidmcp.integrations.langchain import RapidMCPClient

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()

        async with RapidMCPClient({"a": {"address": "a:1"}}) as rc:
            assert rc.servers == ["a"]

        MockClient.return_value.connect.assert_awaited_once()
        MockClient.return_value.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rapidmcp_client_get_tools_aggregates_servers():
    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ListResult, Tool

    def make_list(names):
        return ListResult(
            items=[Tool(name=n, description="", input_schema={"type": "object"}) for n in names],
            next_cursor=None,
        )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        instances = []

        def make_instance(*a, **kw):
            inst = AsyncMock()
            inst.connect = AsyncMock()
            inst.close = AsyncMock()
            inst.list_tools = AsyncMock(
                return_value=make_list(["alpha"] if not instances else ["beta", "gamma"])
            )
            instances.append(inst)
            return inst

        MockClient.side_effect = make_instance

        rc = RapidMCPClient({"a": {"address": "a:1"}, "b": {"address": "b:1"}})
        tools = await rc.get_tools()

        assert sorted(t.name for t in tools) == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_rapidmcp_client_get_tools_respects_allowed_tools_per_server():
    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ListResult, Tool

    listing = ListResult(
        items=[
            Tool(name="keep", description="", input_schema={"type": "object"}),
            Tool(name="drop", description="", input_schema={"type": "object"}),
        ],
        next_cursor=None,
    )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.list_tools = AsyncMock(return_value=listing)

        rc = RapidMCPClient({"a": {"address": "a:1", "allowed_tools": ["keep"]}})
        tools = await rc.get_tools()
        assert [t.name for t in tools] == ["keep"]


@pytest.mark.asyncio
async def test_rapidmcp_client_get_tools_single_server_filter():
    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ListResult, Tool

    def listing(names):
        return ListResult(
            items=[Tool(name=n, description="", input_schema={"type": "object"}) for n in names],
            next_cursor=None,
        )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        instances = []

        def make_instance(*a, **kw):
            inst = AsyncMock()
            inst.connect = AsyncMock()
            inst.close = AsyncMock()
            inst.list_tools = AsyncMock(return_value=listing(["x"] if not instances else ["y"]))
            instances.append(inst)
            return inst

        MockClient.side_effect = make_instance

        rc = RapidMCPClient({"a": {"address": "a:1"}, "b": {"address": "b:1"}})
        tools = await rc.get_tools(server_name="a")
        assert [t.name for t in tools] == ["x"]


@pytest.mark.asyncio
async def test_rapidmcp_client_get_resources_reads_listed_uris():
    from langchain_core.document_loaders.blob_loaders import Blob

    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import (
        ContentItem,
        ListResult,
        ReadResourceResult,
        Resource,
    )

    listing = ListResult(
        items=[
            Resource(uri="file:///a.txt", name="a", mime_type="text/plain"),
            Resource(uri="file:///b.bin", name="b", mime_type="application/octet-stream"),
        ],
        next_cursor=None,
    )

    def fake_read(uri: str) -> ReadResourceResult:
        if uri == "file:///a.txt":
            return ReadResourceResult(
                content=[ContentItem(type="text", text="hello", mime_type="text/plain")]
            )
        return ReadResourceResult(
            content=[
                ContentItem(
                    type="resource", data=b"\x00\x01\x02", mime_type="application/octet-stream"
                )
            ]
        )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.list_resources = AsyncMock(return_value=listing)
        MockClient.return_value.read_resource = AsyncMock(side_effect=fake_read)

        rc = RapidMCPClient({"a": {"address": "a:1"}})
        blobs = await rc.get_resources("a")

        assert len(blobs) == 2
        assert all(isinstance(b, Blob) for b in blobs)
        assert blobs[0].as_string() == "hello"
        assert blobs[0].mimetype == "text/plain"
        assert blobs[0].metadata == {"uri": "file:///a.txt"}
        assert blobs[1].as_bytes() == b"\x00\x01\x02"


@pytest.mark.asyncio
async def test_rapidmcp_client_get_resources_filters_by_uris():
    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ContentItem, ReadResourceResult

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.read_resource = AsyncMock(
            return_value=ReadResourceResult(content=[ContentItem(type="text", text="x")])
        )

        rc = RapidMCPClient({"a": {"address": "a:1"}})
        blobs = await rc.get_resources("a", uris=["file:///one"])

        assert len(blobs) == 1
        MockClient.return_value.list_resources.assert_not_called()
        MockClient.return_value.read_resource.assert_awaited_once_with("file:///one")


@pytest.mark.asyncio
async def test_rapidmcp_client_get_prompt_returns_messages():
    from langchain_core.messages import AIMessage, HumanMessage

    from rapidmcp.integrations.langchain import RapidMCPClient
    from rapidmcp.types import ContentItem, GetPromptResult, PromptMessage

    result = GetPromptResult(
        messages=[
            PromptMessage(role="user", content=ContentItem(type="text", text="hi")),
            PromptMessage(role="assistant", content=ContentItem(type="text", text="hello")),
        ]
    )

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.get_prompt = AsyncMock(return_value=result)

        rc = RapidMCPClient({"a": {"address": "a:1"}})
        msgs = await rc.get_prompt("a", "greet", arguments={"name": "Ada"})

        assert len(msgs) == 2
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].content == "hi"
        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].content == "hello"
        MockClient.return_value.get_prompt.assert_awaited_once_with("greet", {"name": "Ada"})


@pytest.mark.asyncio
async def test_rapidmcp_client_session_yields_underlying_client():
    from rapidmcp.integrations.langchain import RapidMCPClient

    with patch("rapidmcp.integrations.langchain.Client") as MockClient:
        MockClient.return_value.connect = AsyncMock()
        MockClient.return_value.close = AsyncMock()
        MockClient.return_value.ping = AsyncMock(return_value=True)

        rc = RapidMCPClient({"a": {"address": "a:1"}})
        async with rc.session("a") as sess:
            await sess.ping()

        MockClient.return_value.ping.assert_awaited_once()
