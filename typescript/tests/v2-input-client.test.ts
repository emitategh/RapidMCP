import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpError } from "../src/errors.js";

describe("a v2 client answers input rounds with its elicitation handler", () => {
  let server: RapidMCP;
  let port: number;
  let client: Client;

  beforeEach(async () => {
    server = new RapidMCP({ name: "input", stateSecret: "k" });
    server.addTool({
      name: "deploy",
      execute: async (args: any, ctx: any) => {
        const answer = await ctx.elicit("Deploy to production?", {
          type: "object",
          properties: { confirm: { type: "boolean" } },
        });
        if (answer.action !== "accept") return `not deployed (${answer.action})`;
        return `deployed ${args.service} confirm=${JSON.parse(answer.content || "{}").confirm}`;
      },
    });
    server.addTool({
      name: "two_questions",
      execute: async (_a: unknown, ctx: any) => {
        const first = await ctx.elicit("Name?", { type: "object" });
        const second = await ctx.elicit("Colour?", { type: "object" });
        return `${JSON.parse(first.content).name} likes ${JSON.parse(second.content).colour}`;
      },
    });
    server.addTool({
      name: "pay",
      execute: async (_a: unknown, ctx: any) => {
        const answer = await ctx.elicit("Complete the payment", {}, { url: "https://pay.example/session/42" });
        return `payment ${answer.action}`;
      },
    });
    server.addTool({
      name: "never_satisfied",
      execute: async (_a: unknown, ctx: any) => {
        for (let attempt = 0; attempt < 100; attempt++) {
          await ctx.elicit("Again?", {}, { key: `attempt-${attempt}` });
        }
        return "unreachable";
      },
    });
    port = await server.listen();
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  const failure = (p: Promise<unknown>) =>
    p.then(
      () => null,
      (e: unknown) => e as McpError,
    );

  it.each(["legacy", "modern"] as const)("runs one tool on the %s protocol", async (mode) => {
    client = new Client(`127.0.0.1:${port}`, { mode });
    const seen: string[] = [];
    client.setElicitationHandler(async (request) => {
      seen.push(request.message);
      return { action: "accept", content: '{"confirm": true}' };
    });
    await client.connect();

    const result = await client.callTool("deploy", { service: "api" });

    expect(result.content[0].text).toBe("deployed api confirm=true");
    expect(seen).toEqual(["Deploy to production?"]);
  });

  it("gives the handler the form schema on v2", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const requests: any[] = [];
    client.setElicitationHandler(async (request) => {
      requests.push(request);
      return { action: "accept", content: "{}" };
    });
    await client.connect();

    await client.callTool("deploy", { service: "api" });

    expect([requests[0].mode, requests[0].url]).toEqual(["form", ""]);
    expect(JSON.parse(requests[0].schema).properties).toEqual({ confirm: { type: "boolean" } });
  });

  it("asks two questions in order", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const asked: string[] = [];
    client.setElicitationHandler(async (request) => {
      asked.push(request.message);
      return {
        action: "accept",
        content: request.message === "Name?" ? '{"name": "Ada"}' : '{"colour": "green"}',
      };
    });
    await client.connect();

    const result = await client.callTool("two_questions");

    expect(asked).toEqual(["Name?", "Colour?"]);
    expect(result.content[0].text).toBe("Ada likes green");
  });

  it("passes a decline to the tool", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    client.setElicitationHandler(async () => ({ action: "decline", content: "" }));
    await client.connect();

    const result = await client.callTool("deploy", { service: "api" });

    expect(result.content[0].text).toBe("not deployed (decline)");
  });

  it("delivers url mode to a handler that declared it", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const requests: any[] = [];
    client.setElicitationHandler(
      async (request) => {
        requests.push(request);
        return { action: "accept", content: "" };
      },
      { url: true },
    );
    await client.connect();

    const result = await client.callTool("pay");

    expect([requests[0].mode, requests[0].url]).toEqual(["url", "https://pay.example/session/42"]);
    expect(result.content[0].text).toBe("payment accept");
  });

  it("reports a missing capability for url mode it did not declare", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    client.setElicitationHandler(async () => ({ action: "accept", content: "" }));
    await client.connect();

    const err = await failure(client.callTool("pay"));

    expect(err?.code).toBe(-32021);
    expect(err?.data).toEqual({ requiredCapabilities: ["elicitation.url"] });
  });

  it("reports a missing capability when it has no handler", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();

    expect((await failure(client.callTool("deploy", { service: "api" })))?.code).toBe(-32021);
  });

  it("gives up on a server that never stops asking", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    let rounds = 0;
    client.setElicitationHandler(async () => {
      rounds += 1;
      return { action: "accept", content: "{}" };
    });
    await client.connect();

    const err = await failure(client.callTool("never_satisfied"));

    expect(err?.code).toBe(508);
    expect(rounds).toBe(10);
  });
});
