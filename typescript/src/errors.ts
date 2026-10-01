/**
 * Codes sent between peers follow MCP / JSON-RPC 2.0. The last three are raised
 * locally by the client and never sent by a server; they sit outside the
 * JSON-RPC range so they cannot be mistaken for an error from the peer.
 */
export const ErrorCode = {
  MethodNotFound: -32601,
  /** Also "no such tool / resource / prompt". */
  InvalidParams: -32602,
  InternalError: -32603,
  MissingClientCapability: -32021,
  RequestTimeout: 408,
  RequestCancelled: 499,
  NotConnected: 503,
} as const;

export class McpError extends Error {
  public readonly code: number;

  constructor(code: number, message: string) {
    super(message);
    this.name = "McpError";
    this.code = code;
  }
}

export class ToolError extends McpError {
  constructor(message: string) {
    super(-1, message);
    this.name = "ToolError";
  }
}
