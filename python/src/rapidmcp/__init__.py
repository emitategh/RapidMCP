"""rapidmcp: gRPC-native MCP (Model Context Protocol) library."""

from rapidmcp._version import __version__
from rapidmcp.auth import ClientTLSConfig, TLSConfig
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
from rapidmcp.icons import Icon
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
    ElicitRequestInfo,
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
    "ClientTLSConfig",
    "CompleteResult",
    "ContentItem",
    "Context",
    "ElicitRequestInfo",
    "ElicitationField",
    "ElicitationResult",
    "EnumField",
    "FloatField",
    "GetPromptResult",
    "Icon",
    "Image",
    "IntField",
    "ListResult",
    "LoggingMiddleware",
    "McpError",
    "Middleware",
    "Prompt",
    "PromptArgument",
    "PromptMessage",
    "RapidMCP",
    "ReadResourceResult",
    "Resource",
    "ResourceTemplate",
    "ServerInfo",
    "StringField",
    "TLSConfig",
    "TimeoutMiddleware",
    "TimingMiddleware",
    "Tool",
    "ToolAnnotationInfo",
    "ToolAnnotations",
    "ToolCallContext",
    "ToolError",
    "ValidationMiddleware",
    "__version__",
    "build_elicitation_schema",
]
