"""fastermcp: gRPC-native MCP (Model Context Protocol) library."""

__version__ = "0.1.0"

from fastermcp.client import Client
from fastermcp.content import Audio, Image
from fastermcp.context import Context
from fastermcp.elicitation import (
    BoolField,
    ElicitationField,
    ElicitationResult,
    EnumField,
    FloatField,
    IntField,
    StringField,
    build_elicitation_schema,
)
from fastermcp.errors import McpError, ToolError
from fastermcp.middleware import (
    LoggingMiddleware,
    Middleware,
    TimeoutMiddleware,
    TimingMiddleware,
    ToolCallContext,
    ValidationMiddleware,
)
from fastermcp.server import FasterMCP
from fastermcp.tools import ToolAnnotations
from fastermcp.types import (
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
    "FasterMCP",
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
