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
  UnsupportedProtocolVersion: -32022,
  RequestTimeout: 408,
  RequestCancelled: 499,
  NotConnected: 503,
} as const;

/** Structured detail some errors carry. */
export interface McpErrorData {
  /** -32022: versions the server does serve. */
  supported?: string[];
  /** -32022: the version the request asked for. */
  requested?: string;
  /** -32021: capabilities the client would have needed. */
  requiredCapabilities?: string[];
}

export class McpError extends Error {
  public readonly code: number;
  public readonly data: McpErrorData | null;

  constructor(code: number, message: string, data: McpErrorData | null = null) {
    super(message);
    this.name = "McpError";
    this.code = code;
    this.data = data;
  }
}

export class ToolError extends McpError {
  constructor(message: string) {
    super(-1, message);
    this.name = "ToolError";
  }
}
