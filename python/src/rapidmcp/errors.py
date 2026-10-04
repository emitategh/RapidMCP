"""Error types for rapidmcp."""

# Codes sent between peers follow MCP / JSON-RPC 2.0.
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602  # also "no such tool / resource / prompt"
INTERNAL_ERROR = -32603
MISSING_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022

# Codes raised locally by the client, never sent by a server. They sit outside
# the JSON-RPC range so they cannot be mistaken for an error from the peer.
REQUEST_TIMEOUT = 408
REQUEST_CANCELLED = 499
NOT_CONNECTED = 503
INPUT_LOOP = 508


class McpError(Exception):
    """Application-level error from the MCP protocol."""

    def __init__(self, code: int, message: str, data: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class ToolError(McpError):
    """A tool executed but returned is_error=True."""

    def __init__(self, message: str) -> None:
        super().__init__(code=-1, message=message)
