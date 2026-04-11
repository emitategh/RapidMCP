"""mcp-grpc: gRPC-native tool-calling protocol inspired by MCP."""

__version__ = "0.1.0"

from mcp_grpc.client import Client, ListResult
from mcp_grpc.content import Audio, Image
from mcp_grpc.elicitation import (
    BoolField,
    ElicitationField,
    ElicitationResult,
    EnumField,
    FloatField,
    IntField,
    StringField,
    build_elicitation_schema,
)
from mcp_grpc.errors import McpError, ToolError
from mcp_grpc.middleware import (
    LoggingMiddleware,
    Middleware,
    TimeoutMiddleware,
    TimingMiddleware,
    ToolCallContext,
    ValidationMiddleware,
)
from mcp_grpc.server import Context, FasterMCP, ToolAnnotations

__all__ = [
    "Audio",
    "BoolField",
    "Client",
    "Context",
    "ElicitationField",
    "ElicitationResult",
    "EnumField",
    "FasterMCP",
    "FloatField",
    "Image",
    "IntField",
    "ListResult",
    "LoggingMiddleware",
    "McpError",
    "Middleware",
    "StringField",
    "TimeoutMiddleware",
    "TimingMiddleware",
    "ToolAnnotations",
    "ToolCallContext",
    "ToolError",
    "ValidationMiddleware",
    "build_elicitation_schema",
]
