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
  type CallToolEvent,
  type CallToolRequest,
  type GetPromptEvent,
  type GetPromptRequest,
  type ReadResourceEvent,
  type ReadResourceRequest,
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
import { paginate, parseToolArguments } from "../_utils.js";
import { Middleware, type CallToolResult, type ToolCallContext } from "../middleware.js";
import { AsyncQueue } from "../session.js";
import { LOG_LEVELS, V2Context } from "./context.js";
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
  middlewares: Middleware[];
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
    if (meta.logLevel !== undefined && !LOG_LEVELS.includes(meta.logLevel)) {
      throw toServerError(
        new McpError(ErrorCode.InvalidParams, `Unknown log level '${meta.logLevel}'`),
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

  /** A deliberate McpError goes out as it is; anything else becomes *fallback* and is logged. */
  private _failure(err: unknown, fallback: string, context: CallContext) {
    if (err instanceof McpError) return toServerError(err, context.trailer);
    console.error(`[rapidmcp] ${fallback}:`, err);
    return toServerError(new McpError(ErrorCode.InternalError, fallback), context.trailer);
  }

  private async _runTool(name: string, argumentsText: string, ctx: V2Context): Promise<CallToolResult> {
    const args = parseToolArguments(name, argumentsText);
    const tool = this._opts.toolManager.getTool(name);
    if (!tool) throw new McpError(ErrorCode.InvalidParams, `Tool '${name}' not found`);

    let inputSchema: Record<string, unknown> | null = null;
    if (tool.inputSchema && tool.inputSchema !== "{}") {
      try {
        inputSchema = JSON.parse(tool.inputSchema) as Record<string, unknown>;
      } catch {
        // A schema that does not parse is simply not offered to middleware.
      }
    }
    const base = (toolCtx: ToolCallContext) =>
      this._opts.toolManager.callTool(toolCtx.toolName, toolCtx.arguments, toolCtx.ctx);
    const chain = Middleware.buildChain(this._opts.middlewares, base);
    return chain({ toolName: name, arguments: args, ctx, inputSchema });
  }

  async *callTool(
    request: CallToolRequest,
    context: CallContext,
  ): AsyncGenerator<DeepPartial<CallToolEvent>> {
    this._checkMeta(request.meta, context);
    const name = request.name;

    // Events the tool emits and the "stop reading" marker share one queue, so
    // everything emitted before the tool settles is delivered before its result.
    const DONE = Symbol("done");
    const queue = new AsyncQueue<DeepPartial<CallToolEvent> | typeof DONE>();
    const ctx = new V2Context(request.meta!, (event) => queue.enqueue(event), context.signal);
    const stop = () => queue.enqueue(DONE);
    context.signal.addEventListener("abort", stop, { once: true });

    const outcome = this._runTool(name, request.arguments, ctx).then(
      (result) => ({ result }),
      (error: unknown) => ({ error }),
    );
    void outcome.then(stop);

    try {
      for (;;) {
        const item = await queue.dequeue();
        if (item === DONE) break;
        yield item;
      }
    } finally {
      context.signal.removeEventListener("abort", stop);
    }
    // Cancelled, or past its deadline: nobody is waiting for a result.
    if (context.signal.aborted) return;

    const settled = await outcome;
    if ("error" in settled) throw this._failure(settled.error, `Tool call '${name}' failed`, context);
    const { content, isError, structuredContent } = settled.result;
    yield {
      event: {
        $case: "complete",
        complete: {
          meta: this._resultMeta(),
          content,
          isError,
          structuredContent: structuredContent === undefined ? "" : JSON.stringify(structuredContent),
        },
      },
    };
  }

  async *readResource(
    request: ReadResourceRequest,
    context: CallContext,
  ): AsyncGenerator<DeepPartial<ReadResourceEvent>> {
    this._checkMeta(request.meta, context);
    let content;
    try {
      content = await this._opts.resourceManager.readResource(request.uri);
    } catch (err) {
      throw this._failure(err, `Resource handler for '${request.uri}' failed`, context);
    }
    yield { event: { $case: "complete", complete: { meta: this._resultMeta(), content, cache: NO_CACHE } } };
  }

  async *getPrompt(
    request: GetPromptRequest,
    context: CallContext,
  ): AsyncGenerator<DeepPartial<GetPromptEvent>> {
    this._checkMeta(request.meta, context);
    const name = request.name;
    const prompt = this._opts.promptManager.listPrompts().find((p) => p.name === name);
    if (prompt) {
      const missing = prompt.arguments
        .filter((a) => a.required && !(a.name in request.arguments))
        .map((a) => a.name);
      if (missing.length > 0) {
        throw toServerError(
          new McpError(
            ErrorCode.InvalidParams,
            `Missing required argument(s) for prompt '${name}': ${missing.join(", ")}`,
          ),
          context.trailer,
        );
      }
    }
    let messages;
    try {
      messages = await this._opts.promptManager.getPrompt(name, request.arguments);
    } catch (err) {
      throw this._failure(err, `Prompt handler '${name}' failed`, context);
    }
    yield { event: { $case: "complete", complete: { meta: this._resultMeta(), messages } } };
  }
}
