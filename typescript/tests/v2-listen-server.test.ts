import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status } from "nice-grpc-common";
import { RapidMCP } from "../src/server.js";
import { McpDefinition, type ListenEvent, type McpClient, type NotificationFilter } from "../generated/mcp_v2.js";

const META = { protocolVersion: "2026-07-28", clientCapabilities: { extensions: {} }, clientInfo: undefined };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("v2 listen", () => {
  let server: RapidMCP;
  let channel: Channel;
  let v2: McpClient;
  let open: AbortController[];

  beforeEach(async () => {
    open = [];
    server = new RapidMCP({ name: "listen" });
    const port = await server.listen();
    channel = createChannel(`127.0.0.1:${port}`);
    v2 = createClientFactory().create(McpDefinition, channel);
  });

  afterEach(async () => {
    for (const controller of open) controller.abort();
    channel.close();
    await server.close();
  });

  /** Open a stream; returns a reader for its events one at a time. */
  function listen(wanted: Partial<NotificationFilter>) {
    const controller = new AbortController();
    open.push(controller);
    const iterator = v2
      .listen(
        {
          meta: META,
          notifications: {
            toolsListChanged: false,
            promptsListChanged: false,
            resourcesListChanged: false,
            resourceSubscriptions: [],
            ...wanted,
          },
        },
        { signal: controller.signal },
      )
      [Symbol.asyncIterator]();
    return {
      controller,
      next: async (): Promise<NonNullable<ListenEvent["event"]>> => {
        const result = await iterator.next();
        if (result.done) throw new Error("stream ended");
        return result.value.event!;
      },
    };
  }

  async function acknowledged(stream: ReturnType<typeof listen>): Promise<NotificationFilter> {
    const event = await stream.next();
    expect(event.$case).toBe("acknowledged");
    return (event as any).acknowledged;
  }

  const listeners = () => (server as any)._listeners.size as number;

  it("acknowledges the filter first", async () => {
    const stream = listen({ toolsListChanged: true, resourceSubscriptions: ["res://a"] });

    const accepted = await acknowledged(stream);

    expect(accepted.toolsListChanged).toBe(true);
    expect(accepted.promptsListChanged).toBe(false);
    expect(accepted.resourceSubscriptions).toEqual(["res://a"]);
  });

  it("sends a listener only the kinds it asked for", async () => {
    const stream = listen({ promptsListChanged: true });
    await acknowledged(stream);

    server.notifyToolsListChanged(); // not asked for
    server.notifyResourcesListChanged(); // not asked for
    server.notifyPromptsListChanged();

    expect((await stream.next()).$case).toBe("promptsListChanged");
  });

  it("sends resource updates only for subscribed uris", async () => {
    const stream = listen({ resourceSubscriptions: ["res://mine"] });
    await acknowledged(stream);

    server.notifyResourceUpdated("res://other");
    server.notifyResourceUpdated("res://mine");

    const event = await stream.next();
    expect([event.$case, (event as any).resourceUpdated.uri]).toEqual(["resourceUpdated", "res://mine"]);
  });

  it("serves each listener independently", async () => {
    const tools = listen({ toolsListChanged: true });
    const prompts = listen({ promptsListChanged: true });
    await acknowledged(tools);
    await acknowledged(prompts);

    server.notifyPromptsListChanged();
    server.notifyToolsListChanged();

    expect((await tools.next()).$case).toBe("toolsListChanged");
    expect((await prompts.next()).$case).toBe("promptsListChanged");
  });

  it("runs subscribe handlers for each uri, and survives one that throws", async () => {
    const seen: string[] = [];
    server.onResourceSubscribe(() => {
      throw new Error("handler bug");
    });
    server.onResourceSubscribe(async (uri) => void seen.push(uri));
    const stream = listen({ resourceSubscriptions: ["res://a", "res://b"] });
    await acknowledged(stream);
    await sleep(50);

    server.notifyResourceUpdated("res://b");

    expect(((await stream.next()) as any).resourceUpdated.uri).toBe("res://b");
    expect(seen).toEqual(["res://a", "res://b"]);
  });

  it("forgets a listener whose stream was cancelled", async () => {
    const stream = listen({ toolsListChanged: true });
    await acknowledged(stream);
    expect(listeners()).toBe(1);

    stream.controller.abort();
    for (let i = 0; i < 40 && listeners() > 0; i++) await sleep(50);

    expect(listeners()).toBe(0);
    server.notifyToolsListChanged(); // publishing to nobody is fine
  });

  it("rejects a listen request without meta", async () => {
    let trailer = new Metadata();
    const iterator = v2.listen({}, { onTrailer: (t) => (trailer = t) })[Symbol.asyncIterator]();

    const err = await iterator.next().then(
      () => null,
      (e: unknown) => e,
    );

    expect((err as ClientError).code).toBe(Status.INVALID_ARGUMENT);
    expect(trailer.get("mcp-error-code")).toBe("-32602");
  });
});
