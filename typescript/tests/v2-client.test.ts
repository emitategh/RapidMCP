import { describe, it, expect, afterEach } from "vitest";
import { createServer, type Server } from "nice-grpc";
import { z } from "zod";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";
import { McpDefinition as McpV1Definition } from "../generated/mcp.js";
import { McpServicer } from "../src/servicer.js";

describe("Client mode", () => {
  let server: RapidMCP | null = null;
  let client: Client | null = null;
  let v1Only: Server | null = null;

  async function start(opts: Partial<RapidMCPOptions> = {}): Promise<number> {
    server = new RapidMCP({ name: "dual", version: "9.9", ...opts });
    server.addTool({
      name: "echo",
      description: "Echo",
      parameters: z.object({ text: z.string() }),
      annotations: { readOnly: true },
      execute: async (args) => args.text,
    });
    server.addTool({ name: "plain", execute: async () => "x" });
    server.addResource({ uri: "res://a", name: "a", load: async () => ({ text: "a" }) });
    server.addResourceTemplate({
      uriTemplate: "res://items/{id}",
      name: "item",
      load: async () => ({ text: "i" }),
    });
    server.addPrompt({
      name: "greet",
      arguments: [{ name: "who", complete: async (v) => ({ values: [`${v}lice`] }) }],
      load: async () => "hi",
    });
    return server.listen();
  }

  afterEach(async () => {
    await client?.close();
    client = null;
    await server?.close();
    server = null;
    v1Only?.forceShutdown();
    v1Only = null;
  });

  it("discovers and lists over v2 in modern mode", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const tools = await client.listTools();
    const resources = await client.listResources();
    const templates = await client.listResourceTemplates();
    const prompts = await client.listPrompts();
    const completion = await client.complete("ref/prompt", "greet", "who", "A");

    expect(client.protocol).toBe("v2");
    expect(client.isConnected).toBe(true);
    expect(client.serverInfo).toEqual({
      serverName: "dual",
      serverVersion: "9.9",
      capabilities: { tools: true, toolsListChanged: true, resources: true, prompts: true },
    });
    expect(tools.items.map((t) => t.name).sort()).toEqual(["echo", "plain"]);
    expect((tools.items[0].inputSchema.properties as any).text.type).toBe("string");
    expect(resources.items.map((r) => r.uri)).toEqual(["res://a"]);
    expect(templates.items.map((t) => t.uriTemplate)).toEqual(["res://items/{id}"]);
    expect(prompts.items.map((p) => p.name)).toEqual(["greet"]);
    expect(completion.values).toEqual(["Alice"]);
    expect(await client.ping()).toBe(true);
  });

  it("applies MCP defaults to annotation hints the server left unset", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const tools = new Map((await client.listTools()).items.map((t) => [t.name, t]));

    // No annotations at all: MCP's defaults.
    expect(tools.get("plain")!.annotations).toEqual({
      title: "",
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: false,
      openWorldHint: true,
    });
    // Annotated with readOnly only: that hint as given, the rest defaulted.
    expect(tools.get("echo")!.annotations.readOnlyHint).toBe(true);
    expect(tools.get("echo")!.annotations.destructiveHint).toBe(true);
  });

  it("follows pagination cursors in modern mode", async () => {
    const port = await start({ pageSize: 1 });
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const first = await client.listTools();
    const second = await client.listTools(first.nextCursor ?? undefined);

    expect(first.items.map((t) => t.name)).toEqual(["echo"]);
    expect(second.items.map((t) => t.name)).toEqual(["plain"]);
    expect(second.nextCursor).toBeNull();
  });

  it("says which operations v2 does not carry yet", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    const err = await client.callTool("echo", { text: "x" }).then(
      () => null,
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(-32601);
    expect((err as McpError).message).toContain("legacy");
  });

  it("sends its token in modern mode", async () => {
    const port = await start({ auth: (token) => token === "s3cret" });
    client = new Client(`127.0.0.1:${port}`, { mode: "modern", token: "s3cret" });
    await client.connect();
    const denied = new Client(`127.0.0.1:${port}`, { mode: "modern", token: "nope" });

    expect((await client.listPrompts()).items.map((p) => p.name)).toEqual(["greet"]);
    await expect(denied.connect()).rejects.toThrow(/UNAUTHENTICATED/);
  });

  it("picks v2 in auto mode when the server offers it", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`, { mode: "auto" });
    await client.connect();

    expect(client.protocol).toBe("v2");
  });

  it("falls back to v1 in auto mode against a server without v2", async () => {
    await start(); // only to build a populated RapidMCP; its own listener is unused
    v1Only = createServer();
    v1Only.add(
      McpV1Definition,
      new McpServicer({
        name: "old",
        version: "0.1",
        toolManager: server!.toolManager,
        resourceManager: server!.resourceManager,
        promptManager: server!.promptManager,
        middlewares: [],
      }) as any,
    );
    const port = await v1Only.listen("127.0.0.1:0");
    client = new Client(`127.0.0.1:${port}`, { mode: "auto" });
    await client.connect();

    const result = await client.callTool("echo", { text: "old server" });

    expect(client.protocol).toBe("v1");
    expect(result.content[0].text).toBe("old server");
  });

  it("fails promptly in auto mode when nothing is listening", async () => {
    client = new Client("127.0.0.1:1", { mode: "auto", requestTimeout: 2000 });
    const start = Date.now();

    const err = await client.connect().then(
      () => null,
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(McpError);
    expect([408, 503]).toContain((err as McpError).code);
    expect(Date.now() - start).toBeLessThan(8000);
    expect(client.isConnected).toBe(false);
  });

  it("keeps legacy as the default", async () => {
    const port = await start();
    client = new Client(`127.0.0.1:${port}`);
    await client.connect();

    expect(client.protocol).toBe("v1");
  });
});
