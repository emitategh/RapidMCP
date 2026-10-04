/** Regressions found by the whole-branch review of the v2 protocol. */
import { describe, it, expect, afterEach, vi } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { ErrorCode, McpError } from "../src/errors.js";
import { McpV2Servicer } from "../src/v2/servicer.js";
import { toServerError } from "../src/v2/errors.js";
import { McpDefinition, type McpClient } from "../generated/mcp_v2.js";

const META = {
  protocolVersion: "2026-07-28",
  clientCapabilities: { elicitation: { form: true, url: false }, extensions: {} },
  clientInfo: undefined,
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("v2 review fixes", () => {
  let server: RapidMCP | undefined;
  let client: Client | undefined;
  let channel: Channel | undefined;

  afterEach(async () => {
    vi.restoreAllMocks();
    await client?.close();
    channel?.close();
    await server?.close();
    server = client = channel = undefined;
  });

  async function raw(port: number): Promise<McpClient> {
    channel = createChannel(`127.0.0.1:${port}`);
    return createClientFactory().create(McpDefinition, channel);
  }

  async function terminal(v2: McpClient, request: Parameters<McpClient["callTool"]>[0]) {
    let last: any;
    for await (const event of v2.callTool(request)) last = event.event;
    return last;
  }

  /** Record the order in which the server gains and loses listeners. */
  function recordListeners(s: RapidMCP): string[] {
    const events: string[] = [];
    const listeners = (s as any)._listeners;
    const add = listeners.add.bind(listeners);
    const remove = listeners.remove.bind(listeners);
    listeners.add = (...args: unknown[]) => {
      events.push("add");
      return add(...args);
    };
    listeners.remove = (...args: unknown[]) => {
      events.push("remove");
      return remove(...args);
    };
    return events;
  }

  it("ignores an answer sent before the question was asked", async () => {
    server = new RapidMCP({ name: "x", stateSecret: "s3cret-key" });
    server.addTool({
      name: "deploy",
      execute: async (_a: unknown, ctx: any) => {
        const answer = await ctx.elicit("Deploy?", { type: "object", properties: {} });
        return `deployed (${answer.action})`;
      },
    });
    const v2 = await raw(await server.listen());
    const first = await terminal(v2, { meta: META, name: "deploy", arguments: "{}" });
    const key = Object.keys(first.inputRequired.inputRequests)[0];
    const inputResponses = {
      [key]: { response: { $case: "elicit" as const, elicit: { action: "accept", content: "{}" } } },
    };

    const unasked = await terminal(v2, { meta: META, name: "deploy", arguments: "{}", inputResponses });
    const asked = await terminal(v2, {
      meta: META,
      name: "deploy",
      arguments: "{}",
      inputResponses,
      requestState: first.inputRequired.requestState,
    });

    expect(unasked.$case).toBe("inputRequired");
    expect(asked.complete.content[0].text).toBe("deployed (accept)");
  });

  it("rejects prompt arguments the prompt does not declare", async () => {
    server = new RapidMCP({ name: "x" });
    server.addPrompt({
      name: "greet",
      arguments: [{ name: "who", required: true }],
      load: async (args) => `hi ${args.who}`,
    });
    client = new Client(`127.0.0.1:${await server.listen()}`, { mode: "modern" });
    await client.connect();

    const err = await client.getPrompt("greet", { who: "a", extra: "b" }).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(ErrorCode.InvalidParams);
    expect((err as McpError).message).toBe("Unknown argument(s) for prompt 'greet': extra");
  });

  it("treats only plain objects as structured results", async () => {
    server = new RapidMCP({ name: "x" });
    server.addTool({ name: "when", execute: async () => new Date(0) });
    server.addTool({ name: "plain", execute: async () => ({ n: 1 }) });
    client = new Client(`127.0.0.1:${await server.listen()}`, { mode: "modern" });
    await client.connect();

    expect((await client.callTool("when")).structuredContent).toBeUndefined();
    expect((await client.callTool("plain")).structuredContent).toEqual({ n: 1 });
  });

  it("reports a rejected subscription to the caller", async () => {
    vi.spyOn(McpV2Servicer.prototype, "listen").mockImplementation(async function* (_request, context) {
      throw toServerError(
        new McpError(ErrorCode.UnsupportedProtocolVersion, "no listening here"),
        context.trailer,
      );
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    server = new RapidMCP({ name: "x" });
    client = new Client(`127.0.0.1:${await server.listen()}`, { mode: "modern" });
    await client.connect();

    const err = await client.subscribeResource("res://a").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(ErrorCode.UnsupportedProtocolVersion);
  });

  it("does not reopen the stream for handlers that leave the filter unchanged", async () => {
    server = new RapidMCP({ name: "x" });
    const events = recordListeners(server);
    client = new Client(`127.0.0.1:${await server.listen()}`, { mode: "modern" });
    client.onNotification("tools_list_changed", () => {});
    await client.connect();

    client.onNotification("progress", () => {});
    client.onNotification("log", () => {});
    client.onNotification("tools_list_changed", () => {});
    await client.subscribeResource("res://a");
    await client.subscribeResource("res://a"); // already subscribed
    await sleep(200);

    expect(events).toEqual(["add", "add", "remove"]);
    expect((server as any)._listeners.size).toBe(1);
  });

  it("copies icons at registration, so later mutation cannot bypass validation", async () => {
    const icons = [{ src: "https://example.com/i.png" }];
    server = new RapidMCP({ name: "x", icons });
    icons[0].src = "javascript:alert(1)";
    icons.push({ src: "http://plain.example/i.png" });
    client = new Client(`127.0.0.1:${await server.listen()}`, { mode: "modern" });
    await client.connect();

    expect(client.serverInfo?.icons?.map((i) => i.src)).toEqual(["https://example.com/i.png"]);
  });

  it("rejects an empty state secret", () => {
    expect(() => new RapidMCP({ name: "x", stateSecret: "" })).toThrow(/stateSecret/);
    expect(() => new RapidMCP({ name: "x", stateSecret: new Uint8Array() })).toThrow(/stateSecret/);
  });

  it("is not left marked connected when connecting fails", async () => {
    vi.spyOn(McpV2Servicer.prototype, "listen").mockImplementation(async function* (_request, context) {
      throw toServerError(new McpError(ErrorCode.InternalError, "no"), context.trailer);
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    server = new RapidMCP({ name: "x" });
    client = new Client(`127.0.0.1:${await server.listen()}`, { mode: "modern" });
    client.onNotification("tools_list_changed", () => {});

    await expect(client.connect()).rejects.toBeInstanceOf(McpError);

    expect(client.isConnected).toBe(false);
    expect(client.protocol).toBeNull();
  });
});
