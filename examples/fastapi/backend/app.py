"""FastAPI backend — LangChain agent powered by a RapidMCP gRPC tool server."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.requests import Request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MCP_ADDRESS = os.getenv("MCP_ADDRESS", "py-mcp-server:50051")

# ---------------------------------------------------------------------------
# Mock handlers for server-initiated requests
# ---------------------------------------------------------------------------


async def mock_sampling_handler(req):
    """Always responds with a canned summary so sampling tools work without a real LLM loop."""
    from rapidmcp._generated import mcp_pb2

    text_parts = [
        c.text
        for msg in req.messages
        for c in msg.content
        if c.text
    ]
    input_text = " ".join(text_parts).strip()
    return mcp_pb2.SamplingResponse(
        role="assistant",
        content=[mcp_pb2.ContentItem(type="text", text=f"Mock summary: {input_text[:120]}")],
        model="mock-model",
        stop_reason="end_turn",
    )


async def mock_elicitation_handler(req):
    """Always accepts elicitation with confirm=true."""
    from rapidmcp._generated import mcp_pb2

    return mcp_pb2.ElicitationResponse(
        action="accept",
        content=json.dumps({"confirm": True}),
    )


# ---------------------------------------------------------------------------
# App lifespan — connect to MCP server
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    from rapidmcp.integrations.langchain import RapidMCPClient

    rc = RapidMCPClient({"default": {"address": MCP_ADDRESS}})
    default_client = rc.client("default")
    # Handlers must be registered BEFORE connect() so ClientCapabilities are set correctly.
    default_client.set_sampling_handler(mock_sampling_handler)
    default_client.set_elicitation_handler(mock_elicitation_handler)

    # Retry loop: mcp-server container may not be gRPC-ready immediately after start.
    for attempt in range(1, 4):
        try:
            await rc.connect()
            logger.info("Connected to MCP server at %s", MCP_ADDRESS)
            break
        except Exception as exc:
            if attempt == 3:
                raise RuntimeError(f"Could not connect to MCP server after 3 attempts: {exc}") from exc
            logger.warning("MCP server not ready (attempt %d/3), retrying in 2s…", attempt)
            await asyncio.sleep(2)

    app.state.rc = rc
    yield
    await rc.close()
    logger.info("Disconnected from MCP server")


app = FastAPI(title="MCP Example Backend", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_rc(request: Request):
    return request.app.state.rc


def get_default_client(request: Request):
    return get_rc(request).client("default")


# ---------------------------------------------------------------------------
# Chat endpoint — LangChain agent with MCP tools
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    from langchain_anthropic import ChatAnthropic
    from langgraph.prebuilt import create_react_agent

    rc = get_rc(request)
    tools = await rc.get_tools(server_name="default")

    llm = ChatAnthropic(model="claude-sonnet-4-6", max_tokens=1024)
    agent = create_react_agent(
        llm,
        tools,
        prompt="You are a helpful assistant with access to MCP tools. Use them when appropriate.",
    )
    result = await agent.ainvoke(
        {"messages": [("user", req.message)]}
    )
    return ChatResponse(response=result["messages"][-1].content)


# ---------------------------------------------------------------------------
# Tool endpoints
# ---------------------------------------------------------------------------


@app.get("/tools")
async def list_tools(request: Request):
    client = get_default_client(request)
    result = await client.list_tools()
    return {
        "tools": [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in result.items
        ]
    }


# ---------------------------------------------------------------------------
# Resource endpoints
# ---------------------------------------------------------------------------


@app.get("/resources")
async def list_resources(request: Request):
    client = get_default_client(request)
    result = await client.list_resources()
    templates = await client.list_resource_templates()
    return {
        "resources": [
            {"uri": r.uri, "name": r.name, "description": r.description}
            for r in result.items
        ],
        "templates": [
            {"uri_template": t.uri_template, "name": t.name, "description": t.description}
            for t in templates.items
        ],
    }


@app.get("/resources/{uri:path}")
async def read_resource(uri: str, request: Request):
    client = get_default_client(request)
    try:
        result = await client.read_resource(uri)
        return {
            "uri": uri,
            "content": [
                {"type": c.type, "text": c.text, "mime_type": c.mime_type}
                for c in result.content
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Prompt endpoints
# ---------------------------------------------------------------------------


@app.get("/prompts")
async def list_prompts(request: Request):
    client = get_default_client(request)
    result = await client.list_prompts()
    return {
        "prompts": [
            {
                "name": p.name,
                "description": p.description,
                "arguments": [
                    {"name": a.name, "description": a.description, "required": a.required}
                    for a in p.arguments
                ],
            }
            for p in result.items
        ]
    }


@app.get("/prompts/{name}")
async def get_prompt(name: str, request: Request):
    """Render a prompt. Pass prompt arguments as query parameters."""
    client = get_default_client(request)
    args = dict(request.query_params)
    try:
        result = await client.get_prompt(name, arguments=args)
        return {
            "name": name,
            "messages": [{"role": m.role, "text": m.content.text} for m in result.messages],
        }
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health(request: Request):
    client = get_default_client(request)
    try:
        await client.ping()
        return {"status": "ok", "mcp_server": MCP_ADDRESS}
    except Exception as exc:
        return {"status": "degraded", "mcp_server": MCP_ADDRESS, "error": str(exc)}
