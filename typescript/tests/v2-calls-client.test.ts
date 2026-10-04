import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError, ToolError } from "../src/errors.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("modern client: calls, reads and prompts over v2", () => {
  let server: RapidMCP;
  let client: Client;
  let aborted: string[];
  let port: number;

  beforeEach(async () => {
    aborted = [];
    server = new RapidMCP({ name: "calls", version: "1.0" });
    server.addTool({ name: "echo", execute: async (a: any) => a.text });
    server.addTool({ name: "add", execute: async (a: any) => ({ sum: a.a + a.b }) });
    server.addTool({
      name: "friendly",
      execute: async () => {
        throw new ToolError("order id must start with ORD-");
      },
    });
    server.addTool({
      name: "chatty",
      execute: async (_a: unknown, ctx: any) => {
        ctx.log.info("working");
        ctx.reportProgress(1, 2);
        return "done";
      },
    });
    server.addTool({
      name: "slow",
      execute: async (_a: unknown, ctx: any) => {
        ctx.signal.addEventListener("abort", () => aborted.push("slow"), { once: true });
        await sleep(600);
        return "done";
      },
    });
    server.addResource({ uri: "res://text", name: "text", load: async () => ({ text: "hello" }) });
    server.addResource({
      uri: "res://logo",
      name: "logo",
      mimeType: "image/png",
      load: async () => ({ blob: new Uint8Array([0x89, 0x50]) }),
    });
    server.addPrompt({
      name: "greet",
      arguments: [{ name: "who", required: true }],
      load: async (args) => `hi ${args.who}`,
    });
    port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  const codeOf = (p: Promise<unknown>) =>
    p.then(
      () => null,
      (e: unknown) => (e instanceof McpError ? e.code : `not an McpError: ${String(e)}`),
    );

  it("calls a tool", async () => {
    await client.connect();

    const result = await client.callTool("echo", { text: "hi" });

    expect([result.isError, result.content[0].text, result.structuredContent]).toEqual([false, "hi", undefined]);
  });

  it("parses structured content", async () => {
    await client.connect();

    const result = await client.callTool("add", { a: 2, b: 3 });

    expect(result.structuredContent).toEqual({ sum: 5 });
    expect(JSON.parse(result.content[0].text)).toEqual({ sum: 5 });
  });

  it("returns a ToolError as an error result", async () => {
    await client.connect();

    const result = await client.callTool("friendly");

    expect([result.isError, result.content[0].text]).toEqual([true, "order id must start with ORD-"]);
  });

  it("rejects an unknown tool with invalid params", async () => {
    await client.connect();

    expect(await codeOf(client.callTool("nope"))).toBe(-32602);
  });

  it("feeds progress and log handlers from the call's events", async () => {
    const progress: any[] = [];
    const logs: any[] = [];
    client.onNotification("progress", (payload) => void progress.push(JSON.parse(payload)));
    client.onNotification("log", (payload) => void logs.push(JSON.parse(payload)));
    await client.connect();

    const result = await client.callTool("chatty");

    expect(result.content[0].text).toBe("done");
    expect(progress.map((p) => [p.progress, p.total])).toEqual([[1, 2]]);
    expect(progress[0].token).toBeTruthy();
    expect(logs).toEqual([{ level: "info", message: "working", extra: null }]);
  });

  it("survives a notification handler that throws", async () => {
    client.onNotification("progress", () => {
      throw new Error("handler bug");
    });
    await client.connect();

    const result = await client.callTool("chatty");

    expect(result.content[0].text).toBe("done");
  });

  it("times out with 408 and aborts the tool", async () => {
    await client.connect();

    expect(await codeOf(client.callTool("slow", {}, { timeout: 200 }))).toBe(408);
    await sleep(100);

    expect(aborted).toEqual(["slow"]);
  });

  it("aborts the tool when the caller's signal aborts", async () => {
    await client.connect();
    const controller = new AbortController();

    const call = client.callTool("slow", {}, { signal: controller.signal }).catch((e) => e.message);
    await sleep(100);
    controller.abort();

    expect(await call).toBe("Aborted");
    await sleep(100);
    expect(aborted).toEqual(["slow"]);
  });

  it("reads resources", async () => {
    await client.connect();

    const text = await client.readResource("res://text");
    const logo = await client.readResource("res://logo");

    expect([text.content[0].type, text.content[0].text]).toEqual(["text", "hello"]);
    expect([logo.content[0].type, [...logo.content[0].data]]).toEqual(["image", [0x89, 0x50]]);
    expect(await codeOf(client.readResource("res://nope"))).toBe(-32602);
  });

  it("gets a prompt", async () => {
    await client.connect();

    const result = await client.getPrompt("greet", { who: "Ada" });

    expect(result.messages.map((m) => [m.role, m.content.text])).toEqual([["user", "hi Ada"]]);
    expect(await codeOf(client.getPrompt("greet"))).toBe(-32602);
  });
});
