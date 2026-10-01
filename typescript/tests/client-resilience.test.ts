import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function elapsed<T>(p: Promise<T>): Promise<[number, unknown]> {
  const start = Date.now();
  const outcome = await p.then(
    () => null,
    (e: unknown) => e,
  );
  return [Date.now() - start, outcome];
}

describe("client resilience", () => {
  let server: RapidMCP;
  let port: number;
  const abortsSeenByTool: string[] = [];
  const unhandled: unknown[] = [];
  const onUnhandled = (reason: unknown) => unhandled.push(reason);

  function buildServer(): RapidMCP {
    const s = new RapidMCP({ name: "resilience", version: "0.1.0" });
    s.addTool({
      name: "wait_for_abort",
      execute: async (_args: unknown, ctx: any) => {
        await new Promise<void>((resolve) =>
          ctx.signal.addEventListener("abort", () => resolve(), { once: true }),
        );
        abortsSeenByTool.push("aborted");
        return "finished";
      },
    });
    return s;
  }

  beforeEach(async () => {
    unhandled.length = 0;
    abortsSeenByTool.length = 0;
    process.on("unhandledRejection", onUnhandled);
    server = buildServer();
    port = await server.listen();
  });

  afterEach(async () => {
    process.off("unhandledRejection", onUnhandled);
    await server.close();
  });

  it("survives a notification handler that throws", async () => {
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect();
    const received: string[] = [];
    client.onNotification("tools_list_changed", () => {
      throw new Error("handler bug");
    });
    client.onNotification("prompts_list_changed", () => {
      received.push("prompts");
    });
    await client.ping(); // the server has registered the session by now

    server.notifyToolsListChanged();
    server.notifyPromptsListChanged();
    await sleep(100);

    expect(unhandled).toEqual([]);
    expect(received).toEqual(["prompts"]);
    await client.close();
  });

  it("reports itself disconnected once the server is gone", async () => {
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect();

    await server.close();
    await sleep(200);

    expect(client.isConnected).toBe(false);
  });

  it("fails requests immediately once the server is gone", async () => {
    const client = new Client(`127.0.0.1:${port}`, { requestTimeout: 5000 });
    await client.connect();
    await server.close();
    await sleep(200);

    const [ms, err] = await elapsed(client.ping());

    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(503);
    expect(ms).toBeLessThan(1000);
  });

  it("can connect again after the server restarts", async () => {
    const client = new Client(`127.0.0.1:${port}`, { requestTimeout: 2000 });
    await client.connect();
    await server.close();
    await sleep(200);

    server = buildServer();
    await server.listen({ port });
    await client.connect();

    expect(await client.ping()).toBe(true);
    await client.close();
  });

  it("opens a single session for concurrent using() calls", async () => {
    const client = new Client(`127.0.0.1:${port}`);

    await Promise.all([client.using(), client.using()]);
    await client.ping();

    expect((server as any)._sessions.size).toBe(1);
    await client.close();
  });

  it("aborts in-flight tools when the client goes away", async () => {
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect();
    const call = client.callTool("wait_for_abort").catch(() => undefined);
    await sleep(100);

    await client.close();
    await call;
    await sleep(100);

    expect(abortsSeenByTool).toEqual(["aborted"]);
  });
});
