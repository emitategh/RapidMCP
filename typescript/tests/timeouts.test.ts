import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function outcome<T>(p: Promise<T>): Promise<[number, unknown]> {
  const start = Date.now();
  const err = await p.then(
    () => null,
    (e: unknown) => e,
  );
  return [Date.now() - start, err];
}

describe("timeouts", () => {
  let server: RapidMCP;
  let port: number;
  let client: Client;
  const aborted: string[] = [];

  beforeEach(async () => {
    aborted.length = 0;
    server = new RapidMCP({ name: "timeouts" });
    server.addTool({
      name: "slow",
      execute: async (_a: unknown, ctx: any) => {
        ctx.signal.addEventListener("abort", () => aborted.push("slow"), { once: true });
        await sleep(600);
        return "done";
      },
    });
    server.addTool({
      name: "ask",
      execute: async (_a: unknown, ctx: any) => {
        try {
          const reply = await ctx.elicit("Continue?", {}, { timeout: 200 });
          return reply.action;
        } catch (e) {
          return `failed: ${(e as McpError).code}`;
        }
      },
    });
    port = await server.listen();
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  it("times a request out with code 408", async () => {
    client = new Client(`127.0.0.1:${port}`, { requestTimeout: 200 });
    await client.connect();

    const [ms, err] = await outcome(client.callTool("slow"));

    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(408);
    expect(ms).toBeLessThan(500);
  });

  it("lets one call shorten the timeout", async () => {
    client = new Client(`127.0.0.1:${port}`);
    await client.connect();

    const [ms, err] = await outcome(client.callTool("slow", {}, { timeout: 200 }));

    expect((err as McpError).code).toBe(408);
    expect(ms).toBeLessThan(500);
  });

  it("lets one call extend the timeout", async () => {
    client = new Client(`127.0.0.1:${port}`, { requestTimeout: 200 });
    await client.connect();

    const result = await client.callTool("slow", {}, { timeout: 3000 });

    expect(result.content[0].text).toBe("done");
  });

  it("cancels a timed-out tool call on the server", async () => {
    client = new Client(`127.0.0.1:${port}`);
    await client.connect();

    await outcome(client.callTool("slow", {}, { timeout: 200 }));
    await client.ping();
    await sleep(50);

    expect(aborted).toEqual(["slow"]);
  });

  it("lets a tool choose how long to wait for an elicitation", async () => {
    client = new Client(`127.0.0.1:${port}`);
    client.setElicitationHandler(async () => {
      await sleep(1000);
      return { action: "accept", content: "{}" };
    });
    await client.connect();

    const [ms, err] = await outcome(
      client.callTool("ask").then((r) => expect(r.content[0].text).toBe("failed: 408")),
    );

    expect(err).toBeNull();
    expect(ms).toBeLessThan(800);
  });
});
