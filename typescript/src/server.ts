/**
 * RapidMCP — gRPC-native MCP server.
 *
 * High-level API for registering tools, resources, prompts,
 * and middleware, then serving them over a gRPC bidirectional stream.
 */
import { createServer, type Server } from "nice-grpc";
import {
  McpDefinition,
  type DeepPartial,
  type ServerEnvelope,
  ServerNotification_Type,
} from "../generated/mcp.js";
import { ToolManager } from "./tools/tool-manager.js";
import { ResourceManager } from "./resources/resource-manager.js";
import { PromptManager } from "./prompts/prompt-manager.js";
import { McpServicer } from "./servicer.js";
import { AsyncQueue } from "./session.js";
import type { Middleware } from "./middleware.js";
import type { ToolConfig } from "./tools/tool.js";
import type { ResourceConfig, ResourceTemplateConfig } from "./resources/resource.js";
import type { PromptConfig } from "./prompts/prompt.js";

export interface RapidMCPOptions {
  name: string;
  version?: string;
  pageSize?: number;
}

export interface ListenOptions {
  port?: number;
  host?: string;
}

export class RapidMCP {
  private _name: string;
  private _version: string;
  private _pageSize: number | undefined;

  private _toolManager = new ToolManager();
  private _resourceManager = new ResourceManager();
  private _promptManager = new PromptManager();
  private _middlewares: Middleware[] = [];

  private _server: Server | null = null;
  private _sessions = new Set<AsyncQueue<DeepPartial<ServerEnvelope> | null>>();

  constructor(opts: RapidMCPOptions) {
    this._name = opts.name;
    this._version = opts.version ?? "0.1.0";
    this._pageSize = opts.pageSize;
  }

  // ── Registration ──────────────────────────────────────────

  addTool<T>(config: ToolConfig<T>): void {
    this._toolManager.addTool(config);
  }

  addResource(config: ResourceConfig): void {
    this._resourceManager.addResource(config);
  }

  addResourceTemplate(config: ResourceTemplateConfig): void {
    this._resourceManager.addResourceTemplate(config);
  }

  addPrompt(config: PromptConfig): void {
    this._promptManager.addPrompt(config);
  }

  use(middleware: Middleware): void {
    this._middlewares.push(middleware);
  }

  // ── Lifecycle ─────────────────────────────────────────────

  async listen(opts: ListenOptions = {}): Promise<number> {
    const host = opts.host ?? "127.0.0.1";
    const port = opts.port ?? 0;

    const servicer = new McpServicer({
      name: this._name,
      version: this._version,
      toolManager: this._toolManager,
      resourceManager: this._resourceManager,
      promptManager: this._promptManager,
      middlewares: this._middlewares,
      pageSize: this._pageSize,
      onSessionAdd: (queue) => {
        this._sessions.add(queue);
      },
      onSessionRemove: (queue) => {
        this._sessions.delete(queue);
      },
    });

    this._server = createServer();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- DeepPartial union type mismatch
    this._server.add(McpDefinition, servicer as any);
    const listenAddr = `${host}:${port}`;
    const actualPort = await this._server.listen(listenAddr);
    return actualPort;
  }

  async close(): Promise<void> {
    if (this._server) {
      this._server.forceShutdown();
      this._server = null;
    }
    this._sessions.clear();
  }

  // ── Broadcast notifications ───────────────────────────────

  notifyToolsListChanged(): void {
    this._broadcast(ServerNotification_Type.TOOLS_LIST_CHANGED, "");
  }

  notifyResourcesListChanged(): void {
    this._broadcast(ServerNotification_Type.RESOURCES_LIST_CHANGED, "");
  }

  notifyResourceUpdated(uri: string): void {
    this._broadcast(ServerNotification_Type.RESOURCE_UPDATED, JSON.stringify({ uri }));
  }

  notifyPromptsListChanged(): void {
    this._broadcast(ServerNotification_Type.PROMPTS_LIST_CHANGED, "");
  }

  private _broadcast(type: ServerNotification_Type, payload: string): void {
    const envelope: DeepPartial<ServerEnvelope> = {
      requestId: 0n,
      message: {
        $case: "notification" as const,
        notification: { type, payload },
      },
    };
    for (const queue of this._sessions) {
      queue.enqueue(envelope);
    }
  }

  // ── Accessors (for testing / advanced use) ────────────────

  get toolManager(): ToolManager {
    return this._toolManager;
  }

  get resourceManager(): ResourceManager {
    return this._resourceManager;
  }

  get promptManager(): PromptManager {
    return this._promptManager;
  }
}
