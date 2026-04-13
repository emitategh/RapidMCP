import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { TestServer } from "./test-server.js";
import { Client } from "../src/client.js";

const TOOLS = [
  {
    name: "add",
    description: "Add two numbers",
    inputSchema: JSON.stringify({ type: "object", properties: { a: { type: "number" }, b: { type: "number" } } }),
    outputSchema: "",
    annotations: {
      title: "Add",
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
  },
];

const RESOURCES = [
  { uri: "file:///hello.txt", name: "hello", description: "A greeting", mimeType: "text/plain" },
];

const PROMPTS = [
  {
    name: "summarize",
    description: "Summarize text",
    arguments: [{ name: "text", description: "The text to summarize", required: true }],
  },
];

describe("Client integration", () => {
  let server: TestServer;
  let client: Client;

  beforeAll(async () => {
    server = new TestServer({ tools: TOOLS, resources: RESOURCES, prompts: PROMPTS });
    await server.start();
    client = new Client(server.address);
    await client.connect();
  });

  afterAll(async () => {
    await client.close();
    await server.stop();
  });

  it("connects and populates serverInfo", () => {
    const info = client.serverInfo;
    expect(info).not.toBeNull();
    expect(info!.serverName).toBe("test-server");
    expect(info!.serverVersion).toBe("0.1.0");
    expect(info!.capabilities.tools).toBe(true);
  });

  it("listTools returns tools", async () => {
    const result = await client.listTools();
    expect(result.items).toHaveLength(1);
    expect(result.items[0].name).toBe("add");
    expect(result.items[0].description).toBe("Add two numbers");
    expect(result.items[0].annotations.title).toBe("Add");
    expect(result.nextCursor).toBeNull();
  });

  it("callTool returns content", async () => {
    const result = await client.callTool("add", { a: 1, b: 2 });
    expect(result.isError).toBe(false);
    expect(result.content).toHaveLength(1);
    expect(result.content[0].type).toBe("text");
    expect(result.content[0].text).toBe("called add");
  });

  it("listResources returns resources", async () => {
    const result = await client.listResources();
    expect(result.items).toHaveLength(1);
    expect(result.items[0].uri).toBe("file:///hello.txt");
    expect(result.items[0].name).toBe("hello");
  });

  it("readResource returns content", async () => {
    const result = await client.readResource("file:///hello.txt");
    expect(result.content).toHaveLength(1);
    expect(result.content[0].text).toBe("content of file:///hello.txt");
  });

  it("listPrompts returns prompts with arguments", async () => {
    const result = await client.listPrompts();
    expect(result.items).toHaveLength(1);
    expect(result.items[0].name).toBe("summarize");
    expect(result.items[0].arguments).toHaveLength(1);
    expect(result.items[0].arguments[0].name).toBe("text");
    expect(result.items[0].arguments[0].required).toBe(true);
  });

  it("ping returns true", async () => {
    const result = await client.ping();
    expect(result).toBe(true);
  });

  it("ref-counted using()", async () => {
    const c1 = new Client(server.address);
    await c1.using();
    expect(c1.serverInfo).not.toBeNull();

    // Second using() should not fail
    await c1.using();
    expect(c1.serverInfo).not.toBeNull();

    // First release — should NOT close (refCount still 1)
    await c1.release();
    expect(c1.serverInfo).not.toBeNull();

    // Second release — should close (refCount 0)
    await c1.release();
    expect(c1.serverInfo).toBeNull();
  });
});
