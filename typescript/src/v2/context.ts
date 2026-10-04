/** Context for a v2 tool call: everything goes onto the call's own stream. */
import type { CallToolEvent, DeepPartial, InputRequest, RequestMeta } from "../../generated/mcp_v2.js";
import type { Answers } from "./state.js";
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
 * Thrown by ctx.elicit() to end the call with input_required. An McpError so
 * the tool manager lets it through instead of turning it into tool output; the
 * v2 servicer catches it before it could become an error.
 */
export class NeedsInput extends McpError {
  constructor(public readonly requests: Record<string, InputRequest>) {
    super(ErrorCode.InternalError, "The tool needs input from the client");
    this.name = "NeedsInput";
  }
}

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

  private _elicitCalls = 0;

  constructor(
    private readonly _meta: RequestMeta,
    private readonly _emit: (event: DeepPartial<CallToolEvent>) => void,
    /** Aborted when the client cancels the call or its deadline passes. */
    public readonly signal: AbortSignal,
    private readonly _answers: Answers = {},
  ) {
    this.log = {
      debug: (message) => this._log("debug", message),
      info: (message) => this._log("info", message),
      warning: (message) => this._log("warning", message),
      error: (message) => this._log("error", message),
    };
  }

  /** The answers this request carries, by key. */
  get inputResponses(): Answers {
    return { ...this._answers };
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

  /**
   * Ask the user a question.
   *
   * The first time, this ends the call: the client asks the user and calls the
   * tool again, and the tool **runs again from the top**. On that run this
   * returns the answer. So ask before doing anything with side effects.
   */
  async elicit(
    message: string,
    schema: Record<string, unknown> = {},
    opts: { key?: string; url?: string } = {},
  ): Promise<{ action: string; content: string }> {
    const mode = opts.url !== undefined ? "url" : "form";
    const capability = this._meta.clientCapabilities?.elicitation;
    if (!capability || !capability[mode]) {
      throw new McpError(
        ErrorCode.MissingClientCapability,
        `Client does not support ${mode} elicitation`,
        { requiredCapabilities: [`elicitation.${mode}`] },
      );
    }

    const key = opts.key ?? `elicit-${this._elicitCalls}`;
    this._elicitCalls += 1;
    const answer = this._answers[key];
    if (answer !== undefined) return answer;

    throw new NeedsInput({
      [key]: {
        request: {
          $case: "elicit",
          elicit: {
            message,
            mode:
              opts.url !== undefined
                ? { $case: "url", url: { url: opts.url } }
                : { $case: "form", form: { requestedSchema: JSON.stringify(schema) } },
          },
        },
      },
    });
  }
}
