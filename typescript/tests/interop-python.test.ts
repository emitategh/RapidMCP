/** The TypeScript client against the Python server, over the v2 protocol. */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";

const PYTHON_DIR = fileURLToPath(new URL("../../python/", import.meta.url));
const INTERPRETER = [".venv/Scripts/python.exe", ".venv/bin/python"]
  .map((relative) => PYTHON_DIR + relative)
  .find((path) => existsSync(path));

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe.skipIf(!INTERPRETER)("TypeScript client against the Python server", () => {
  let server: ChildProcess;
  let target: string;

  beforeAll(async () => {
    server = spawn(INTERPRETER!, ["tests/servers/interop.py"], { cwd: PYTHON_DIR });
    target = await new Promise<string>((resolve, reject) => {
      let output = "";
      const timer = setTimeout(() => reject(new Error(`Python server did not start:\n${output}`)), 60_000);
      const onData = (chunk: Buffer) => {
        output += chunk.toString();
        const match = /PORT (\d+)/.exec(output);
        if (match) {
          clearTimeout(timer);
          resolve(`127.0.0.1:${match[1]}`);
        }
      };
      server.stdout!.on("data", onData);
      server.stderr!.on("data", onData);
      server.on("exit", (code) => reject(new Error(`Python server exited with ${code}:\n${output}`)));
    });
  }, 70_000);

  afterAll(() => {
    server?.kill();
  });

  async function connected(opts: ConstructorParameters<typeof Client>[1] = {}) {
    const client = new Client(target, { mode: "modern", ...opts });
    return client;
  }

  it("discovers and lists", async () => {
    const client = await connected();
    await client.connect();

    const tools = await client.listTools();
    const resources = await client.listResources();
    const templates = await client.listResourceTemplates();
    const prompts = await client.listPrompts();
    await client.close();

    expect(client.protocol).toBeNull(); // closed
    expect(tools.items.map((t) => t.name).sort()).toEqual(["add", "ask", "chatty", "echo", "poke"]);
    expect(resources.items.map((r) => r.uri)).toEqual(["res://greeting"]);
    expect(templates.items.map((t) => t.uriTemplate)).toEqual(["res://items/{id}"]);
    expect(prompts.items.map((p) => [p.name, p.arguments.map((a) => a.name)])).toEqual([["greet", ["who"]]]);
  });

  it("identifies the server over v2", async () => {
    const client = await connected();
    await client.connect();

    expect([client.protocol, client.serverInfo?.serverName]).toEqual(["v2", "interop-python"]);
    await client.close();
  });

  it("calls tools, with structured results, progress and logs", async () => {
    const client = await connected();
    const progress: any[] = [];
    const logs: any[] = [];
    client.onNotification("progress", (payload) => void progress.push(JSON.parse(payload)));
    client.onNotification("log", (payload) => void logs.push(JSON.parse(payload)));
    await client.connect();

    const echo = await client.callTool("echo", { text: "hola" });
    const added = await client.callTool("add", { a: 2, b: 3 });
    const chatty = await client.callTool("chatty");
    await client.close();

    expect(echo.content[0].text).toBe("hola");
    expect(added.structuredContent).toEqual({ sum: 5 });
    expect(chatty.content[0].text).toBe("done");
    expect(progress.map((p) => [p.progress, p.total])).toEqual([[1, 2]]);
    expect(logs.map((l) => [l.level, l.message])).toEqual([["info", "working"]]);
  });

  it("completes an input round", async () => {
    const client = await connected();
    const asked: string[] = [];
    client.setElicitationHandler(async (request) => {
      asked.push(request.message);
      return { action: "accept", content: '{"confirm": true}' };
    });
    await client.connect();

    const result = await client.callTool("ask");
    await client.close();

    expect([result.content[0].text, asked]).toEqual(["confirmed", ["Confirm?"]]);
  });

  it("reads resources and gets prompts", async () => {
    const client = await connected();
    await client.connect();

    const greeting = await client.readResource("res://greeting");
    const item = await client.readResource("res://items/7");
    const prompt = await client.getPrompt("greet", { who: "Ada" });
    await client.close();

    expect(greeting.content[0].text).toBe("hello");
    expect(item.content[0].text).toBe("item 7");
    expect(prompt.messages.map((m) => [m.role, m.content.text])).toEqual([["user", "hi Ada"]]);
  });

  it("keeps error codes", async () => {
    const client = await connected();
    await client.connect();
    const code = (p: Promise<unknown>) =>
      p.then(
        () => null,
        (e: unknown) => (e instanceof McpError ? e.code : String(e)),
      );

    const codes = [await code(client.callTool("nope")), await code(client.getPrompt("greet"))];
    await client.close();

    expect(codes).toEqual([-32602, -32602]);
  });

  it("receives a notification", async () => {
    const client = await connected();
    const seen: string[] = [];
    client.onNotification("tools_list_changed", () => void seen.push("tools"));
    await client.connect();

    await client.callTool("poke");
    for (let i = 0; i < 100 && seen.length === 0; i++) await sleep(20);
    await client.close();

    expect(seen).toEqual(["tools"]);
  });
});
