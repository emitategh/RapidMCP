/**
 * McpServicer — gRPC session handler.
 *
 * Implements the nice-grpc McpServiceImplementation interface.
 * For each client connection, runs a concurrent reader/writer over
 * a single bidirectional gRPC stream.
 */
import type { CallContext } from "nice-grpc-common";
import {
  type ClientEnvelope,
  type ServerEnvelope,
  type DeepPartial,
  type ClientCapabilities,
  ServerNotification_Type,
  ClientNotification_Type,
  type McpServiceImplementation,
} from "../generated/mcp.js";
import { ErrorCode, McpError } from "./errors.js";
import { AsyncQueue, PendingRequests } from "./session.js";
import { Context } from "./context.js";
import { paginate } from "./_utils.js";
import type { ToolManager } from "./tools/tool-manager.js";
import type { ResourceManager } from "./resources/resource-manager.js";
import type { PromptManager } from "./prompts/prompt-manager.js";
import { Middleware, type CallToolResult, type ToolCallContext } from "./middleware.js";

export interface McpServicerOptions {
  name: string;
  version: string;
  toolManager: ToolManager;
  resourceManager: ResourceManager;
  promptManager: PromptManager;
  middlewares: Middleware[];
  pageSize?: number;
  /** Called with the uri when a client subscribes to a resource. */
  subscribeHandlers?: Array<(uri: string) => void | Promise<void>>;
  /** Called when a client reports that its roots changed. */
  rootsListChangedHandlers?: Array<() => void | Promise<void>>;
  /** Called when a new session becomes active (after initialized). */
  onSessionAdd?: (queue: AsyncQueue<DeepPartial<ServerEnvelope> | null>) => void;
  /** Called when a session ends. */
  onSessionRemove?: (queue: AsyncQueue<DeepPartial<ServerEnvelope> | null>) => void;
}

export class McpServicer implements McpServiceImplementation {
  private _name: string;
  private _version: string;
  private _toolManager: ToolManager;
  private _resourceManager: ResourceManager;
  private _promptManager: PromptManager;
  private _middlewares: Middleware[];
  private _pageSize: number | undefined;
  private _subscribeHandlers: Array<(uri: string) => void | Promise<void>>;
  private _rootsListChangedHandlers: Array<() => void | Promise<void>>;
  private _onSessionAdd?: (queue: AsyncQueue<DeepPartial<ServerEnvelope> | null>) => void;
  private _onSessionRemove?: (queue: AsyncQueue<DeepPartial<ServerEnvelope> | null>) => void;

  constructor(opts: McpServicerOptions) {
    this._name = opts.name;
    this._version = opts.version;
    this._toolManager = opts.toolManager;
    this._resourceManager = opts.resourceManager;
    this._promptManager = opts.promptManager;
    this._middlewares = opts.middlewares;
    this._pageSize = opts.pageSize;
    this._subscribeHandlers = opts.subscribeHandlers ?? [];
    this._rootsListChangedHandlers = opts.rootsListChangedHandlers ?? [];
    this._onSessionAdd = opts.onSessionAdd;
    this._onSessionRemove = opts.onSessionRemove;
  }

  async *session(
    request: AsyncIterable<ClientEnvelope>,
    _context: CallContext,
  ): AsyncGenerator<DeepPartial<ServerEnvelope>> {
    const writeQueue = new AsyncQueue<DeepPartial<ServerEnvelope> | null>();
    const pending = new PendingRequests();

    // Start reader in background
    const readerDone = this._processRequests(request, writeQueue, pending);

    // Yield from write queue until null sentinel
    try {
      while (true) {
        const item = await writeQueue.dequeue();
        if (item === null) break;
        yield item;
      }
    } finally {
      this._onSessionRemove?.(writeQueue);
      pending.rejectAll(new Error("session closed"));
    }

    await readerDone;
  }

