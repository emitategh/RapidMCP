import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("cancellation and promise hygiene", () => {
  let server: RapidMCP;
  let port: number;
  const abortsSeenByTool: string[] = [];
  const recordedCalls: string[] = [];
  const unhandled: unknown[] = [];
  const onUnhandled = (reason: unknown) => unhandled.push(reason);

  beforeAll(async () => {
    server = new RapidMCP({ name: "cancel-server", version: "0.1.0" });

    server.addTool({
      name: "wait_for_abort",
      execute: async (_args: unknown, ctx: any) => {
        await new Promise<void>((resolve) =>
          ctx.signal.addEventListener("abort", () => resolve(), { once: true }),
        );
        abortsSeenByTool.push("aborted");
        return "finished after abort";
      },
    });

    server.addTool({
      name: "record_call",
      execute: async () => {
        recordedCalls.push("called");
        return "ok";
      },
    });

    server.addTool({
      name: "sampler",
      execute: async (_args: unknown, ctx: any) => {
        const reply = await ctx.sample({
          messages: [{ role: "user", content: [{ type: "text", text: "hi" }] }],
          maxTokens: 5,
        });
        return `model=${reply.model}`;
      },
    });

    port = await server.listen();
  });

  afterAll(async () => {
    await server.close();
  });

  beforeEach(() => {
    unhandled.length = 0;
    abortsSeenByTool.length = 0;
    recordedCalls.length = 0;
    process.on("unhandledRejection", onUnhandled);
  });

  afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
  });

  it("does not leak an unhandled rejection when a call with a signal fails", async () => {
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect();
    const ac = new AbortController();

    await expect(client.callTool("does_not_exist", {}, { signal: ac.signal })).rejects.toThrow(
      "not found",
    );
    await sleep(50);

    expect(unhandled).toEqual([]);
    await client.close();
  });

  it("does not send the call at all when the signal is already aborted", async () => {
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect();
    const ac = new AbortController();
    ac.abort();

    await expect(client.callTool("record_call", {}, { signal: ac.signal })).rejects.toThrow(
      "Aborted",
    );
    await client.ping(); // anything sent before this has been processed by the server
    await client.close();
    await sleep(50);

    expect(recordedCalls).toEqual([]);
    expect(unhandled).toEqual([]);
  });

  it("aborts the tool's signal on the server when the client cancels the call", async () => {
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect();
    const ac = new AbortController();

    const call = client.callTool("wait_for_abort", {}, { signal: ac.signal });
    await sleep(100);
    ac.abort();
    await expect(call).rejects.toThrow("Aborted");
    await sleep(100);

    expect(abortsSeenByTool).toEqual(["aborted"]);
    await client.close();
  });

  it("rejects a cancelled tool call with 499 instead of returning its result", async () => {
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect(); // initialize takes client request id 1

    const call = client.callTool("wait_for_abort"); // client request id 2
    await sleep(100);
    await client.cancel(2n);

    const err = await call.then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(499);
    await client.close();
  });

  it("does not disturb an unrelated server-initiated request with the same id", async () => {
    const client = new Client(`127.0.0.1:${port}`);
    client.setSamplingHandler(async () => {
      await sleep(300);
      return { role: "assistant", content: [], model: "test-model", stopReason: "end" };
    });
    await client.connect(); // client request id 1 is the finished initialize

    // The server's sampling request for this session gets server-side id 1.
    const call = client.callTool("sampler");
    await sleep(100);
    await client.cancel(1n);

    const result = await call;
    expect(result.isError).toBe(false);
    expect(result.content[0].text).toBe("model=test-model");
    await client.close();
  });
});
