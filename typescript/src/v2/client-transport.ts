/** Client transport for the v2 protocol: one stateless RPC per operation. */
import { createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status, type CallOptions } from "nice-grpc-common";
import {
  McpDefinition,
  type CallToolResult as WireCallToolResult,
  type GetPromptResult as WireGetPromptResult,
  type McpClient,
  type ReadResourceResult as WireReadResourceResult,
  type RequestMeta,
} from "../../generated/mcp_v2.js";
import type { NotificationRegistry } from "../session.js";
import { buildMetadata, type ClientOptions } from "../auth.js";
import { ErrorCode, McpError } from "../errors.js";
import {
  convertCallToolResult,
  convertCompleteResult,
  convertGetPromptResult,
  convertReadResourceResult,
  type CallToolResult,
  type GetPromptResult,
  type ReadResourceResult,
  convertPrompt,
  convertResource,
  convertResourceTemplate,
  convertToolV2,
  type CompleteResult,
  type ListResult,
  type Prompt,
  type Resource,
  type ResourceTemplate,
  type ServerInfo,
  type Tool,
} from "../types.js";
import { errorFromRpc } from "./errors.js";

export const PROTOCOL_VERSION = "2026-07-28";

/** True when a failed discover means "this server does not serve v2". */
export function isV2Missing(err: unknown): boolean {
  return err instanceof ClientError && err.code === Status.UNIMPLEMENTED;
}

export class V2Transport {
  private _client: McpClient;
  private _nextProgressToken = 1;

  constructor(
    channel: Channel,
    private readonly _opts: ClientOptions,
    private readonly _timeoutMs: number,
    private readonly _supportsElicitation: () => boolean,
    private readonly _notifications: NotificationRegistry,
  ) {
    this._client = createClientFactory().create(McpDefinition, channel);
  }

  private _meta(events = false): RequestMeta {
    return {
      protocolVersion: PROTOCOL_VERSION,
      clientCapabilities: {
        elicitation: this._supportsElicitation() ? { form: true, url: false } : undefined,
        extensions: {},
      },
      clientInfo: { name: "rapidmcp-typescript", version: "0.3.0" },
      progressToken:
        events && this._notifications.has("progress") ? `p${this._nextProgressToken++}` : undefined,
      logLevel: events && this._notifications.has("log") ? "debug" : undefined,
    };
  }

  /** Run one RPC with the token, a deadline, and MCP error translation. */
  private async _call<T>(invoke: (options: CallOptions) => Promise<T>): Promise<T> {
    let trailer: Metadata | null = null;
    const options: CallOptions = {
      signal: AbortSignal.timeout(this._timeoutMs),
      onTrailer: (t) => {
        trailer = t;
      },
    };
    if (this._opts.token) options.metadata = buildMetadata(this._opts);
    try {
      return await invoke(options);
    } catch (err) {
      if (err instanceof ClientError) {
        const mapped = errorFromRpc(err.code, err.details, trailer);
        if (mapped) throw mapped;
      } else if (err instanceof Error && (err.name === "AbortError" || err.name === "TimeoutError")) {
        throw new McpError(ErrorCode.RequestTimeout, "Request timeout");
      }
      throw err;
    }
  }

  async discover(): Promise<ServerInfo> {
    const result = await this._call((o) => this._client.discover({ meta: this._meta() }, o));
    const caps = result.capabilities;
    return {
      serverName: result.meta?.serverInfo?.name ?? "",
      serverVersion: result.meta?.serverInfo?.version ?? "",
      capabilities: {
        tools: caps?.tools !== undefined,
        toolsListChanged: caps?.tools?.listChanged ?? false,
        resources: caps?.resources !== undefined,
        prompts: caps?.prompts !== undefined,
      },
    };
  }

  async listTools(cursor?: string): Promise<ListResult<Tool>> {
    const result = await this._call((o) =>
      this._client.listTools({ meta: this._meta(), cursor: cursor ?? "" }, o),
    );
    return { items: result.tools.map(convertToolV2), nextCursor: result.nextCursor || null };
  }

  async listResources(cursor?: string): Promise<ListResult<Resource>> {
    const result = await this._call((o) =>
      this._client.listResources({ meta: this._meta(), cursor: cursor ?? "" }, o),
    );
    return { items: result.resources.map(convertResource), nextCursor: result.nextCursor || null };
  }

  async listResourceTemplates(cursor?: string): Promise<ListResult<ResourceTemplate>> {
    const result = await this._call((o) =>
      this._client.listResourceTemplates({ meta: this._meta(), cursor: cursor ?? "" }, o),
    );
    return {
      items: result.templates.map(convertResourceTemplate),
      nextCursor: result.nextCursor || null,
    };
  }

