"""rapidmcp: gRPC-native MCP (Model Context Protocol) library."""

__version__ = "0.1.0"

from rapidmcp.client import Client
from rapidmcp.content import Audio, Image
from rapidmcp.context import Context
from rapidmcp.elicitation import (
    BoolField,
    ElicitationField,
    ElicitationResult,
    EnumField,
    FloatField,
    IntField,
    StringField,
    build_elicitation_schema,
)
from rapidmcp.errors import McpError, ToolError
from rapidmcp.middleware import (
    LoggingMiddleware,
    Middleware,
    TimeoutMiddleware,
    TimingMiddleware,
    ToolCallContext,
    ValidationMiddleware,
)
from rapidmcp.server import RapidMCP
from rapidmcp.tools import ToolAnnotations
from rapidmcp.types import (
    CallToolResult,
    CompleteResult,
    ContentItem,
    GetPromptResult,
    ListResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    ServerInfo,
    Tool,
    ToolAnnotationInfo,
)

__all__ = [
    "Audio",
    "BoolField",
    "CallToolResult",
    "Client",
    "CompleteResult",
    "ContentItem",
    "Context",
    "ElicitationField",
    "ElicitationResult",
    "EnumField",
    "RapidMCP",
    "FloatField",
    "GetPromptResult",
    "Image",
    "IntField",
    "ListResult",
    "LoggingMiddleware",
    "McpError",
    "Middleware",
    "Prompt",
    "PromptArgument",
    "PromptMessage",
    "ReadResourceResult",
    "Resource",
    "ResourceTemplate",
    "ServerInfo",
    "StringField",
    "TimeoutMiddleware",
    "TimingMiddleware",
    "Tool",
    "ToolAnnotationInfo",
    "ToolAnnotations",
    "ToolCallContext",
    "ToolError",
    "ValidationMiddleware",
    "build_elicitation_schema",
]
