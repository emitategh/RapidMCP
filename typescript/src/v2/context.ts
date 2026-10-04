/** Context for a v2 tool call: everything goes onto the call's own stream. */
import type { CallToolEvent, DeepPartial, RequestMeta } from "../../generated/mcp_v2.js";
import { ErrorCode, McpError } from "../errors.js";

/** RFC 5424 severities, lowest first. */
export const LOG_LEVELS = [
  "debug",
  "info",
  "notice",
  "warning",
  "error",
  "critical",
  "alert",
  "emergency",
];

/**
 * Same surface as the v1 Context, stateless underneath. Progress and log
 * messages are emitted only when the request asked for them in its meta.
 * Requests from the server to the client do not exist on v2.
 */
export class V2Context {
  public readonly log: {
    debug: (message: string) => void;
    info: (message: string) => void;
    warning: (message: string) => void;
    error: (message: string) => void;
  };

  constructor(
    private readonly _meta: RequestMeta,
    private readonly _emit: (event: DeepPartial<CallToolEvent>) => void,
    /** Aborted when the client cancels the call or its deadline passes. */
    public readonly signal: AbortSignal,
  ) {
    this.log = {
      debug: (message) => this._log("debug", message),
      info: (message) => this._log("info", message),
      warning: (message) => this._log("warning", message),
      error: (message) => this._log("error", message),
    };
  }

  private _log(level: string, message: string): void {
    const wanted = this._meta.logLevel;
    if (wanted === undefined) return;
    if (LOG_LEVELS.indexOf(level) < LOG_LEVELS.indexOf(wanted)) return;
    this._emit({
      event: {
        $case: "log",
        log: { level, logger: "", data: JSON.stringify({ message, extra: null }) },
      },
    });
  }

  reportProgress(current: number, total?: number, message = ""): void {
    const token = this._meta.progressToken;
    if (token === undefined) return;
    this._emit({ event: { $case: "progress", progress: { token, progress: current, total, message } } });
  }

  async sample(): Promise<never> {
    throw new McpError(
      ErrorCode.MethodNotFound,
      "Sampling is not available on the v2 protocol; call your LLM provider directly",
    );
  }

  async listRoots(): Promise<never> {
    throw new McpError(
      ErrorCode.MethodNotFound,
      "Roots are not available on the v2 protocol; take paths as tool arguments",
    );
  }

  async elicit(): Promise<never> {
    throw new McpError(ErrorCode.MethodNotFound, "ctx.elicit() is not available on the v2 protocol yet");
  }
}
