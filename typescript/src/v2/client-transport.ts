/** Client transport for the v2 protocol: one stateless RPC per operation. */
import { createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status, type CallOptions } from "nice-grpc-common";
import { McpDefinition, type McpClient, type RequestMeta } from "../../generated/mcp_v2.js";
import { buildMetadata, type ClientOptions } from "../auth.js";
import { ErrorCode, McpError } from "../errors.js";
import {
  convertCompleteResult,
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

  constructor(
    channel: Channel,
    private readonly _opts: ClientOptions,
    private readonly _timeoutMs: number,
    private readonly _supportsElicitation: () => boolean,
  ) {
    this._client = createClientFactory().create(McpDefinition, channel);
  }

  private _meta(): RequestMeta {
    return {
      protocolVersion: PROTOCOL_VERSION,
      clientCapabilities: {
        elicitation: this._supportsElicitation() ? { form: true, url: false } : undefined,
        extensions: {},
      },
      clientInfo: { name: "rapidmcp-typescript", version: "0.3.0" },
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
}
