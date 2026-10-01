import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function codeOf(p: Promise<unknown>): Promise<number | string> {
  const err = await p.then(
    () => null,
    (e: unknown) => e,
  );
  return err instanceof McpError ? err.code : `not an McpError: ${String(err)}`;
}

describe("error codes follow MCP", () => {
  let server: RapidMCP;
  let client: Client;
  let port: number;

  beforeAll(async () => {
    server = new RapidMCP({ name: "codes" });
    server.addTool({
      name: "ask",
      execute: async (_a: unknown, ctx: any) => (await ctx.elicit("Continue?", {})).action,
    });
    server.addTool({
      name: "stubborn", // ignores ctx.signal
      execute: async () => {
        await sleep(600);
        return "done";
      },
    });
    port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { requestTimeout: 3000 }); // no elicitation handler
    await client.connect(); // initialize used request id 1
  });

  afterAll(async () => {
    await client.close();
    await server.close();
  });

  it("reports an unknown tool as invalid params", async () => {
    expect(await codeOf(client.callTool("nope"))).toBe(-32602);
  });

  it("reports an unknown resource as invalid params", async () => {
    expect(await codeOf(client.readResource("res://nope"))).toBe(-32602);
  });

  it("reports an unknown prompt as invalid params", async () => {
    expect(await codeOf(client.getPrompt("nope"))).toBe(-32602);
  });

  it("reports a missing client capability with its own code", async () => {
    expect(await codeOf(client.callTool("ask"))).toBe(-32021);
  });

  it("ends the local wait on cancel without waiting for the server", async () => {
    const fresh = new Client(`127.0.0.1:${port}`);
    await fresh.connect(); // request id 1
    const call = fresh.callTool("stubborn"); // request id 2
    await sleep(100);
    const start = Date.now();

    await fresh.cancel(2n);
    const code = await codeOf(call);

    expect(code).toBe(499);
    expect(Date.now() - start).toBeLessThan(300);
    await fresh.close();
  });
});
