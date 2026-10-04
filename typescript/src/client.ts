/**
 * Client — gRPC-native MCP client.
 *
 * Connects to an MCP server via a single bidirectional gRPC stream
 * and exposes the full MCP API surface.
 */
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import type { CallOptions } from "nice-grpc-common";
import {
  McpDefinition,
  type ClientEnvelope,
  type ServerEnvelope,
  type DeepPartial,
  ServerNotification_Type,
  ClientNotification_Type,
  type SamplingRequest,
  type SamplingResponse,
  type ElicitationRequest,
  type ElicitationResponse,
  type Root,
  type ListRootsResponse,
} from "../generated/mcp.js";
import { ErrorCode, McpError } from "./errors.js";
import {
  V2Transport,
  isV2Missing,
  type ElicitRequestInfo,
} from "./v2/client-transport.js";
import { AsyncQueue, PendingRequests, NotificationRegistry, withTimeout } from "./session.js";
import { buildChannelCredentials, buildMetadata, type ClientOptions } from "./auth.js";
import {
  type Tool,
  type CallToolResult,
  type Resource,
  type ReadResourceResult,
  type ResourceTemplate,
  type Prompt,
  type GetPromptResult,
  type CompleteResult,
  type ListResult,
  type ServerInfo,
  convertTool,
  convertResource,
  convertResourceTemplate,
  convertPrompt,
  convertCallToolResult,
  convertReadResourceResult,
  convertGetPromptResult,
  convertCompleteResult,
} from "./types.js";

/** Map ServerNotification_Type enum to snake_case string. */
const NOTIFICATION_TYPE_MAP: Record<number, string> = {
  [ServerNotification_Type.TOOLS_LIST_CHANGED]: "tools_list_changed",
  [ServerNotification_Type.RESOURCES_LIST_CHANGED]: "resources_list_changed",
  [ServerNotification_Type.RESOURCE_UPDATED]: "resource_updated",
  [ServerNotification_Type.PROMPTS_LIST_CHANGED]: "prompts_list_changed",
  [ServerNotification_Type.PROGRESS]: "progress",
  [ServerNotification_Type.LOG]: "log",
};

export class Client {
  private _target: string;
  private _opts: ClientOptions;
  private _requestTimeout: number;

  private _channel: Channel | null = null;
  private _sendQueue = new AsyncQueue<DeepPartial<ClientEnvelope> | null>();
  private _pending = new PendingRequests();
  private _notifications = new NotificationRegistry();
  private _readerDone: Promise<void> | null = null;
  private _serverInfo: ServerInfo | null = null;
  private _refCount = 0;
  private _connected = false;
  private _mode: "legacy" | "modern" | "auto";
  private _v2: V2Transport | null = null;
  /** True while a reader loop is consuming the current stream. */
  private _streamOpen = false;
  /** In-flight connect(), shared by concurrent callers. */
  private _connecting: Promise<void> | null = null;
  /** Bumped per connection so a stale reader loop cannot touch a newer one. */
  private _generation = 0;

  private _samplingHandler: ((req: SamplingRequest) => Promise<SamplingResponse>) | null = null;
  private _elicitationHandler:
    | ((req: ElicitRequestInfo) => Promise<{ action: string; content?: string }>)
    | null = null;
  private _elicitationUrl = false;
  private _rootsHandler: (() => Promise<Root[]>) | null = null;

  constructor(target: string, opts: ClientOptions = {}) {
    this._target = target;
    this._opts = opts;
    this._requestTimeout = opts.requestTimeout ?? 30_000;
    this._mode = opts.mode ?? "legacy";
  }

  /** Server info populated after connect(). */
  get serverInfo(): ServerInfo | null {
    return this._serverInfo;
  }

  /** Whether the client is currently connected to the server. */
  get isConnected(): boolean {
    return this._connected;
  }

  /** "v2" or "v1" once connected, null before. */
  get protocol(): "v1" | "v2" | null {
    if (this._v2) return "v2";
    return this._connected ? "v1" : null;
  }

  /** Connect to the server: open channel, start bidi stream, run initialize handshake. */
  async connect(): Promise<void> {
    if (this._connected) return;
    if (!this._connecting) {
      this._connecting = this._doConnect().finally(() => {
        this._connecting = null;
      });
    }
    return this._connecting;
  }

