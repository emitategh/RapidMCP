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
} from "../generated/mcp.js";
import { McpError } from "./errors.js";
import { AsyncQueue, PendingRequests, NotificationRegistry } from "./session.js";
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

  constructor(target: string, opts: ClientOptions = {}) {
    this._target = target;
    this._opts = opts;
    this._requestTimeout = opts.requestTimeout ?? 30_000;
  }

  /** Server info populated after connect(). */
  get serverInfo(): ServerInfo | null {
    return this._serverInfo;
  }

  /** Connect to the server: open channel, start bidi stream, run initialize handshake. */
  async connect(): Promise<void> {
    if (this._connected) return;

    const credentials = buildChannelCredentials(this._opts);
    this._channel = createChannel(this._target, credentials);
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

    const responseStream = grpcClient.session(requestIterable(), callOpts);

    // Start reader loop
    this._readerDone = this._readLoop(responseStream);

    // Initialize handshake
    const initResponse = (await this._request({
      message: {
        $case: "initialize" as const,
        initialize: {
          clientName: "rapidmcp-ts",
          clientVersion: "0.1.0",
          capabilities: { sampling: false, elicitation: false, roots: false },
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
  private async _readLoop(stream: AsyncIterable<ServerEnvelope>): Promise<void> {
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
            // Fire-and-forget — don't block reader
            void this._notifications.dispatch(typeName, notif.payload);
            break;
          }

          case "sampling":
            // Server-initiated sampling — not implemented yet, ignore
            break;

          case "elicitation":
            // Server-initiated elicitation — not implemented yet, ignore
            break;

          case "rootsRequest":
            // Server requesting roots — not implemented yet, ignore
            break;

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
      this._pending.rejectAll(
        err instanceof Error ? err : new Error(String(err)),
      );
    }
  }

  /** Send a request envelope and wait for the correlated response. */
  private async _request(
    envelope: Omit<DeepPartial<ClientEnvelope>, "requestId">,
  ): Promise<unknown> {
    const requestId = this._pending.nextId();
    const promise = this._pending.create(requestId);

    this._sendQueue.enqueue({ ...envelope, requestId } as DeepPartial<ClientEnvelope>);

    // Race against timeout
    const timeoutPromise = new Promise<never>((_, reject) => {
      const timer = setTimeout(
        () => reject(new McpError(-1, "Request timeout")),
        this._requestTimeout,
      );
      // Unref so timer doesn't keep the process alive
      if (typeof timer === "object" && "unref" in timer) {
        (timer as NodeJS.Timeout).unref();
      }
    });

    return Promise.race([promise, timeoutPromise]);
  }

  // ── Public API ────────────────────────────────────────────

  async listTools(cursor?: string): Promise<ListResult<Tool>> {
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
  ): Promise<CallToolResult> {
    const resp = (await this._request({
      message: {
        $case: "callTool" as const,
        callTool: { name, arguments: JSON.stringify(args) },
      },
    })) as Parameters<typeof convertCallToolResult>[0];
    return convertCallToolResult(resp);
  }

  async listResources(cursor?: string): Promise<ListResult<Resource>> {
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
    const resp = (await this._request({
      message: {
        $case: "readResource" as const,
        readResource: { uri },
      },
    })) as Parameters<typeof convertReadResourceResult>[0];
    return convertReadResourceResult(resp);
  }

  async subscribeResource(uri: string): Promise<void> {
    await this._request({
      message: {
        $case: "subscribeRes" as const,
        subscribeRes: { uri },
      },
    });
  }

  async listResourceTemplates(cursor?: string): Promise<ListResult<ResourceTemplate>> {
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
    await this._request({
      message: { $case: "ping" as const, ping: {} },
    });
    return true;
  }

  async cancel(targetRequestId: bigint): Promise<void> {
    // Fire-and-forget — no response expected for cancel
    this._sendQueue.enqueue({
      requestId: 0n,
      message: {
        $case: "cancel" as const,
        cancel: { targetRequestId },
      },
    });
  }

  notifyRootsListChanged(): void {
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
