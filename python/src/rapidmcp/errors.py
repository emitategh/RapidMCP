"""Error types for rapidmcp."""


class McpError(Exception):
    """Application-level error from the MCP protocol."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(McpError):
    """A tool executed but returned is_error=True."""

    def __init__(self, message: str) -> None:
        super().__init__(code=-1, message=message)
