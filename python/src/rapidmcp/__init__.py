"""rapidmcp: gRPC-native MCP (Model Context Protocol) library."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("rapidmcp")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

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
    "ClientTLSConfig",
    "CompleteResult",
    "ContentItem",
    "Context",
    "ElicitationField",
    "ElicitationResult",
    "EnumField",
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
    "build_elicitation_schema",
]
