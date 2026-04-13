import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { z } from "zod";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { ToolError } from "../src/errors.js";

describe("Server ↔ Client integration", () => {
  let server: RapidMCP;
  let client: Client;
  let port: number;

  beforeAll(async () => {
    server = new RapidMCP({ name: "test-server", version: "0.1.0" });

    server.addTool({
      name: "echo",
      description: "Echo text",
      parameters: z.object({ text: z.string() }),
      execute: async (args) => args.text,
    });

    server.addTool({
      name: "add",
      description: "Add two numbers",
      parameters: z.object({ a: z.number(), b: z.number() }),
      annotations: { readOnly: true },
      execute: async (args) => String(args.a + args.b),
    });

    server.addTool({
      name: "no_params",
      description: "Tool without parameters",
      execute: async () => "pong",
    });

    server.addTool({
      name: "fail",
      description: "Always fails",
      execute: async () => {
        throw new ToolError("Something went wrong");
      },
    });

    server.addTool({
      name: "log_demo",
      description: "Demo logging",
      execute: async (_args: unknown, ctx: any) => {
        ctx.log.info("Hello from tool");
        return "logged";
      },
    });

    server.addResource({
      uri: "res://info",
      name: "Server Info",
      mimeType: "application/json",
      load: async () => ({ text: JSON.stringify({ status: "ok" }) }),
    });

    server.addResourceTemplate({
      uriTemplate: "res://items/{id}",
      name: "Item",
      mimeType: "application/json",
      arguments: [{ name: "id", required: true }],
      load: async (args) => ({ text: JSON.stringify({ id: args.id }) }),
    });

    server.addPrompt({
      name: "greet",
      description: "Greeting prompt",
      arguments: [
        { name: "name", required: true },
        {
          name: "style",
          required: false,
          complete: async (value: string) => ({
            values: ["formal", "casual"].filter((s) => s.startsWith(value)),
          }),
        },
      ],
      load: async (args) => `Hello, ${args.name}!`,
    });

    port = await server.listen({ port: 0 });
    client = new Client(`127.0.0.1:${port}`);
    await client.connect();
  }, 10_000);

  afterAll(async () => {
    await client.close();
    await server.close();
  });

  it("connects and gets server info", () => {
    expect(client.serverInfo).not.toBeNull();
    expect(client.serverInfo!.serverName).toBe("test-server");
    expect(client.serverInfo!.serverVersion).toBe("0.1.0");
  });

  it("lists tools", async () => {
    const result = await client.listTools();
    expect(result.items.length).toBeGreaterThanOrEqual(4);
    expect(result.items.some((t) => t.name === "echo")).toBe(true);
    expect(result.items.some((t) => t.name === "add")).toBe(true);
  });

  it("calls echo tool", async () => {
    const result = await client.callTool("echo", { text: "hello" });
    expect(result.content[0].text).toBe("hello");
    expect(result.isError).toBe(false);
  });

  it("calls add tool", async () => {
    const result = await client.callTool("add", { a: 2, b: 3 });
    expect(result.content[0].text).toBe("5");
  });

  it("calls tool without parameters", async () => {
    const result = await client.callTool("no_params", {});
    expect(result.content[0].text).toBe("pong");
  });

  it("tool error returns isError=true", async () => {
    const result = await client.callTool("fail", {});
    expect(result.isError).toBe(true);
    expect(result.content[0].text).toContain("Something went wrong");
  });

  it("lists resources", async () => {
    const result = await client.listResources();
    expect(result.items.some((r) => r.uri === "res://info")).toBe(true);
  });

  it("reads a resource", async () => {
    const result = await client.readResource("res://info");
    expect(result.content[0].text).toContain("ok");
  });

  it("reads a resource template", async () => {
    const result = await client.readResource("res://items/42");
    const data = JSON.parse(result.content[0].text);
    expect(data.id).toBe("42");
  });

  it("lists resource templates", async () => {
    const result = await client.listResourceTemplates();
    expect(result.items.some((t) => t.uriTemplate === "res://items/{id}")).toBe(
      true,
    );
  });

  it("lists prompts", async () => {
    const result = await client.listPrompts();
    expect(result.items.some((p) => p.name === "greet")).toBe(true);
  });

  it("gets a prompt", async () => {
    const result = await client.getPrompt("greet", { name: "World" });
    expect(result.messages[0].content.text).toBe("Hello, World!");
  });

  it("completes prompt argument", async () => {
    const result = await client.complete("ref/prompt", "greet", "style", "for");
    expect(result.values).toEqual(["formal"]);
  });

  it("pings the server", async () => {
    expect(await client.ping()).toBe(true);
  });
});