  private async _doConnect(): Promise<void> {
    // Drop whatever a previous, now-dead connection left behind.
    this._channel?.close();
    this._pending.rejectAll(new McpError(ErrorCode.NotConnected, "Connection closed"));
    this._sendQueue = new AsyncQueue<DeepPartial<ClientEnvelope> | null>();
    const generation = ++this._generation;

    const credentials = buildChannelCredentials(this._opts);
    this._channel = createChannel(this._target, credentials);

    if (this._mode !== "legacy") {
      const transport = new V2Transport(
        this._channel,
        this._opts,
        this._requestTimeout,
        () => ({ handler: this._elicitationHandler, url: this._elicitationUrl }),
        this._notifications,
      );
      try {
        this._serverInfo = await transport.discover();
        this._v2 = transport;
        this._connected = true;
        return;
      } catch (err) {
        if (!(this._mode === "auto" && isV2Missing(err))) {
          this._channel.close();
          this._channel = null;
          throw err;
        }
        // An old server: carry on with the v1 stream on the same channel.
      }
    }

    const grpcClient = createClientFactory().create(McpDefinition, this._channel);

    // Build call options with metadata if token is set
    const callOpts: CallOptions = {};
    if (this._opts.token) {
      callOpts.metadata = buildMetadata(this._opts);
    }

    // Create the async iterable that feeds outbound envelopes
    const sendQueue = this._sendQueue;
    async function* requestIterable(): AsyncGenerator<DeepPartial<ClientEnvelope>> {
      while (true) {
        const item = await sendQueue.dequeue();
        if (item === null) return; // stream closed
        yield item;
      }
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- DeepPartial union type mismatch
    const responseStream = grpcClient.session(requestIterable() as any, callOpts);

    // Start reader loop
    this._streamOpen = true;
    this._readerDone = this._readLoop(responseStream, generation);

    // Initialize handshake
    const initResponse = (await this._request({
      message: {
        $case: "initialize" as const,
        initialize: {
          clientName: "rapidmcp-typescript",
          clientVersion: "0.1.0",
          capabilities: {
            sampling: this._samplingHandler !== null,
            elicitation: this._elicitationHandler !== null,
            roots: this._rootsHandler !== null,
          },
        },
      },
    })) as { serverName: string; serverVersion: string; capabilities?: { tools: boolean; toolsListChanged: boolean; resources: boolean; prompts: boolean } };

    this._serverInfo = {
      serverName: initResponse.serverName,
      serverVersion: initResponse.serverVersion,
      capabilities: {
        tools: initResponse.capabilities?.tools ?? false,
        toolsListChanged: initResponse.capabilities?.toolsListChanged ?? false,
        resources: initResponse.capabilities?.resources ?? false,
        prompts: initResponse.capabilities?.prompts ?? false,
      },
    };

    // Send initialized ack (fire-and-forget, no response expected)
    this._sendQueue.enqueue({
      requestId: 0n,
      message: { $case: "initialized" as const, initialized: {} },
    });

    this._connected = true;
  }

  /** Reader loop — dispatches incoming server envelopes. */
  private async _readLoop(stream: AsyncIterable<ServerEnvelope>, generation: number): Promise<void> {
    try {
      for await (const envelope of stream) {
        const msg = envelope.message;
        if (!msg) continue;

        switch (msg.$case) {
          case "error": {
            const err = msg.error;
            this._pending.reject(
              envelope.requestId,
              new McpError(err.code, err.message),
            );
            break;
          }

          case "notification": {
            const notif = msg.notification;
            const typeName = NOTIFICATION_TYPE_MAP[notif.type] ?? "unknown";
            // Fire-and-forget — don't block reader. A throwing handler must not
            // surface as an unhandled rejection (which kills the process).
            this._notifications.dispatch(typeName, notif.payload).catch((err) => {
              console.error(`[rapidmcp] notification handler for '${typeName}' failed:`, err);
            });
            break;
          }

          case "sampling": {
            const rid = envelope.requestId;
            if (this._samplingHandler) {
              this._handleServerPush(rid, async () => {
                const result = await this._samplingHandler!(msg.sampling);
                this._sendQueue.enqueue({
                  requestId: rid,
                  message: { $case: "samplingReply", samplingReply: result },
                });
              });
            } else {
              this._sendQueue.enqueue({
                requestId: rid,
                message: { $case: "error", error: { code: -32600, message: "sampling not supported by this client" } },
              });
            }
            break;
          }

          case "elicitation": {
            const rid = envelope.requestId;
            if (this._elicitationHandler) {
              this._handleServerPush(rid, async () => {
                const result = await this._elicitationHandler!({
                  message: msg.elicitation.message,
                  schema: msg.elicitation.schema,
                  mode: "form",
                  url: "",
                });
                this._sendQueue.enqueue({
                  requestId: rid,
                  message: {
                    $case: "elicitationReply",
                    elicitationReply: { action: result.action, content: result.content ?? "" },
                  },
                });
              });
            } else {
              this._sendQueue.enqueue({
                requestId: rid,
                message: { $case: "error", error: { code: -32600, message: "elicitation not supported by this client" } },
              });
            }
            break;
          }

          case "rootsRequest": {
            const rid = envelope.requestId;
            if (this._rootsHandler) {
              this._handleServerPush(rid, async () => {
                const roots = await this._rootsHandler!();
                this._sendQueue.enqueue({
                  requestId: rid,
                  message: { $case: "rootsReply", rootsReply: { roots } },
                });
              });
            } else {
              this._sendQueue.enqueue({
                requestId: rid,
                message: { $case: "error", error: { code: -32600, message: "roots not supported by this client" } },
              });
            }
            break;
          }

          default: {
            // Regular response — resolve the pending request.
            // Extract the inner message value (the value at the $case key).
            const innerKey = msg.$case as string;
            const inner = (msg as Record<string, unknown>)[innerKey];
            this._pending.resolve(envelope.requestId, inner);
            break;
          }
        }
      }
    } catch (err) {
      // Stream error — reject all pending requests
      if (generation === this._generation) {
        this._pending.rejectAll(err instanceof Error ? err : new Error(String(err)));
      }
    } finally {
      // The stream is over, however it ended: nothing will answer from here on.
      if (generation === this._generation) {
        this._streamOpen = false;
        this._connected = false;
        this._pending.rejectAll(new McpError(ErrorCode.NotConnected, "Connection closed"));
      }
    }
  }

  /** Run a server-push handler as fire-and-forget; send error reply on failure. */
  private _handleServerPush(rid: bigint, fn: () => Promise<void>): void {
    fn().catch(() => {
      this._sendQueue.enqueue({
        requestId: rid,
        message: { $case: "error", error: { code: -32603, message: "Handler failed" } },
      });
    });
  }

  /** Throw for operations the v2 protocol does not carry yet. */
  private _v1Only(operation: string): void {
    if (this._v2) {
      throw new McpError(
        ErrorCode.MethodNotFound,
        `${operation} is not available on the v2 protocol yet; use mode: "legacy"`,
      );
    }
  }

  /** Fail now rather than at the request timeout when nothing is reading replies. */
  private _assertStreamOpen(): void {
    if (!this._streamOpen) {
      throw new McpError(ErrorCode.NotConnected, `Not connected to ${this._target}`);
    }
  }

  /** Send a request envelope and wait for the correlated response. */
  private async _request(
    envelope: Omit<DeepPartial<ClientEnvelope>, "requestId">,
  ): Promise<unknown> {
    this._assertStreamOpen();
    const requestId = this._pending.nextId();
    const promise = this._pending.create(requestId);

    this._sendQueue.enqueue({ ...envelope, requestId } as DeepPartial<ClientEnvelope>);

    return withTimeout(promise, this._requestTimeout, () => {
      this._pending.discard(requestId);
      return new McpError(ErrorCode.RequestTimeout, "Request timeout");
    });
  }

  // ── Public API ────────────────────────────────────────────

  async listTools(cursor?: string): Promise<ListResult<Tool>> {
    if (this._v2) return this._v2.listTools(cursor);
    const resp = (await this._request({
      message: {
        $case: "listTools" as const,
        listTools: { cursor: cursor ?? "" },
      },
    })) as { tools: unknown[]; nextCursor: string };
    return {
      items: resp.tools.map((t) => convertTool(t as Parameters<typeof convertTool>[0])),
      nextCursor: resp.nextCursor || null,
    };
  }

  async callTool(
    name: string,
    args: Record<string, unknown> = {},
    opts?: {
      signal?: AbortSignal;
      /** Milliseconds to wait for this call; overrides the client's requestTimeout. */
      timeout?: number;
    },
  ): Promise<CallToolResult> {
    if (this._v2) return this._v2.callTool(name, args, opts);
    if (opts?.signal?.aborted) {
      throw new McpError(-1, "Aborted");
    }
    this._assertStreamOpen();

    const requestId = this._pending.nextId();
    const promise = this._pending.create(requestId);

    this._sendQueue.enqueue({
      requestId,
      message: {
        $case: "callTool" as const,
        callTool: { name, arguments: JSON.stringify(args) },
      },
    } as DeepPartial<ClientEnvelope>);

    const timed = withTimeout(promise, opts?.timeout ?? this._requestTimeout, () => {
      // We stopped waiting — don't leave the tool running on the server.
      this._pending.discard(requestId);
      void this.cancel(requestId);
      return new McpError(ErrorCode.RequestTimeout, "Request timeout");
    });
    const racers: Promise<unknown>[] = [timed];

    // AbortSignal
    if (opts?.signal) {
      const signal = opts.signal;
      const abortPromise = new Promise<never>((_, reject) => {
        const onAbort = () => {
          this.cancel(requestId);
          reject(new McpError(-1, "Aborted"));
        };
        signal.addEventListener("abort", onAbort, { once: true });
        // Clean up listener once the call settles or times out. then(f, f) rather than
        // finally(): finally() returns a promise that re-rejects with nobody listening.
        const cleanup = () => signal.removeEventListener("abort", onAbort);
        timed.then(cleanup, cleanup);
      });
      racers.push(abortPromise);
    }

    const resp = (await Promise.race(racers)) as Parameters<typeof convertCallToolResult>[0];
    return convertCallToolResult(resp);
  }

  async listResources(cursor?: string): Promise<ListResult<Resource>> {
    if (this._v2) return this._v2.listResources(cursor);
    const resp = (await this._request({
      message: {
        $case: "listResources" as const,
        listResources: { cursor: cursor ?? "" },
      },
    })) as { resources: unknown[]; nextCursor: string };
    return {
      items: resp.resources.map((r) => convertResource(r as Parameters<typeof convertResource>[0])),
      nextCursor: resp.nextCursor || null,
    };
  }

  async readResource(uri: string): Promise<ReadResourceResult> {
    if (this._v2) return this._v2.readResource(uri);
    const resp = (await this._request({
      message: {
        $case: "readResource" as const,
        readResource: { uri },
      },
    })) as Parameters<typeof convertReadResourceResult>[0];
    return convertReadResourceResult(resp);
  }

  subscribeResource(uri: string): void {
    this._v1Only("subscribeResource");
    this._sendQueue.enqueue({
      requestId: 0n,
      message: {
        $case: "subscribeRes" as const,
        subscribeRes: { uri },
      },
    });
  }

  async listResourceTemplates(cursor?: string): Promise<ListResult<ResourceTemplate>> {
    if (this._v2) return this._v2.listResourceTemplates(cursor);
    const resp = (await this._request({
      message: {
        $case: "listResourceTemplates" as const,
        listResourceTemplates: { cursor: cursor ?? "" },
      },
    })) as { templates: unknown[]; nextCursor: string };
    return {
      items: resp.templates.map((t) =>
        convertResourceTemplate(t as Parameters<typeof convertResourceTemplate>[0]),
      ),
      nextCursor: resp.nextCursor || null,
    };
  }

  async listPrompts(cursor?: string): Promise<ListResult<Prompt>> {
    if (this._v2) return this._v2.listPrompts(cursor);
    const resp = (await this._request({
      message: {
        $case: "listPrompts" as const,
        listPrompts: { cursor: cursor ?? "" },
      },
    })) as { prompts: unknown[]; nextCursor: string };
    return {
      items: resp.prompts.map((p) => convertPrompt(p as Parameters<typeof convertPrompt>[0])),
      nextCursor: resp.nextCursor || null,
    };
  }

  async getPrompt(
    name: string,
    args: Record<string, string> = {},
  ): Promise<GetPromptResult> {
    if (this._v2) return this._v2.getPrompt(name, args);
    const resp = (await this._request({
      message: {
        $case: "getPrompt" as const,
        getPrompt: { name, arguments: args },
      },
    })) as Parameters<typeof convertGetPromptResult>[0];
    return convertGetPromptResult(resp);
  }

  async complete(
    refType: string,
    refName: string,
    argName: string,
    argValue: string,
  ): Promise<CompleteResult> {
    if (this._v2) return this._v2.complete(refType, refName, argName, argValue);
    const resp = (await this._request({
      message: {
        $case: "complete" as const,
        complete: {
          ref: { type: refType, name: refName },
          argument: { name: argName, value: argValue },
        },
      },
    })) as Parameters<typeof convertCompleteResult>[0];
    return convertCompleteResult(resp);
  }

  async ping(): Promise<boolean> {
    if (this._v2) {
      await this._v2.discover();
      return true;
    }
    await this._request({
      message: { $case: "ping" as const, ping: {} },
    });
    return true;
  }

  /**
   * Stop waiting for a request and tell the server to stop working on it.
   * The pending call rejects here with McpError 499; the server sends no
   * response for a cancelled request.
   */
  async cancel(targetRequestId: bigint): Promise<void> {
    this._v1Only("cancel");
    this._pending.reject(
      targetRequestId,
      new McpError(ErrorCode.RequestCancelled, "Request cancelled"),
    );
    this._sendQueue.enqueue({
      requestId: 0n,
      message: {
        $case: "cancel" as const,
        cancel: { targetRequestId },
      },
    });
  }

  notifyRootsListChanged(): void {
    this._v1Only("notifyRootsListChanged");
    this._sendQueue.enqueue({
      requestId: 0n,
      message: {
        $case: "clientNotification" as const,
        clientNotification: {
          type: ClientNotification_Type.ROOTS_LIST_CHANGED,
          payload: "",
        },
      },
    });
  }

  onNotification(
    type: string,
    handler: (payload: string) => void | Promise<void>,
  ): void {
    this._notifications.register(type, handler);
  }

  /** Register a handler for server-initiated sampling requests. */
  setSamplingHandler(handler: (req: SamplingRequest) => Promise<SamplingResponse>): void {
    this._samplingHandler = handler;
  }

  /**
   * Register a handler for the server's questions. It receives the message and,
   * for a form, the JSON Schema text; on v2 also the mode and, for url mode, the
   * url. Pass `{ url: true }` if it can also send the user to a URL.
   */
  setElicitationHandler(
    handler: (req: ElicitRequestInfo) => Promise<{ action: string; content?: string }>,
    opts: { url?: boolean } = {},
  ): void {
    this._elicitationHandler = handler;
    this._elicitationUrl = opts.url ?? false;
  }

  /** Register a handler for server-initiated roots requests. */
  setRootsHandler(handler: () => Promise<Root[]>): void {
    this._rootsHandler = handler;
  }

  // ── Lifecycle ─────────────────────────────────────────────

  /** Increment ref count and connect if needed. Returns this for chaining. */
  async using(): Promise<Client> {
    this._refCount++;
    if (!this._connected) {
      await this.connect();
    }
    return this;
  }

  /** Decrement ref count and close when it reaches zero. */
  async release(): Promise<void> {
    this._refCount = Math.max(0, this._refCount - 1);
    if (this._refCount === 0) {
      await this.close();
    }
  }

  /** Async dispose — calls release(). */
  async [Symbol.asyncDispose](): Promise<void> {
    await this.release();
  }

  /** Close the connection: close send stream, await reader, cancel pending, close channel. */
  async close(): Promise<void> {
    if (this._v2) {
      this._v2 = null;
      this._channel?.close();
      this._channel = null;
      this._connected = false;
      this._serverInfo = null;
      return;
    }
    if (!this._connected && !this._readerDone) return;

    // Signal the send generator to stop
    this._sendQueue.enqueue(null);

    // Wait for reader to finish
    if (this._readerDone) {
      await this._readerDone.catch(() => {});
      this._readerDone = null;
    }

    // Cancel any remaining pending requests
    this._pending.cancelAll();

    // Close channel
    if (this._channel) {
      this._channel.close();
      this._channel = null;
    }

    this._connected = false;
    this._serverInfo = null;
  }
}