  private async _processRequests(
    request: AsyncIterable<ClientEnvelope>,
    writeQueue: AsyncQueue<DeepPartial<ServerEnvelope> | null>,
    pending: PendingRequests,
  ): Promise<void> {
    let clientCapabilities: ClientCapabilities = {
      sampling: false,
      elicitation: false,
      roots: false,
    };
    // In-flight tool calls by client request id, so `cancel` can reach them.
    const toolCalls = new Map<bigint, AbortController>();

    try {
      for await (const envelope of request) {
        const msg = envelope.message;
        if (!msg) continue;
        const rid = envelope.requestId;

        switch (msg.$case) {
          case "initialize": {
            const init = msg.initialize;
            clientCapabilities = init.capabilities ?? {
              sampling: false,
              elicitation: false,
              roots: false,
            };
            writeQueue.enqueue({
              requestId: rid,
              message: {
                $case: "initialize" as const,
                initialize: {
                  serverName: this._name,
                  serverVersion: this._version,
                  capabilities: {
                    tools: this._toolManager.listTools().length > 0,
                    toolsListChanged: true,
                    resources:
                      this._resourceManager.listResources().length > 0 ||
                      this._resourceManager.listResourceTemplates().length > 0,
                    prompts: this._promptManager.listPrompts().length > 0,
                  },
                },
              },
            });
            break;
          }

          case "initialized": {
            // Session is now active — register for broadcasts
            this._onSessionAdd?.(writeQueue);
            break;
          }

          case "listTools": {
            const tools = this._toolManager.listTools();
            const defs = tools.map((t) => ({
              name: t.name,
              description: t.description,
              inputSchema: t.inputSchema,
              outputSchema: t.outputSchema,
              annotations: t.annotations
                ? {
                    title: t.annotations.title ?? "",
                    readOnlyHint: t.annotations.readOnly ?? false,
                    destructiveHint: t.annotations.destructive ?? false,
                    idempotentHint: t.annotations.idempotent ?? false,
                    openWorldHint: t.annotations.openWorld ?? false,
                  }
                : undefined,
            }));
            const [page, nextCursor] = paginate(defs, msg.listTools.cursor, this._pageSize);
            writeQueue.enqueue({
              requestId: rid,
              message: {
                $case: "listTools" as const,
                listTools: { tools: page, nextCursor },
              },
            });
            break;
          }

          case "callTool": {
            // Fire-and-forget: don't block the reader loop
            const callMsg = msg.callTool;
            const controller = new AbortController();
            toolCalls.set(rid, controller);
            void this._handleCallTool(
              rid,
              callMsg.name,
              callMsg.arguments,
              clientCapabilities,
              writeQueue,
              pending,
              controller.signal,
            ).finally(() => toolCalls.delete(rid));
            break;
          }

          case "listResources": {
            const resources = this._resourceManager.listResources();
            const defs = resources.map((r) => ({
              uri: r.uri,
              name: r.name,
              description: r.description,
              mimeType: r.mimeType,
            }));
            const [page, nextCursor] = paginate(defs, msg.listResources.cursor, this._pageSize);
            writeQueue.enqueue({
              requestId: rid,
              message: {
                $case: "listResources" as const,
                listResources: { resources: page, nextCursor },
              },
            });
            break;
          }

          case "readResource": {
            try {
              const content = await this._resourceManager.readResource(msg.readResource.uri);
              writeQueue.enqueue({
                requestId: rid,
                message: {
                  $case: "readResource" as const,
                  readResource: { content },
                },
              });
            } catch (err) {
              this._enqueueError(
                writeQueue,
                rid,
                err,
                `Resource handler for '${msg.readResource.uri}' failed`,
              );
            }
            break;
          }

          case "listResourceTemplates": {
            const templates = this._resourceManager.listResourceTemplates();
            const defs = templates.map((t) => ({
              uriTemplate: t.uriTemplate,
              name: t.name,
              description: t.description,
              mimeType: t.mimeType,
            }));
            const [page, nextCursor] = paginate(
              defs,
              msg.listResourceTemplates.cursor,
              this._pageSize,
            );
            writeQueue.enqueue({
              requestId: rid,
              message: {
                $case: "listResourceTemplates" as const,
                listResourceTemplates: { templates: page, nextCursor },
              },
            });
            break;
          }

          case "listPrompts": {
            const prompts = this._promptManager.listPrompts();
            const defs = prompts.map((p) => ({
              name: p.name,
              description: p.description,
              arguments: p.arguments.map((a) => ({
                name: a.name,
                description: a.description ?? "",
                required: a.required ?? false,
              })),
            }));
            const [page, nextCursor] = paginate(defs, msg.listPrompts.cursor, this._pageSize);
            writeQueue.enqueue({
              requestId: rid,
              message: {
                $case: "listPrompts" as const,
                listPrompts: { prompts: page, nextCursor },
              },
            });
            break;
          }

          case "getPrompt": {
            try {
              const messages = await this._promptManager.getPrompt(
                msg.getPrompt.name,
                msg.getPrompt.arguments,
              );
              writeQueue.enqueue({
                requestId: rid,
                message: {
                  $case: "getPrompt" as const,
                  getPrompt: { messages },
                },
              });
            } catch (err) {
              this._enqueueError(
                writeQueue,
                rid,
                err,
                `Prompt handler '${msg.getPrompt.name}' failed`,
              );
            }
            break;
          }

          case "complete": {
            try {
              const ref = msg.complete.ref;
              const arg = msg.complete.argument;
              const result = await this._promptManager.complete(
                ref?.type ?? "",
                ref?.name ?? "",
                arg?.name ?? "",
                arg?.value ?? "",
              );
              writeQueue.enqueue({
                requestId: rid,
                message: {
                  $case: "complete" as const,
                  complete: {
                    values: result.values,
                    hasMore: result.hasMore ?? false,
                    total: result.total ?? 0,
                  },
                },
              });
            } catch (err) {
              this._enqueueError(
                writeQueue,
                rid,
                err,
                `Completion handler for '${msg.complete.ref?.name ?? ""}' failed`,
              );
            }
            break;
          }

          case "samplingReply": {
            pending.resolve(rid, msg.samplingReply);
            break;
          }

          case "elicitationReply": {
            pending.resolve(rid, msg.elicitationReply);
            break;
          }

          case "rootsReply": {
            pending.resolve(rid, msg.rootsReply);
            break;
          }

          case "ping": {
            writeQueue.enqueue({
              requestId: rid,
              message: { $case: "pong" as const, pong: {} },
            });
            break;
          }

          case "cancel": {
            // targetRequestId is a client request id; `pending` holds server-initiated
            // ids from a separate counter, so it must not be touched here.
            toolCalls.get(msg.cancel.targetRequestId)?.abort();
            break;
          }

          case "subscribeRes": {
            const uri = msg.subscribeRes.uri;
            await this._runHandlers("resource subscribe", this._subscribeHandlers, (h) => h(uri));
            break;
          }

          case "clientNotification": {
            if (msg.clientNotification.type === ClientNotification_Type.ROOTS_LIST_CHANGED) {
              await this._runHandlers("roots list changed", this._rootsListChangedHandlers, (h) =>
                h(),
              );
            }
            break;
          }

          case "error": {
            // Client-side error response to a server-initiated request
            const errResp = msg.error;
            pending.reject(rid, new McpError(errResp.code, errResp.message));
            break;
          }
        }
      }
    } catch (err) {
      // Stream error — reject all pending requests
      pending.rejectAll(err instanceof Error ? err : new Error(String(err)));
    } finally {
      // Nobody is left to receive results — tell in-flight tools to stop.
      for (const controller of toolCalls.values()) controller.abort();
      // Signal the writer to stop
      writeQueue.enqueue(null);
    }
  }

