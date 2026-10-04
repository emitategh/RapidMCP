/**
 * RapidMCP — gRPC-native MCP server.
 *
 * High-level API for registering tools, resources, prompts,
 * and middleware, then serving them over a gRPC bidirectional stream.
 */
import { randomBytes } from "node:crypto";
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
import { McpDefinition as McpV2Definition } from "../generated/mcp_v2.js";
import { McpV2Servicer } from "./v2/servicer.js";
import { Listeners } from "./v2/listeners.js";
import { AsyncQueue } from "./session.js";
import {
  authMiddleware,
  buildServerCredentials,
  type ServerTlsConfig,
  type TokenVerifier,
} from "./auth.js";
import { prefixResourceUri } from "./_utils.js";
import type { Middleware } from "./middleware.js";
import type { ToolConfig } from "./tools/tool.js";
import type { ResourceConfig, ResourceTemplateConfig } from "./resources/resource.js";
import type { PromptConfig } from "./prompts/prompt.js";

export interface RapidMCPOptions {
  name: string;
  version?: string;
  pageSize?: number;
  /** Verify the bearer token of every incoming session; reject when it returns false. */
  auth?: TokenVerifier;
  /** Serve over TLS (and mTLS when `ca` is set). */
  tls?: ServerTlsConfig;
  /** Signs the request_state v2 tools hand to clients between input rounds. Replicas must share it. */
  stateSecret?: string | Uint8Array;
  /** Report "Error calling tool 'x'" without the exception text (ToolError messages still pass). */
  maskErrorDetails?: boolean;
}

export interface ListenOptions {
  port?: number;
  host?: string;
}

export class RapidMCP {
  private _name: string;
  private _version: string;
  private _pageSize: number | undefined;
  private _auth: TokenVerifier | undefined;
  private _tls: ServerTlsConfig | undefined;
  private _stateSecret: Uint8Array;
  private _stateSecretConfigured: boolean;

  private _toolManager: ToolManager;
  private _resourceManager = new ResourceManager();
  private _promptManager = new PromptManager();
  private _middlewares: Middleware[] = [];
  private _subscribeHandlers: Array<(uri: string) => void | Promise<void>> = [];
  private _rootsListChangedHandlers: Array<() => void | Promise<void>> = [];

  private _server: Server | null = null;
  private _sessions = new Set<AsyncQueue<DeepPartial<ServerEnvelope> | null>>();
  private _listeners = new Listeners();

  constructor(opts: RapidMCPOptions) {
    this._name = opts.name;
    this._version = opts.version ?? "0.1.0";
    this._pageSize = opts.pageSize;
    this._auth = opts.auth;
    this._tls = opts.tls;
    this._stateSecretConfigured = opts.stateSecret !== undefined;
    this._stateSecret =
      opts.stateSecret === undefined
        ? randomBytes(32)
        : typeof opts.stateSecret === "string"
          ? new TextEncoder().encode(opts.stateSecret)
          : opts.stateSecret;
    this._toolManager = new ToolManager({ maskErrorDetails: opts.maskErrorDetails });
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

  /**
   * Merge everything registered on *sub* into this server under *prefix*.
   *
   * Tools and prompts become `{prefix}_{name}`; resources and resource
   * templates get *prefix* as the first path segment (`res://x` ->
   * `res://{prefix}/x`). *sub* only donates its registrations — its
   * middleware and handlers are not adopted; this server's middleware wraps
   * the mounted tools.
   *
   * Throws on any name/URI collision, in which case nothing is registered.
   */
  mount(sub: RapidMCP, opts: { prefix: string }): void {
    const { prefix } = opts;
    const tools = sub._toolManager
      .listTools()
      .map((t) => ({ ...t, name: `${prefix}_${t.name}` }));
    const prompts = sub._promptManager
      .listPrompts()
      .map((p) => ({ ...p, name: `${prefix}_${p.name}` }));
    const resources = sub._resourceManager
      .listResources()
      .map((r) => ({ ...r, uri: prefixResourceUri(r.uri, prefix) }));
    const templates = sub._resourceManager
      .listResourceTemplates()
      .map((t) => ({ ...t, uriTemplate: prefixResourceUri(t.uriTemplate, prefix) }));

    const collision = (kind: string, key: string) =>
      new Error(`mount(prefix=${JSON.stringify(prefix)}): ${kind} collision '${key}'`);
    for (const t of tools) {
      if (this._toolManager.getTool(t.name)) throw collision("tool", t.name);
    }
    for (const p of prompts) {
      if (this._promptManager.has(p.name)) throw collision("prompt", p.name);
    }
    for (const r of resources) {
      if (this._resourceManager.hasResource(r.uri)) throw collision("resource", r.uri);
    }
    for (const t of templates) {
      if (this._resourceManager.hasResourceTemplate(t.uriTemplate)) {
        throw collision("resource template", t.uriTemplate);
      }
    }

    for (const t of tools) this._toolManager.register(t);
    for (const p of prompts) this._promptManager.register(p);
    for (const r of resources) this._resourceManager.registerResource(r);
    for (const t of templates) this._resourceManager.registerResourceTemplate(t);
  }

  /** Run *handler* with the uri whenever a client subscribes to a resource. */
  onResourceSubscribe(handler: (uri: string) => void | Promise<void>): void {
    this._subscribeHandlers.push(handler);
  }

  /** Run *handler* whenever a client reports that its roots changed. */
  onRootsListChanged(handler: () => void | Promise<void>): void {
    this._rootsListChangedHandlers.push(handler);
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
      subscribeHandlers: this._subscribeHandlers,
      rootsListChangedHandlers: this._rootsListChangedHandlers,
      onSessionAdd: (queue) => {
        this._sessions.add(queue);
      },
      onSessionRemove: (queue) => {
        this._sessions.delete(queue);
      },
    });

    const v2Servicer = new McpV2Servicer({
      name: this._name,
      version: this._version,
      toolManager: this._toolManager,
      resourceManager: this._resourceManager,
      promptManager: this._promptManager,
      middlewares: this._middlewares,
      pageSize: this._pageSize,
      stateSecret: this._stateSecret,
      stateSecretConfigured: this._stateSecretConfigured,
      authEnabled: this._auth !== undefined,
      listeners: this._listeners,
      subscribeHandlers: this._subscribeHandlers,
    });

    this._server = createServer();
    const registrar = this._auth ? this._server.with(authMiddleware(this._auth)) : this._server;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- DeepPartial union type mismatch
    registrar.add(McpDefinition, servicer as any);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- DeepPartial union type mismatch
    registrar.add(McpV2Definition, v2Servicer as any);
    const listenAddr = `${host}:${port}`;
    const credentials = this._tls ? buildServerCredentials(this._tls) : undefined;
    const actualPort = await this._server.listen(listenAddr, credentials);
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
    this._listeners.toolsListChanged();
    this._broadcast(ServerNotification_Type.TOOLS_LIST_CHANGED, "");
  }

  notifyResourcesListChanged(): void {
    this._listeners.resourcesListChanged();
    this._broadcast(ServerNotification_Type.RESOURCES_LIST_CHANGED, "");
  }

  notifyResourceUpdated(uri: string): void {
    this._listeners.resourceUpdated(uri);
    this._broadcast(ServerNotification_Type.RESOURCE_UPDATED, JSON.stringify({ uri }));
  }

  notifyPromptsListChanged(): void {
    this._listeners.promptsListChanged();
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
