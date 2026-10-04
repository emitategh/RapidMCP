import { describe, it, expect, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status } from "nice-grpc-common";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import {
  ErrorData,
  McpDefinition,
  type CallToolEvent,
  type McpClient,
  type RequestMeta,
} from "../generated/mcp_v2.js";
import { operationDigest, seal } from "../src/v2/state.js";

const FORM = { elicitation: { form: true, url: false }, extensions: {} };
const FORM_AND_URL = { elicitation: { form: true, url: true }, extensions: {} };
const NONE = { elicitation: undefined, extensions: {} };

const meta = (clientCapabilities: RequestMeta["clientCapabilities"] = FORM): RequestMeta => ({
  protocolVersion: "2026-07-28",
  clientCapabilities,
  clientInfo: undefined,
});

type Answer = [action: string, content: string];

describe("ctx.elicit() on v2", () => {
  const servers: RapidMCP[] = [];
  const channels: Channel[] = [];

  async function start(opts: Partial<RapidMCPOptions> = {}) {
    const runs: string[] = [];
    const server = new RapidMCP({ name: "input", stateSecret: "s3cret-key", ...opts });
    server.addTool({
      name: "deploy",
      execute: async (args: any, ctx: any) => {
        runs.push(args.service);
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
      name: "named",
      execute: async (_a: unknown, ctx: any) => {
        const answer = await ctx.elicit("Sure?", { type: "object" }, { key: "confirmation" });
        return `${answer.action} / seen=${Object.keys(ctx.inputResponses).sort().join(",")}`;
      },
    });
    server.addTool({
      name: "pay",
      execute: async (_a: unknown, ctx: any) => {
        const answer = await ctx.elicit("Complete the payment", {}, { url: "https://pay.example/session/42" });
        return `payment ${answer.action}`;
      },
    });
    const port = await server.listen();
    const channel = createChannel(`127.0.0.1:${port}`);
    servers.push(server);
    channels.push(channel);
    return { runs, v2: createClientFactory().create(McpDefinition, channel) as McpClient };
  }

  afterEach(async () => {
    for (const channel of channels.splice(0)) channel.close();
    for (const server of servers.splice(0)) await server.close();
  });

  /** One round; returns the terminal event. */
  async function call(
    v2: McpClient,
    name: string,
    args = "{}",
    extra: { answers?: Record<string, Answer>; state?: Uint8Array; meta?: RequestMeta; options?: object } = {},
  ): Promise<NonNullable<CallToolEvent["event"]>> {
    const inputResponses = Object.fromEntries(
      Object.entries(extra.answers ?? {}).map(([key, [action, content]]) => [
        key,
        { response: { $case: "elicit" as const, elicit: { action, content } } },
      ]),
    );
    let last: CallToolEvent["event"];
    for await (const event of v2.callTool(
      { meta: extra.meta ?? meta(), name, arguments: args, inputResponses, requestState: extra.state ?? new Uint8Array() },
      extra.options ?? {},
    )) {
      last = event.event;
    }
    return last!;
  }

  async function failure(run: (onTrailer: (t: Metadata) => void) => Promise<unknown>) {
    let trailer = new Metadata();
    const err = await run((t) => (trailer = t)).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ClientError);
    return {
      code: Number(trailer.get("mcp-error-code")),
      message: (err as ClientError).details,
      status: (err as ClientError).code,
      trailer,
    };
  }

  const state = (event: NonNullable<CallToolEvent["event"]>) =>
    event.$case === "inputRequired" ? event.inputRequired.requestState : new Uint8Array();

  it("asks the question on the first round", async () => {
    const { v2 } = await start();

    const event = await call(v2, "deploy", '{"service":"api"}');

    expect(event.$case).toBe("inputRequired");
    const required = (event as any).inputRequired;
    expect(Object.keys(required.inputRequests)).toEqual(["elicit-0"]);
    const request = required.inputRequests["elicit-0"].request.elicit;
    expect(request.message).toBe("Deploy to production?");
    expect(request.mode.$case).toBe("form");
    expect(JSON.parse(request.mode.form.requestedSchema).properties).toEqual({ confirm: { type: "boolean" } });
    expect(required.requestState.length).toBeGreaterThan(32);
  });

  it("completes the call when retried with the answer", async () => {
    const { v2 } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');

    const second = await call(v2, "deploy", '{"service":"api"}', {
      answers: { "elicit-0": ["accept", '{"confirm": true}'] },
      state: state(first),
    });

    expect((second as any).complete.content[0].text).toBe("deployed api confirm=true");
  });

  it("hands a declined question to the tool as a decline", async () => {
    const { v2 } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');

    const second = await call(v2, "deploy", '{"service":"api"}', {
      answers: { "elicit-0": ["decline", ""] },
      state: state(first),
    });

    expect([(second as any).complete.isError, (second as any).complete.content[0].text]).toEqual([
      false,
      "not deployed (decline)",
    ]);
  });

  it("runs the tool again from the top each round", async () => {
    const { v2, runs } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');
    await call(v2, "deploy", '{"service":"api"}', { answers: { "elicit-0": ["accept", "{}"] }, state: state(first) });

    expect(runs).toEqual(["api", "api"]);
  });

  it("keeps the first answer until the third call", async () => {
    const { v2 } = await start();
    const one = await call(v2, "two_questions");
    const two = await call(v2, "two_questions", "{}", {
      answers: { "elicit-0": ["accept", '{"name": "Ada"}'] },
      state: state(one),
    });
    const three = await call(v2, "two_questions", "{}", {
      answers: { "elicit-1": ["accept", '{"colour": "green"}'] }, // only the latest answer
      state: state(two),
    });

    expect(Object.keys((one as any).inputRequired.inputRequests)).toEqual(["elicit-0"]);
    expect(Object.keys((two as any).inputRequired.inputRequests)).toEqual(["elicit-1"]);
    expect((three as any).complete.content[0].text).toBe("Ada likes green");
  });

  it("ignores an answer nobody asked for", async () => {
    const { v2 } = await start();

    const event = await call(v2, "deploy", '{"service":"api"}', { answers: { surprise: ["accept", "{}"] } });

    expect(event.$case).toBe("inputRequired");
    expect(Object.keys((event as any).inputRequired.inputRequests)).toEqual(["elicit-0"]);
  });

  it("supports an explicit key and exposes the answers", async () => {
    const { v2 } = await start();
    const first = await call(v2, "named");
    const second = await call(v2, "named", "{}", {
      answers: { confirmation: ["accept", '{"ok": true}'] },
      state: state(first),
    });

    expect(Object.keys((first as any).inputRequired.inputRequests)).toEqual(["confirmation"]);
    expect((second as any).complete.content[0].text).toBe("accept / seen=confirmation");
  });

  it("rejects altered state", async () => {
    const { v2 } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');
    const tampered = new Uint8Array(state(first));
    tampered[tampered.length - 3] ^= 1;

    const { code, message } = await failure((onTrailer) =>
      call(v2, "deploy", '{"service":"api"}', { state: tampered, options: { onTrailer } }),
    );

    expect(code).toBe(-32602);
    expect(message.startsWith("Invalid request_state:")).toBe(true);
  });

  it("rejects state replayed on other arguments or another tool", async () => {
    const { v2 } = await start();
    const first = await call(v2, "deploy", '{"service":"api"}');

    const otherArguments = await failure((onTrailer) =>
      call(v2, "deploy", '{"service":"db"}', { state: state(first), options: { onTrailer } }),
    );
    const otherTool = await failure((onTrailer) =>
      call(v2, "two_questions", "{}", { state: state(first), options: { onTrailer } }),
    );

    expect([otherArguments.code, otherTool.code]).toEqual([-32602, -32602]);
    expect(otherArguments.message).toContain("different request");
    expect(otherTool.message).toContain("different request");
  });

  it("rejects expired state", async () => {
    const { v2 } = await start();
    const args = '{"service":"api"}';
    const stale = seal(
      new TextEncoder().encode("s3cret-key"),
      {},
      operationDigest("tools/call", "deploy", args),
      "",
      { now: 1000 },
    );

    const { code, message } = await failure((onTrailer) =>
      call(v2, "deploy", args, { state: stale, options: { onTrailer } }),
    );

    expect([code, message.includes("expired")]).toEqual([-32602, true]);
  });

  it("rejects state presented by another caller", async () => {
    const { v2 } = await start({ auth: (token) => token === "alice" || token === "bob" });
    const alice = Metadata({ authorization: "Bearer alice" });
    const bob = Metadata({ authorization: "Bearer bob" });
    const first = await call(v2, "deploy", '{"service":"api"}', { options: { metadata: alice } });

    const { code, message } = await failure((onTrailer) =>
      call(v2, "deploy", '{"service":"api"}', { state: state(first), options: { metadata: bob, onTrailer } }),
    );
    const sameCaller = await call(v2, "deploy", '{"service":"api"}', {
      answers: { "elicit-0": ["accept", "{}"] },
      state: state(first),
      options: { metadata: alice },
    });

    expect([code, message.includes("different caller")]).toEqual([-32602, true]);
    expect(sameCaller.$case).toBe("complete");
  });

  it("lets any replica with the same secret finish the call", async () => {
    const a = await start();
    const b = await start();
    const first = await call(a.v2, "deploy", '{"service":"api"}');

    const second = await call(b.v2, "deploy", '{"service":"api"}', {
      answers: { "elicit-0": ["accept", '{"confirm": true}'] },
      state: state(first),
    });

    expect((second as any).complete.content[0].text).toBe("deployed api confirm=true");
    expect([a.runs, b.runs]).toEqual([["api"], ["api"]]);
  });

  it("reports a missing capability when the client cannot be asked", async () => {
    const { v2 } = await start();

    const { code, status, trailer } = await failure((onTrailer) =>
      call(v2, "deploy", '{"service":"api"}', { meta: meta(NONE), options: { onTrailer } }),
    );

    expect([code, status]).toEqual([-32021, Status.FAILED_PRECONDITION]);
    expect(ErrorData.decode(trailer.get("mcp-error-data-bin")!).requiredCapabilities).toEqual([
      "elicitation.form",
    ]);
  });

  it("needs the url capability for url mode", async () => {
    const { v2 } = await start();

    const denied = await failure((onTrailer) => call(v2, "pay", "{}", { options: { onTrailer } }));
    const asked = await call(v2, "pay", "{}", { meta: meta(FORM_AND_URL) });
    const done = await call(v2, "pay", "{}", {
      meta: meta(FORM_AND_URL),
      answers: { "elicit-0": ["accept", ""] },
      state: state(asked),
    });

    expect(ErrorData.decode(denied.trailer.get("mcp-error-data-bin")!).requiredCapabilities).toEqual([
      "elicitation.url",
    ]);
    const request = (asked as any).inputRequired.inputRequests["elicit-0"].request.elicit;
    expect([request.mode.$case, request.mode.url.url]).toEqual(["url", "https://pay.example/session/42"]);
    expect((done as any).complete.content[0].text).toBe("payment accept");
  });
});
