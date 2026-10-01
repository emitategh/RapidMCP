import { ServerNotification_Type, type DeepPartial, type ServerEnvelope } from "../generated/mcp.js";
import { McpError } from "./errors.js";
import { AsyncQueue, PendingRequests } from "./session.js";

interface ClientCapabilities {
  sampling: boolean;
  elicitation: boolean;
  roots: boolean;
}

export interface SamplingContentInput {
  type: string;
  text?: string;
  data?: Uint8Array;
  mimeType?: string;
  uri?: string;
  /** tool_use fields */
  toolUseId?: string;
  toolName?: string;
  /** JSON-serialised input object */
  toolInput?: string;
  /** tool_result field — matches the toolUseId it answers */
  toolResultId?: string;
}

export interface SamplingRequestInput {
  messages: Array<{ role: string; content: SamplingContentInput[] }>;
  maxTokens: number;
  systemPrompt?: string;
  /** Tools the model may call; inputSchema is a JSON Schema string. */
  tools?: Array<{ name: string; description?: string; inputSchema?: string }>;
  /** "auto" | "required" | "none" */
  toolChoice?: string;
  modelPreferences?: {
    hints?: string[];
    costPriority?: number;
    speedPriority?: number;
    intelligencePriority?: number;
  };
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
        data: c.data ?? new Uint8Array(),
        mimeType: c.mimeType ?? "",
        uri: c.uri ?? "",
        toolUseId: c.toolUseId ?? "",
        toolName: c.toolName ?? "",
        toolInput: c.toolInput ?? "",
        toolResultId: c.toolResultId ?? "",
      })),
    }));
    const prefs = request.modelPreferences;

    this._queue.enqueue({
      requestId: rid,
      message: {
        $case: "sampling" as const,
        sampling: {
          messages,
          systemPrompt: request.systemPrompt ?? "",
          maxTokens: request.maxTokens,
          tools: (request.tools ?? []).map((t) => ({
            name: t.name,
            description: t.description ?? "",
            inputSchema: t.inputSchema ?? "",
          })),
          toolChoice: request.toolChoice ?? "",
          modelPreferences: prefs && {
            hints: (prefs.hints ?? []).map((name) => ({ name })),
            costPriority: prefs.costPriority ?? 0,
            speedPriority: prefs.speedPriority ?? 0,
            intelligencePriority: prefs.intelligencePriority ?? 0,
          },
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

  /** Ask the client for its registered root URIs. */
  async listRoots(): Promise<Array<{ uri: string; name: string }>> {
    if (!this._capabilities.roots) {
      throw new McpError(400, "Client does not support roots");
    }
    const rid = this._pending.nextId();
    const future = this._pending.create(rid);

    this._queue.enqueue({
      requestId: rid,
      message: { $case: "rootsRequest" as const, rootsRequest: {} },
    });

    const timeout = new Promise<never>((_, reject) => {
      const timer = setTimeout(
        () => reject(new McpError(408, "Roots request timed out")),
        REQUEST_TIMEOUT,
      );
      if (typeof timer === "object" && "unref" in timer) (timer as NodeJS.Timeout).unref();
    });

    const reply = (await Promise.race([future, timeout])) as {
      roots: Array<{ uri: string; name: string }>;
    };
    return reply.roots;
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
