"""mcp-grpc: gRPC-native tool-calling protocol inspired by MCP."""
from mcp_grpc.client import McpClient
from mcp_grpc.errors import McpError, ToolError
from mcp_grpc.server import McpServer

__all__ = ["McpServer", "McpClient", "McpError", "ToolError"]
