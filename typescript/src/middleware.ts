export interface CallToolResult {
  content: Array<{ type: string; text: string; data: Uint8Array; mimeType: string; uri: string }>;
  isError: boolean;
  /** The tool's result when it was a JSON object; carried to v2 clients as structured content. */
  structuredContent?: unknown;
}

export interface ToolCallContext {
  toolName: string;
  arguments: Record<string, unknown>;
  ctx: any;
  inputSchema: Record<string, unknown> | null;
}

export abstract class Middleware {
  async onToolCall(ctx: ToolCallContext, next: () => Promise<CallToolResult>): Promise<CallToolResult> {
    return next();
  }

  static buildChain(
    middlewares: Middleware[],
    base: (ctx: ToolCallContext) => Promise<CallToolResult>,
  ): (ctx: ToolCallContext) => Promise<CallToolResult> {
    let chain = base;
    for (const mw of [...middlewares].reverse()) {
      const current = chain;
      chain = (ctx) => mw.onToolCall(ctx, () => current(ctx));
    }
    return chain;
  }
}

export class TimingMiddleware extends Middleware {
  async onToolCall(ctx: ToolCallContext, next: () => Promise<CallToolResult>): Promise<CallToolResult> {
    const start = performance.now();
    const result = await next();
    const elapsed = performance.now() - start;
    console.log(`[timing] ${ctx.toolName} completed in ${elapsed.toFixed(2)}ms`);
    return result;
  }
}

export class LoggingMiddleware extends Middleware {
  async onToolCall(ctx: ToolCallContext, next: () => Promise<CallToolResult>): Promise<CallToolResult> {
    console.log(`[mcp] tool=${ctx.toolName} args=${JSON.stringify(ctx.arguments)}`);
    const result = await next();
    console.log(`[mcp] tool=${ctx.toolName} is_error=${result.isError}`);
    return result;
  }
}

export class TimeoutMiddleware extends Middleware {
  private _defaultTimeout: number;
  private _perTool: Map<string, number>;

  constructor(opts: { defaultTimeout?: number; perTool?: Record<string, number> } = {}) {
    super();
    this._defaultTimeout = opts.defaultTimeout ?? 30_000;
    this._perTool = new Map(Object.entries(opts.perTool ?? {}));
  }

  async onToolCall(ctx: ToolCallContext, next: () => Promise<CallToolResult>): Promise<CallToolResult> {
    const timeout = this._perTool.get(ctx.toolName) ?? this._defaultTimeout;
    return Promise.race([
      next(),
      new Promise<CallToolResult>((_, reject) => {
        const timer = setTimeout(() => {
          reject(new Error("TIMEOUT"));
        }, timeout);
        if (typeof timer === "object" && "unref" in timer) (timer as NodeJS.Timeout).unref();
      }),
    ]).catch((err) => {
      if (err instanceof Error && err.message === "TIMEOUT") {
        return {
          content: [{ type: "text", text: `Tool '${ctx.toolName}' timed out after ${timeout}ms`, data: new Uint8Array(), mimeType: "", uri: "" }],
          isError: true,
        };
      }
      throw err;
    });
  }
}

export class ValidationMiddleware extends Middleware {
  async onToolCall(ctx: ToolCallContext, next: () => Promise<CallToolResult>): Promise<CallToolResult> {
    const schema = ctx.inputSchema;
    if (!schema) return next();

    const properties = (schema.properties ?? {}) as Record<string, unknown>;
    const required = (schema.required ?? []) as string[];

    const missing = required.filter((f) => !(f in ctx.arguments));
    if (missing.length > 0) {
      return {
        content: [{ type: "text", text: `Tool '${ctx.toolName}' missing required argument(s): ${missing.join(", ")}`, data: new Uint8Array(), mimeType: "", uri: "" }],
        isError: true,
      };
    }

    if (Object.keys(properties).length > 0) {
      const unknown = Object.keys(ctx.arguments).filter((k) => !(k in properties));
      if (unknown.length > 0) {
        return {
          content: [{ type: "text", text: `Tool '${ctx.toolName}' received unknown argument(s): ${unknown.join(", ")}`, data: new Uint8Array(), mimeType: "", uri: "" }],
          isError: true,
        };
      }
    }

    return next();
  }
}