  async listPrompts(cursor?: string): Promise<ListResult<Prompt>> {
    const result = await this._call((o) =>
      this._client.listPrompts({ meta: this._meta(), cursor: cursor ?? "" }, o),
    );
    return { items: result.prompts.map(convertPrompt), nextCursor: result.nextCursor || null };
  }

  async complete(
    refType: string,
    refName: string,
    argName: string,
    argValue: string,
  ): Promise<CompleteResult> {
    const result = await this._call((o) =>
      this._client.complete(
        {
          meta: this._meta(),
          ref: { type: refType, name: refName },
          argument: { name: argName, value: argValue },
        },
        o,
      ),
    );
    return convertCompleteResult(result);
  }

  private async _notify(kind: string, payload: unknown): Promise<void> {
    try {
      await this._notifications.dispatch(kind, JSON.stringify(payload));
    } catch (err) {
      console.error(`[rapidmcp] notification handler for '${kind}' failed:`, err);
    }
  }

  /** Run a streaming RPC to its terminal event, feeding progress and log handlers. */
  private async _stream<R>(
    open: (options: CallOptions) => AsyncIterable<{ event?: { $case: string } | undefined }>,
    opts: { signal?: AbortSignal; timeout?: number } = {},
  ): Promise<R> {
    if (opts.signal?.aborted) throw new McpError(-1, "Aborted");
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, opts.timeout ?? this._timeoutMs);
    const onAbort = () => controller.abort();
    opts.signal?.addEventListener("abort", onAbort, { once: true });

    let trailer: Metadata | null = null;
    const options: CallOptions = {
      signal: controller.signal,
      onTrailer: (t) => {
        trailer = t;
      },
    };
    if (this._opts.token) options.metadata = buildMetadata(this._opts);

    try {
      for await (const message of open(options)) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any -- one loop serves three event unions
        const event = message.event as any;
        if (!event) continue;
        if (event.$case === "progress") {
          const p = event.progress;
          await this._notify("progress", {
            progress: p.progress,
            total: p.total ?? null,
            message: p.message,
            token: p.token,
          });
        } else if (event.$case === "log") {
          const data = event.log.data ? JSON.parse(event.log.data) : {};
          await this._notify("log", {
            level: event.log.level,
            message: data.message ?? null,
            extra: data.extra ?? null,
          });
        } else if (event.$case === "complete") {
          return event.complete as R;
        } else if (event.$case === "inputRequired") {
          throw new McpError(
            ErrorCode.MethodNotFound,
            "The server asked for input, which this client cannot give yet",
          );
        }
      }
      throw new McpError(ErrorCode.InternalError, "The server ended the call without a result");
    } catch (err) {
      if (err instanceof McpError) throw err;
      if (err instanceof ClientError) {
        const mapped = errorFromRpc(err.code, err.details, trailer);
        if (mapped) throw mapped;
      } else if (err instanceof Error && err.name === "AbortError") {
        throw timedOut
          ? new McpError(ErrorCode.RequestTimeout, "Request timeout")
          : new McpError(-1, "Aborted");
      }
      throw err;
    } finally {
      clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
    }
  }

  async callTool(
    name: string,
    args: Record<string, unknown>,
    opts: { signal?: AbortSignal; timeout?: number } = {},
  ): Promise<CallToolResult> {
    const wire = await this._stream<WireCallToolResult>(
      (o) =>
        this._client.callTool(
          { meta: this._meta(true), name, arguments: JSON.stringify(args), inputResponses: {} },
          o,
        ),
      opts,
    );
    return {
      ...convertCallToolResult(wire),
      structuredContent: wire.structuredContent ? JSON.parse(wire.structuredContent) : undefined,
    };
  }

  async readResource(uri: string): Promise<ReadResourceResult> {
    const wire = await this._stream<WireReadResourceResult>((o) =>
      this._client.readResource({ meta: this._meta(), uri, inputResponses: {} }, o),
    );
    return convertReadResourceResult(wire);
  }

  async getPrompt(name: string, args: Record<string, string>): Promise<GetPromptResult> {
    const wire = await this._stream<WireGetPromptResult>((o) =>
      this._client.getPrompt({ meta: this._meta(), name, arguments: args, inputResponses: {} }, o),
    );
    return convertGetPromptResult(
      wire as unknown as Parameters<typeof convertGetPromptResult>[0],
    );
  }
}
