import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";

const ICON = { src: "https://example.com/i.png", mimeType: "image/png", sizes: ["48x48"], theme: "dark" };

describe("a modern client sees cache hints and icons, and propagates trace context", () => {
  let server: RapidMCP;
  let port: number;
  let client: Client;

  beforeEach(async () => {
    server = new RapidMCP({ name: "extras", cacheTtlMs: 30_000, cacheScope: "public", icons: [ICON] });
    server.addTool({ name: "trace", icons: [ICON], execute: async (_a: unknown, ctx: any) => ({ ...ctx.traceContext }) });
    server.addResource({ uri: "res://a", name: "a", icons: [ICON], load: async () => ({ text: "a" }) });
    server.addResourceTemplate({ uriTemplate: "res://items/{id}", name: "item", icons: [ICON], load: async () => ({ text: "i" }) });
    server.addPrompt({ name: "greet", icons: [ICON], load: async () => "hi" });
    port = await server.listen();
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  it("sees cache hints on v2", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const results = [
      await client.listTools(),
      await client.listResources(),
      await client.listResourceTemplates(),
      await client.listPrompts(),
      await client.readResource("res://a"),
    ];

    expect(results.map((r) => [r.ttlMs, r.cacheScope])).toEqual(Array(5).fill([30_000, "public"]));
  });

  it("sees no cache hints or icons on v1", async () => {
    client = new Client(`127.0.0.1:${port}`);
    await client.connect();

    const tools = await client.listTools();
    const read = await client.readResource("res://a");

    expect([tools.ttlMs, tools.cacheScope, read.ttlMs, read.cacheScope]).toEqual([
      undefined,
      undefined,
      undefined,
      undefined,
    ]);
    expect(tools.items[0].icons).toBeUndefined();
    expect(client.serverInfo?.icons).toBeUndefined();
  });

  it("sees icons on v2", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    expect((await client.listTools()).items[0].icons).toEqual([ICON]);
    expect((await client.listResources()).items[0].icons).toEqual([ICON]);
    expect((await client.listResourceTemplates()).items[0].icons).toEqual([ICON]);
    expect((await client.listPrompts()).items[0].icons).toEqual([ICON]);
    expect(client.serverInfo?.icons).toEqual([ICON]);
  });

  it("calls the trace context provider per request and sends only the trace keys", async () => {
    const spans = ["00-aaaa-01", "00-bbbb-01"];
    client = new Client(`127.0.0.1:${port}`, {
      mode: "modern",
      traceContext: () => ({ traceparent: spans.shift()!, baggage: "user=ada", authorization: "nope" }),
    });
    await client.connect(); // discover does not consume a span: the provider is only for calls

    const first = await client.callTool("trace");
    const second = await client.callTool("trace");

    expect(first.structuredContent).toEqual({ traceparent: "00-aaaa-01", baggage: "user=ada" });
    expect(second.structuredContent).toEqual({ traceparent: "00-bbbb-01", baggage: "user=ada" });
  });
});
