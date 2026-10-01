/**
 * v2 servicer — MCP 2026-07-28 semantics, one RPC per operation.
 *
 * Stateless by construction: every handler reads what it needs from the
 * request's own `meta` and from the server's registries, never from anything
 * an earlier request left behind.
 */
import type { CallContext } from "nice-grpc-common";
import {
  CacheScope,
  type CacheHint,
  type CompleteRequest,
  type CompleteResult,
  type DeepPartial,
  type DiscoverRequest,
  type DiscoverResult,
  type ListPromptsRequest,
  type ListPromptsResult,
  type ListResourceTemplatesRequest,
  type ListResourceTemplatesResult,
  type ListResourcesRequest,
  type ListResourcesResult,
  type ListToolsRequest,
  type ListToolsResult,
  type McpServiceImplementation,
  type RequestMeta,
  type ResultMeta,
} from "../../generated/mcp_v2.js";
import { paginate } from "../_utils.js";
import { ErrorCode, McpError } from "../errors.js";
import type { PromptManager } from "../prompts/prompt-manager.js";
import type { ResourceManager } from "../resources/resource-manager.js";
import type { ToolManager } from "../tools/tool-manager.js";
import { toServerError } from "./errors.js";

export const SUPPORTED_VERSIONS = ["2026-07-28"];

export interface McpV2ServicerOptions {
  name: string;
  version: string;
  toolManager: ToolManager;
  resourceManager: ResourceManager;
  promptManager: PromptManager;
  pageSize?: number;
}

const NO_CACHE: CacheHint = { ttlMs: 0n, scope: CacheScope.CACHE_SCOPE_PRIVATE };

export class McpV2Servicer implements McpServiceImplementation {
  constructor(private readonly _opts: McpV2ServicerOptions) {}

  private _resultMeta(): ResultMeta {
    return { serverInfo: { name: this._opts.name, version: this._opts.version } };
  }

  /** Reject a request whose metadata is missing or names a version we do not serve. */
  private _checkMeta(meta: RequestMeta | undefined, context: CallContext): void {
    if (!meta?.protocolVersion || !meta.clientCapabilities) {
      throw toServerError(
        new McpError(
          ErrorCode.InvalidParams,
          "Request meta must carry protocolVersion and clientCapabilities",
        ),
        context.trailer,
      );
    }
    if (!SUPPORTED_VERSIONS.includes(meta.protocolVersion)) {
      throw toServerError(
        new McpError(ErrorCode.UnsupportedProtocolVersion, "Unsupported protocol version", {
          supported: SUPPORTED_VERSIONS,
          requested: meta.protocolVersion,
        }),
        context.trailer,
      );
    }
  }

  async discover(
    request: DiscoverRequest,
    context: CallContext,
  ): Promise<DeepPartial<DiscoverResult>> {
    this._checkMeta(request.meta, context);
    const { toolManager, resourceManager, promptManager } = this._opts;
    const hasResources =
      resourceManager.listResources().length > 0 ||
      resourceManager.listResourceTemplates().length > 0;
    return {
      meta: this._resultMeta(),
      supportedVersions: SUPPORTED_VERSIONS,
      capabilities: {
        tools: toolManager.listTools().length > 0 ? { listChanged: true } : undefined,
        resources: hasResources ? { listChanged: true, subscribe: true } : undefined,
        prompts: promptManager.listPrompts().length > 0 ? { listChanged: true } : undefined,
      },
      cache: NO_CACHE,
    };
  }

  async listTools(
    request: ListToolsRequest,
    context: CallContext,
  ): Promise<DeepPartial<ListToolsResult>> {
    this._checkMeta(request.meta, context);
    const tools = this._opts.toolManager.listTools().map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
      outputSchema: t.outputSchema,
      // Only the hints the author actually set go on the wire.
      annotations: t.annotations
        ? {
            title: t.annotations.title ?? "",
            readOnlyHint: t.annotations.readOnly,
            destructiveHint: t.annotations.destructive,
            idempotentHint: t.annotations.idempotent,
            openWorldHint: t.annotations.openWorld,
          }
        : undefined,
    }));
    const [page, nextCursor] = paginate(tools, request.cursor, this._opts.pageSize);
    return { meta: this._resultMeta(), tools: page, nextCursor, cache: NO_CACHE };
  }

  async listResources(
    request: ListResourcesRequest,
    context: CallContext,
  ): Promise<DeepPartial<ListResourcesResult>> {
    this._checkMeta(request.meta, context);
    const resources = this._opts.resourceManager.listResources().map((r) => ({
      uri: r.uri,
      name: r.name,
      description: r.description,
      mimeType: r.mimeType,
    }));
    const [page, nextCursor] = paginate(resources, request.cursor, this._opts.pageSize);
    return { meta: this._resultMeta(), resources: page, nextCursor, cache: NO_CACHE };
  }

  async listResourceTemplates(
    request: ListResourceTemplatesRequest,
    context: CallContext,
  ): Promise<DeepPartial<ListResourceTemplatesResult>> {
    this._checkMeta(request.meta, context);
    const templates = this._opts.resourceManager.listResourceTemplates().map((t) => ({
      uriTemplate: t.uriTemplate,
      name: t.name,
      description: t.description,
      mimeType: t.mimeType,
    }));
    const [page, nextCursor] = paginate(templates, request.cursor, this._opts.pageSize);
    return { meta: this._resultMeta(), templates: page, nextCursor, cache: NO_CACHE };
  }

  async listPrompts(
    request: ListPromptsRequest,
    context: CallContext,
  ): Promise<DeepPartial<ListPromptsResult>> {
    this._checkMeta(request.meta, context);
    const prompts = this._opts.promptManager.listPrompts().map((p) => ({
      name: p.name,
      description: p.description,
      arguments: p.arguments.map((a) => ({
        name: a.name,
        description: a.description ?? "",
        required: a.required ?? false,
      })),
    }));
    const [page, nextCursor] = paginate(prompts, request.cursor, this._opts.pageSize);
    return { meta: this._resultMeta(), prompts: page, nextCursor, cache: NO_CACHE };
  }

  async complete(
    request: CompleteRequest,
    context: CallContext,
  ): Promise<DeepPartial<CompleteResult>> {
    this._checkMeta(request.meta, context);
    try {
      const result = await this._opts.promptManager.complete(
        request.ref?.type ?? "",
        request.ref?.name ?? "",
        request.argument?.name ?? "",
        request.argument?.value ?? "",
      );
      return {
        meta: this._resultMeta(),
        values: result.values,
        hasMore: result.hasMore ?? false,
        total: result.total ?? result.values.length,
      };
    } catch (err) {
      console.error("[rapidmcp] completion handler failed:", err);
      throw toServerError(
        new McpError(ErrorCode.InternalError, "Completion handler failed"),
        context.trailer,
      );
    }
  }
}
