import { describe, it, expect, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { Metadata } from "nice-grpc-common";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import { CacheScope, McpDefinition, type McpClient } from "../generated/mcp_v2.js";

const META = { protocolVersion: "2026-07-28", clientCapabilities: { extensions: {} }, clientInfo: undefined };
const ICON = { src: "https://example.com/i.png", mimeType: "image/png", sizes: ["48x48"], theme: "dark" };

describe("cache hints, icons and trace context on v2", () => {
  let server: RapidMCP;
  let channel: Channel;

  async function start(opts: Partial<RapidMCPOptions> = {}): Promise<McpClient> {
    server = new RapidMCP({ name: "extras", version: "1.0", ...opts });
    server.addTool({ name: "echo", icons: [ICON], execute: async (a: any) => a.text });
    server.addTool({ name: "trace", execute: async (_a: unknown, ctx: any) => ({ ...ctx.traceContext }) });
    server.addResource({ uri: "res://a", name: "a", icons: [ICON], load: async () => ({ text: "a" }) });
    server.addResourceTemplate({
      uriTemplate: "res://items/{id}",
      name: "item",
      icons: [ICON],
      load: async () => ({ text: "i" }),
    });
    server.addPrompt({ name: "greet", icons: [ICON], load: async () => "hi" });
    const port = await server.listen();
    channel = createChannel(`127.0.0.1:${port}`);
    return createClientFactory().create(McpDefinition, channel);
  }

  afterEach(async () => {
    channel?.close();
    await server?.close();
  });

  async function everything(v2: McpClient) {
    let read: any;
    for await (const event of v2.readResource({ meta: META, uri: "res://a" })) read = (event.event as any).complete;
    return {
      discover: await v2.discover({ meta: META }),
      tools: await v2.listTools({ meta: META, cursor: "" }),
      resources: await v2.listResources({ meta: META, cursor: "" }),
      templates: await v2.listResourceTemplates({ meta: META, cursor: "" }),
      prompts: await v2.listPrompts({ meta: META, cursor: "" }),
      read,
    };
  }

  const hints = (results: Record<string, any>) =>
    Object.fromEntries(Object.entries(results).map(([name, r]) => [name, [r.cache.ttlMs, r.cache.scope]]));
  const all = (value: unknown) =>
    Object.fromEntries(["discover", "tools", "resources", "templates", "prompts", "read"].map((k) => [k, value]));

  it("defaults cache hints to immediately stale and private", async () => {
    const results = await everything(await start());

    expect(hints(results)).toEqual(all([0n, CacheScope.CACHE_SCOPE_PRIVATE]));
  });

  it("stamps configured cache hints on every cacheable result", async () => {
    const results = await everything(await start({ cacheTtlMs: 60_000, cacheScope: "public" }));

    expect(hints(results)).toEqual(all([60000n, CacheScope.CACHE_SCOPE_PUBLIC]));
  });

  it("rejects bad cache settings at construction", () => {
    expect(() => new RapidMCP({ name: "x", cacheTtlMs: -1 })).toThrow(/cacheTtlMs/);
    expect(() => new RapidMCP({ name: "x", cacheScope: "everyone" as any })).toThrow(/cacheScope/);
  });

  it("lists icons with their items", async () => {
    const results = await everything(await start({ icons: [ICON] }));

    expect(results.tools.tools.find((t) => t.name === "echo")!.icons).toEqual([ICON]);
    expect(results.tools.tools.find((t) => t.name === "trace")!.icons).toEqual([]);
    expect(results.resources.resources[0].icons).toEqual([ICON]);
    expect(results.templates.templates[0].icons).toEqual([ICON]);
    expect(results.prompts.prompts[0].icons).toEqual([ICON]);
  });

  it("sends the server's icons with discover only", async () => {
    const results = await everything(await start({ icons: [ICON] }));

    expect(results.discover.meta?.serverInfo?.icons).toEqual([ICON]);
    expect(results.tools.meta?.serverInfo?.icons).toEqual([]);
  });

  it.each(["javascript:alert(1)", "http://example.com/i.png", "file:///i.png", ""])(
    "rejects the icon source %j at registration",
    (src) => {
      const s = new RapidMCP({ name: "x" });

      expect(() => s.addTool({ name: "bad", icons: [{ src }], execute: async () => "x" })).toThrow(
        /https: or data:/,
      );
      expect(s.toolManager.listTools()).toEqual([]);
    },
  );

  it("accepts data: uri icons", () => {
    expect(() => new RapidMCP({ name: "x", icons: [{ src: "DATA:image/png;base64,AAAA" }] })).not.toThrow();
  });

  it("hands the tool the request's trace context and nothing else", async () => {
    const v2 = await start();
    const metadata = Metadata({
      traceparent: "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
      tracestate: "vendor=1",
      baggage: "user=ada",
      "x-other": "ignored",
    });

    let result: any;
    for await (const event of v2.callTool({ meta: META, name: "trace", arguments: "{}" }, { metadata })) {
      result = (event.event as any).complete;
    }

    expect(JSON.parse(result.structuredContent)).toEqual({
      traceparent: "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
      tracestate: "vendor=1",
      baggage: "user=ada",
    });
  });
});
