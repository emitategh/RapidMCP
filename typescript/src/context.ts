import { ServerNotification_Type, type DeepPartial, type ServerEnvelope } from "../generated/mcp.js";
import { McpError } from "./errors.js";
import { AsyncQueue, PendingRequests } from "./session.js";

interface ClientCapabilities {
  sampling: boolean;
  elicitation: boolean;
  roots: boolean;
}

export interface SamplingRequestInput {
  messages: Array<{ role: string; content: Array<{ type: string; text?: string }> }>;
  maxTokens: number;
  systemPrompt?: string;
}

const REQUEST_TIMEOUT = 30_000;

export class Context {
  private _capabilities: ClientCapabilities;
  private _pending: PendingRequests;
  private _queue: AsyncQueue<DeepPartial<ServerEnvelope> | null>;
  /** Aborted when the client cancels this tool call. */
  public readonly signal: AbortSignal;
  public readonly log: {
    debug: (message: string) => void;
    info: (message: string) => void;
    warning: (message: string) => void;
    error: (message: string) => void;
  };

  constructor(
    capabilities: ClientCapabilities,
    pending: PendingRequests,
    queue: AsyncQueue<DeepPartial<ServerEnvelope> | null>,
    signal: AbortSignal = new AbortController().signal,
  ) {
    this._capabilities = capabilities;
    this._pending = pending;
    this._queue = queue;
    this.signal = signal;

    this.log = {
      debug: (msg: string) => this._log("debug", msg),
      info: (msg: string) => this._log("info", msg),
      warning: (msg: string) => this._log("warning", msg),
      error: (msg: string) => this._log("error", msg),
    };
  }

  private _log(level: string, message: string): void {
    this._queue.enqueue({
      requestId: 0n,
      message: {
        $case: "notification" as const,
        notification: {
          type: ServerNotification_Type.LOG,
          payload: JSON.stringify({ level, message }),
        },
      },
    });
  }

  reportProgress(current: number, total: number): void {
    this._queue.enqueue({
      requestId: 0n,
      message: {
        $case: "notification" as const,
        notification: {
          type: ServerNotification_Type.PROGRESS,
          payload: JSON.stringify({ progress: current, total }),
        },
      },
    });
  }

  async sample(request: SamplingRequestInput): Promise<unknown> {
    if (!this._capabilities.sampling) {
      throw new McpError(400, "Client does not support sampling");
    }
    const rid = this._pending.nextId();
    const future = this._pending.create(rid);

    const messages = request.messages.map((m) => ({
      role: m.role,
      content: m.content.map((c) => ({
        type: c.type,
        text: c.text ?? "",
        data: new Uint8Array(),
        mimeType: "",
        uri: "",
        toolUseId: "",
        toolName: "",
        toolInput: "",
        toolResultId: "",
      })),
    }));

    this._queue.enqueue({
      requestId: rid,
      message: {
        $case: "sampling" as const,
        sampling: {
          messages,
          systemPrompt: request.systemPrompt ?? "",
          maxTokens: request.maxTokens,
          tools: [],
          toolChoice: "",
        },
      },
    });

    const timeout = new Promise<never>((_, reject) => {
      const timer = setTimeout(
        () => reject(new McpError(408, "Sampling request timed out")),
        REQUEST_TIMEOUT,
      );
      if (typeof timer === "object" && "unref" in timer) (timer as NodeJS.Timeout).unref();
    });

    return Promise.race([future, timeout]);
  }

  async elicit(
    message: string,
    schema: Record<string, unknown>,
  ): Promise<{ action: string; content: string }> {
    if (!this._capabilities.elicitation) {
      throw new McpError(400, "Client does not support elicitation");
    }
    const rid = this._pending.nextId();
    const future = this._pending.create(rid);

    this._queue.enqueue({
      requestId: rid,
      message: {
        $case: "elicitation" as const,
        elicitation: {
          message,
          schema: JSON.stringify(schema),
        },
      },
    });

    const timeout = new Promise<never>((_, reject) => {
      const timer = setTimeout(
        () => reject(new McpError(408, "Elicitation request timed out")),
        REQUEST_TIMEOUT,
      );
      if (typeof timer === "object" && "unref" in timer) (timer as NodeJS.Timeout).unref();
    });

    return Promise.race([future, timeout]) as Promise<{ action: string; content: string }>;
  }
}
