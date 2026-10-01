import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";
import { Middleware, type CallToolResult } from "../src/middleware.js";

async function failure(p: Promise<unknown>): Promise<McpError> {
  const err = await p.then(
    () => null,
    (e: unknown) => e,
  );
  expect(err).toBeInstanceOf(McpError);
  return err as McpError;
}

describe("what the server tells a client when its own code fails", () => {
  let server: RapidMCP;
  let client: Client;

  beforeAll(async () => {
    server = new RapidMCP({ name: "detail" });
    server.addResource({
      uri: "res://secret",
      name: "secret",
      load: async () => {
        throw new Error("ENOENT: open '/srv/secrets/db-password.txt'");
      },
    });
    server.addPrompt({
      name: "broken",
      load: async () => {
        throw new Error("connect failed: postgres://admin:hunter2@db/prod");
      },
    });
    server.addTool({ name: "echo", execute: async (args: any) => JSON.stringify(args) });
    class Explode extends Middleware {
      async onToolCall(ctx: { toolName: string }, next: () => Promise<CallToolResult>) {
        if (ctx.toolName === "echo") throw new Error("middleware saw api key sk-123");
        return next();
      }
    }
    server.use(new Explode());
    const port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { requestTimeout: 3000 });
    await client.connect();
  });

  afterAll(async () => {
    await client.close();
    await server.close();
  });

  it("does not send a failing resource handler's exception text", async () => {
    const err = await failure(client.readResource("res://secret"));

    expect([err.code, err.message]).toEqual([-32603, "Resource handler for 'res://secret' failed"]);
  });

  it("does not send a failing prompt handler's exception text", async () => {
    const err = await failure(client.getPrompt("broken"));

    expect([err.code, err.message]).toEqual([-32603, "Prompt handler 'broken' failed"]);
  });

  it("does not send a failing middleware's exception text", async () => {
    const err = await failure(client.callTool("echo", { a: 1 }));

    expect([err.code, err.message]).toEqual([-32603, "Tool call 'echo' failed"]);
  });

  it("still sends the message of a deliberate McpError", async () => {
    const err = await failure(client.readResource("res://missing"));

    expect([err.code, err.message]).toEqual([-32602, "Resource 'res://missing' not found"]);
  });
});

describe("tool arguments that are not a JSON object", () => {
  let server: RapidMCP;
  let client: Client;
  const seen: unknown[] = [];

  beforeAll(async () => {
    server = new RapidMCP({ name: "args" });
    server.addTool({
      name: "record",
      execute: async (args: unknown) => {
        seen.push(args);
        return "ok";
      },
    });
    const port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { requestTimeout: 3000 });
    await client.connect();
  });

  afterAll(async () => {
    await client.close();
    await server.close();
  });

  /** Send a call_tool envelope with raw argument text, bypassing callTool's JSON.stringify. */
  function rawCall(argumentsText: string): Promise<unknown> {
    return (client as any)._request({
      message: { $case: "callTool", callTool: { name: "record", arguments: argumentsText } },
    });
  }

  it("rejects text that is not JSON as invalid params", async () => {
    const err = await failure(rawCall("{not json"));

    expect([err.code, err.message]).toEqual([
      -32602,
      "Invalid arguments for tool 'record': not valid JSON",
    ]);
  });

  it.each(["[1,2]", "null", "42", '"text"'])(
    "rejects %s as invalid params without running the tool",
    async (text) => {
      const err = await failure(rawCall(text));

      expect([err.code, err.message]).toEqual([
        -32602,
        "Invalid arguments for tool 'record': expected a JSON object",
      ]);
      expect(seen).toEqual([]);
    },
  );
});
