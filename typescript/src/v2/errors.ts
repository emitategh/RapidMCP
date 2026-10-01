/**
 * Carry MCP errors over gRPC: a status tooling understands, plus the exact
 * MCP code (and any structured data) in trailing metadata.
 */
import { Metadata, ServerError, Status } from "nice-grpc-common";
import { ErrorData } from "../../generated/mcp_v2.js";
import { ErrorCode, McpError, type McpErrorData } from "../errors.js";

const CODE_KEY = "mcp-error-code";
const DATA_KEY = "mcp-error-data-bin";

const STATUS = new Map<number, Status>([
  [ErrorCode.InvalidParams, Status.INVALID_ARGUMENT],
  [ErrorCode.MethodNotFound, Status.UNIMPLEMENTED],
  [ErrorCode.InternalError, Status.INTERNAL],
  [ErrorCode.MissingClientCapability, Status.FAILED_PRECONDITION],
  [ErrorCode.UnsupportedProtocolVersion, Status.FAILED_PRECONDITION],
]);

export function statusFor(code: number): Status {
  return STATUS.get(code) ?? Status.UNKNOWN;
}

export function setErrorTrailers(trailer: Metadata, error: McpError): void {
  trailer.set(CODE_KEY, String(error.code));
  if (error.data) {
    trailer.set(
      DATA_KEY,
      ErrorData.encode({
        supportedVersions: error.data.supported ?? [],
        requestedVersion: error.data.requested ?? "",
        requiredCapabilities: error.data.requiredCapabilities ?? [],
      }).finish(),
    );
  }
}

/** The error to throw from a v2 handler; also fills the call's trailer. */
export function toServerError(error: McpError, trailer: Metadata): ServerError {
  setErrorTrailers(trailer, error);
  return new ServerError(statusFor(error.code), error.message);
}

function dataFrom(raw: Uint8Array): McpErrorData | null {
  const parsed = ErrorData.decode(raw);
  const data: McpErrorData = {};
  if (parsed.supportedVersions.length > 0) data.supported = parsed.supportedVersions;
  if (parsed.requestedVersion) data.requested = parsed.requestedVersion;
  if (parsed.requiredCapabilities.length > 0) data.requiredCapabilities = parsed.requiredCapabilities;
  return Object.keys(data).length > 0 ? data : null;
}

/**
 * The McpError a failed RPC stands for, or null when it is not one.
 * Null means "rethrow the gRPC error as it is" — an auth failure, or an
 * UNIMPLEMENTED from a server that does not serve v2 at all.
 */
export function errorFromRpc(
  status: Status,
  details: string,
  trailer: Metadata | null,
): McpError | null {
  const code = trailer?.get(CODE_KEY);
  if (code !== undefined) {
    const raw = trailer?.get(DATA_KEY);
    return new McpError(Number(code), details, raw ? dataFrom(raw) : null);
  }
  if (status === Status.DEADLINE_EXCEEDED) {
    return new McpError(ErrorCode.RequestTimeout, details || "Request timeout");
  }
  if (status === Status.UNAVAILABLE) {
    return new McpError(ErrorCode.NotConnected, details || "Server unavailable");
  }
  return null;
}
