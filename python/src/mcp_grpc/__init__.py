"""mcp-grpc: gRPC-native tool-calling protocol inspired by MCP."""
from mcp_grpc.client import Client, ListResult
from mcp_grpc.errors import McpError, ToolError
from mcp_grpc.middleware import Middleware, ToolCallContext
from mcp_grpc.server import Context, FasterMCP

__all__ = [
    "FasterMCP",
    "Client",
    "Context",
    "McpError",
    "ToolError",
    "ListResult",
    "Middleware",
    "ToolCallContext",
]
