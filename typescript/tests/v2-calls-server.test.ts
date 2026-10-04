import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status } from "nice-grpc-common";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import { ToolError } from "../src/errors.js";
import { Middleware, type CallToolResult, type ToolCallContext } from "../src/middleware.js";
import { McpDefinition, type McpClient, type RequestMeta } from "../generated/mcp_v2.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function meta(extra: Partial<RequestMeta> = {}): RequestMeta {
  return {
    protocolVersion: "2026-07-28",
    clientCapabilities: { extensions: {} },
    clientInfo: undefined,
    ...extra,
  };
}

class Block extends Middleware {
  async onToolCall(ctx: ToolCallContext, next: () => Promise<CallToolResult>) {
    if (ctx.toolName === "blocked") {
      return {
        content: [{ type: "text", text: "blocked by middleware", data: new Uint8Array(), mimeType: "", uri: "" }],
        isError: true,
      };
    }
    return next();
  }
}

describe("v2 streaming calls", () => {
  let server: RapidMCP;
  let channel: Channel;
  let v2: McpClient;
  let finished: string[];
  let aborted: string[];

  async function start(opts: Partial<RapidMCPOptions> = {}) {
    finished = [];
    aborted = [];
    server = new RapidMCP({ name: "calls", version: "1.0", ...opts });
    server.use(new Block());
    server.addTool({ name: "echo", execute: async (a: any) => a.text });
    server.addTool({ name: "add", execute: async (a: any) => ({ sum: a.a + a.b }) });
    server.addTool({ name: "blocked", execute: async () => "never" });
    server.addTool({
      name: "friendly",
      execute: async () => {
        throw new ToolError("order id must start with ORD-");
      },
    });
    server.addTool({
      name: "chatty",
      execute: async (_a: unknown, ctx: any) => {
        ctx.log.debug("starting");
        ctx.reportProgress(1, 2);
        ctx.log.warning("halfway");
        ctx.reportProgress(2, 2);
        return "done";
      },
    });
    server.addTool({
      name: "progress_then_fail",
      execute: async (_a: unknown, ctx: any) => {
        ctx.reportProgress(1, 2);
        throw new ToolError("gave up halfway");
      },
    });
    server.addTool({
      name: "wants_sampling",
      execute: async (_a: unknown, ctx: any) => {
        await ctx.sample({ messages: [], maxTokens: 1 });
        return "never";
      },
    });
    server.addTool({
      name: "slow",
      execute: async (_a: unknown, ctx: any) => {
        ctx.signal.addEventListener("abort", () => aborted.push("slow"), { once: true });
        await sleep(600);
        finished.push("slow");
        return "done";
      },
    });
    server.addResource({
      uri: "res://text",
      name: "text",
      mimeType: "text/markdown",
      load: async () => ({ text: "# hello" }),
    });
    server.addResource({
      uri: "res://logo",
      name: "logo",
      mimeType: "image/png",
      load: async () => ({ blob: new Uint8Array([0x89, 0x50]) }),
    });
    server.addResource({
      uri: "res://broken",
      name: "broken",
      load: async () => {
        throw new Error("password is hunter2");
      },
    });
    server.addResourceTemplate({
      uriTemplate: "res://items/{id}",
      name: "item",
      load: async (args) => ({ text: `item ${args.id}` }),
    });
    server.addPrompt({
      name: "greet",
      arguments: [{ name: "who", required: true }, { name: "tone" }],
      load: async (args) => `hi ${args.who}, ${args.tone ?? "kind"}`,
    });
    server.addPrompt({
      name: "broken_prompt",
      load: async () => {
        throw new Error("secret");
      },
    });
    const port = await server.listen();
    channel = createChannel(`127.0.0.1:${port}`);
    v2 = createClientFactory().create(McpDefinition, channel);
  }

  beforeEach(() => start());

  afterEach(async () => {
    channel.close();
    await server.close();
  });

  async function events<T>(stream: AsyncIterable<T>): Promise<T[]> {
    const out: T[] = [];
    for await (const event of stream) out.push(event);
    return out;
  }

  const kinds = (list: Array<{ event?: { $case: string } }>) => list.map((e) => e.event?.$case);

  /** Run a stream expected to fail; return its MCP code and message. */
  async function failure(open: (onTrailer: (t: Metadata) => void) => AsyncIterable<unknown>) {
    let trailer = new Metadata();
    const err = await events(open((t) => (trailer = t))).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ClientError);
    return [Number(trailer.get("mcp-error-code")), (err as ClientError).details];
  }

  const call = (name: string, args = "{}", m = meta(), options = {}) =>
    v2.callTool({ meta: m, name, arguments: args, inputResponses: {}, requestState: new Uint8Array() }, options);

  // ── tool calls ───────────────────────────────────────────────────────────

  it("ends a tool call with one complete event", async () => {
    const list = await events(call("echo", '{"text":"hi"}'));

    expect(kinds(list)).toEqual(["complete"]);
    const result = (list[0].event as any).complete;
    expect(result.content.map((c: any) => [c.type, c.text])).toEqual([["text", "hi"]]);
    expect(result.isError).toBe(false);
    expect(result.structuredContent).toBe("");
    expect(result.meta.serverInfo.name).toBe("calls");
  });

  it("sends an object result as structured content and as text", async () => {
    const result = ((await events(call("add", '{"a":2,"b":3}'))).at(-1)!.event as any).complete;

    expect(JSON.parse(result.structuredContent)).toEqual({ sum: 5 });
    expect(JSON.parse(result.content[0].text)).toEqual({ sum: 5 });
  });

  it("runs middleware around v2 calls", async () => {
    const result = ((await events(call("blocked"))).at(-1)!.event as any).complete;

    expect([result.isError, result.content[0].text]).toEqual([true, "blocked by middleware"]);
  });

  it("returns a ToolError as a complete result marked isError", async () => {
    const result = ((await events(call("friendly"))).at(-1)!.event as any).complete;

    expect([result.isError, result.content[0].text]).toEqual([true, "order id must start with ORD-"]);
  });

  it("sends no progress or log events unless the request asked", async () => {
    expect(kinds(await events(call("chatty")))).toEqual(["complete"]);
  });

  it("echoes the request's progress token", async () => {
    const list = await events(call("chatty", "{}", meta({ progressToken: "tok-7" })));

    expect(kinds(list)).toEqual(["progress", "progress", "complete"]);
    expect(
      list.slice(0, 2).map((e: any) => [e.event.progress.token, e.event.progress.progress, e.event.progress.total]),
    ).toEqual([
      ["tok-7", 1, 2],
      ["tok-7", 2, 2],
    ]);
  });

  it("respects the requested log level", async () => {
    const debug = await events(call("chatty", "{}", meta({ logLevel: "debug" })));
    const warning = await events(call("chatty", "{}", meta({ logLevel: "warning" })));
    const logs = (list: any[]) =>
      list.filter((e) => e.event.$case === "log").map((e) => [e.event.log.level, JSON.parse(e.event.log.data)]);

    expect(logs(debug)).toEqual([
      ["debug", { message: "starting", extra: null }],
      ["warning", { message: "halfway", extra: null }],
    ]);
    expect(logs(warning).map((l) => l[0])).toEqual(["warning"]);
  });

  it("returns a plain result when a log level is set but nothing is logged", async () => {
    expect(kinds(await events(call("echo", '{"text":"x"}', meta({ logLevel: "debug" }))))).toEqual(["complete"]);
  });

  it("rejects an unknown log level", async () => {
    const [code] = await failure((onTrailer) =>
      call("echo", '{"text":"x"}', meta({ logLevel: "loud" }), { onTrailer }),
    );

    expect(code).toBe(-32602);
  });

  it("still delivers a failure that follows progress", async () => {
    const list = await events(call("progress_then_fail", "{}", meta({ progressToken: "t" })));

    expect(kinds(list)).toEqual(["progress", "complete"]);
    const result = (list.at(-1)!.event as any).complete;
    expect([result.isError, result.content[0].text]).toEqual([true, "gave up halfway"]);
  });

  it("rejects an unknown tool and bad arguments as invalid params", async () => {
    expect(await failure((onTrailer) => call("nope", "{}", meta(), { onTrailer }))).toEqual([
      -32602,
      "Tool 'nope' not found",
    ]);
    expect(await failure((onTrailer) => call("echo", "{not json", meta(), { onTrailer }))).toEqual([
      -32602,
      "Invalid arguments for tool 'echo': not valid JSON",
    ]);
    expect(await failure((onTrailer) => call("echo", "[1]", meta(), { onTrailer }))).toEqual([
      -32602,
      "Invalid arguments for tool 'echo': expected a JSON object",
    ]);
  });

  it("says sampling is not available on v2", async () => {
    const [code, message] = await failure((onTrailer) => call("wants_sampling", "{}", meta(), { onTrailer }));

    expect(code).toBe(-32601);
    expect(message).toContain("v2");
  });

  it("aborts the tool's signal when the RPC is cancelled", async () => {
    const controller = new AbortController();
    const reading = events(call("slow", "{}", meta(), { signal: controller.signal })).catch(() => "cancelled");
    await sleep(100);

    controller.abort();
    expect(await reading).toBe("cancelled");
    await sleep(100);

    expect(aborted).toEqual(["slow"]);
  });

  // ── resources ────────────────────────────────────────────────────────────

  const read = (uri: string, options = {}) =>
    v2.readResource({ meta: meta(), uri, inputResponses: {}, requestState: new Uint8Array() }, options);

  it("reads a text resource", async () => {
    const list = await events(read("res://text"));

    expect(kinds(list)).toEqual(["complete"]);
    const item = (list[0].event as any).complete.content[0];
    expect([item.type, item.text, item.mimeType, item.uri]).toEqual([
      "text",
      "# hello",
      "text/markdown",
      "res://text",
    ]);
    expect((list[0].event as any).complete.cache.ttlMs).toBe(0n);
  });

  it("types a binary resource by its mime type", async () => {
    const item = ((await events(read("res://logo")))[0].event as any).complete.content[0];

    expect([item.type, [...item.data], item.mimeType]).toEqual(["image", [0x89, 0x50], "image/png"]);
  });

  it("reads a templated resource", async () => {
    const item = ((await events(read("res://items/42")))[0].event as any).complete.content[0];

    expect(item.text).toBe("item 42");
  });

  it("rejects a missing resource as invalid params", async () => {
    expect(await failure((onTrailer) => read("res://nope", { onTrailer }))).toEqual([
      -32602,
      "Resource 'res://nope' not found",
    ]);
  });

  it("does not leak a failing resource handler's exception", async () => {
    expect(await failure((onTrailer) => read("res://broken", { onTrailer }))).toEqual([
      -32603,
      "Resource handler for 'res://broken' failed",
    ]);
  });

  // ── prompts ──────────────────────────────────────────────────────────────

  const prompt = (name: string, args: Record<string, string> = {}, options = {}) =>
    v2.getPrompt(
      { meta: meta(), name, arguments: args, inputResponses: {}, requestState: new Uint8Array() },
      options,
    );

  it("returns a prompt as a user message", async () => {
    const list = await events(prompt("greet", { who: "Ada" }));

    expect(kinds(list)).toEqual(["complete"]);
    const message = (list[0].event as any).complete.messages[0];
    expect([message.role, message.content.type, message.content.text]).toEqual(["user", "text", "hi Ada, kind"]);
  });

  it("rejects an unknown prompt and a missing required argument as invalid params", async () => {
    expect(await failure((onTrailer) => prompt("nope", {}, { onTrailer }))).toEqual([
      -32602,
      "Prompt 'nope' not found",
    ]);
    expect(await failure((onTrailer) => prompt("greet", {}, { onTrailer }))).toEqual([
      -32602,
      "Missing required argument(s) for prompt 'greet': who",
    ]);
  });

  it("does not leak a failing prompt handler's exception", async () => {
    expect(await failure((onTrailer) => prompt("broken_prompt", {}, { onTrailer }))).toEqual([
      -32603,
      "Prompt handler 'broken_prompt' failed",
    ]);
  });

  // ── auth ─────────────────────────────────────────────────────────────────

  it("requires the token on streaming calls when the server has auth", async () => {
    channel.close();
    await server.close();
    await start({ auth: (token) => token === "s3cret" });

    const denied = await events(call("echo", '{"text":"x"}')).then(
      () => null,
      (e: unknown) => e,
    );
    const allowed = await events(
      call("echo", '{"text":"x"}', meta(), { metadata: Metadata({ authorization: "Bearer s3cret" }) }),
    );

    expect((denied as ClientError).code).toBe(Status.UNAUTHENTICATED);
    expect((allowed.at(-1)!.event as any).complete.content[0].text).toBe("x");
  });
});