  private async _handleCallTool(
    rid: bigint,
    name: string,
    argsJson: string,
    capabilities: ClientCapabilities,
    writeQueue: AsyncQueue<DeepPartial<ServerEnvelope> | null>,
    pending: PendingRequests,
    signal: AbortSignal,
  ): Promise<void> {
    try {
      let args: Record<string, unknown> = {};
      if (argsJson) {
        let parsed: unknown;
        try {
          parsed = JSON.parse(argsJson);
        } catch {
          throw new McpError(
            ErrorCode.InvalidParams,
            `Invalid arguments for tool '${name}': not valid JSON`,
          );
        }
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          throw new McpError(
            ErrorCode.InvalidParams,
            `Invalid arguments for tool '${name}': expected a JSON object`,
          );
        }
        args = parsed as Record<string, unknown>;
      }

      const tool = this._toolManager.getTool(name);
      if (!tool) {
        throw new McpError(ErrorCode.InvalidParams, `Tool '${name}' not found`);
      }

      // Build context for tools that need it
      const ctx = new Context(capabilities, pending, writeQueue, signal);

      // Parse input schema for middleware
      let inputSchema: Record<string, unknown> | null = null;
      if (tool.inputSchema && tool.inputSchema !== "{}") {
        try {
          inputSchema = JSON.parse(tool.inputSchema) as Record<string, unknown>;
        } catch {
          // Ignore parse errors in schema
        }
      }

      // Build middleware chain
      const base = async (toolCtx: ToolCallContext): Promise<CallToolResult> => {
        return this._toolManager.callTool(toolCtx.toolName, toolCtx.arguments, toolCtx.ctx);
      };

      const chain = Middleware.buildChain(this._middlewares, base);

      const result = await chain({
        toolName: name,
        arguments: args,
        ctx,
        inputSchema,
      });

      // No response for a cancelled call: the client has stopped waiting for it.
      if (signal.aborted) return;

      writeQueue.enqueue({
        requestId: rid,
        message: {
          $case: "callTool" as const,
          callTool: {
            content: result.content,
            isError: result.isError,
          },
        },
      });
    } catch (err) {
      if (signal.aborted) return;
      this._enqueueError(writeQueue, rid, err, `Tool call '${name}' failed`);
    }
  }

  /** Run user handlers one by one; a failing handler is logged, never fatal to the session. */
  private async _runHandlers<H>(
    label: string,
    handlers: H[],
    call: (handler: H) => void | Promise<void>,
  ): Promise<void> {
    for (const handler of handlers) {
      try {
        await call(handler);
      } catch (err) {
        console.error(`[rapidmcp] ${label} handler failed:`, err);
      }
    }
  }

  /**
   * Answer a request with an error. An McpError is a deliberate protocol error
   * and goes out as it is; anything else is the server's own code failing, so
   * the client gets *fallback* and the detail stays in the server log.
   */
  private _enqueueError(
    writeQueue: AsyncQueue<DeepPartial<ServerEnvelope> | null>,
    rid: bigint,
    err: unknown,
    fallback: string,
  ): void {
    const deliberate = err instanceof McpError;
    if (!deliberate) console.error(`[rapidmcp] ${fallback}:`, err);
    const code = deliberate ? err.code : ErrorCode.InternalError;
    const message = deliberate ? err.message : fallback;
    writeQueue.enqueue({
      requestId: rid,
      message: {
        $case: "error" as const,
        error: { code, message },
      },
    });
  }
}
