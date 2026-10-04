import { describe, it, expect, afterEach } from "vitest";
import { createChannel, createClientFactory, type Channel } from "nice-grpc";
import { ClientError, Metadata, Status } from "nice-grpc-common";
import { z } from "zod";
import { RapidMCP, type RapidMCPOptions } from "../src/server.js";
import { Client } from "../src/client.js";
import { McpDefinition, ErrorData, CacheScope, type McpClient } from "../generated/mcp_v2.js";

const META = {
  protocolVersion: "2026-07-28",
  clientCapabilities: { extensions: {} },
  clientInfo: { name: "test", version: "0" },
};

describe("the v2 service", () => {
  let server: RapidMCP;
  let channel: Channel | null = null;
  let port = 0;

  async function start(opts: Partial<RapidMCPOptions> = {}, bare = false): Promise<McpClient> {
    server = new RapidMCP({ name: "v2-server", version: "1.2.3", ...opts });
    if (!bare) {
      server.addTool({
        name: "echo",
        description: "Echo",
        parameters: z.object({ text: z.string() }),
        annotations: { readOnly: true },
        execute: async (args) => args.text,
      });
      server.addTool({ name: "plain", execute: async () => "x" });
      server.addResource({
        uri: "res://a",
        name: "a",
        description: "A",
        load: async () => ({ text: "a" }),
      });
      server.addResourceTemplate({
        uriTemplate: "res://items/{id}",
        name: "item",
        load: async () => ({ text: "i" }),
      });
      server.addPrompt({
        name: "greet",
        description: "Greet",
        arguments: [
          { name: "who", complete: async (v) => ({ values: [`${v}lice`, `${v}da`], total: 2 }) },
        ],
        load: async () => "hi",
      });
    }
    port = await server.listen();
    channel = createChannel(`127.0.0.1:${port}`);
    return createClientFactory().create(McpDefinition, channel);
  }

  afterEach(async () => {
    channel?.close();
    channel = null;
    await server.close();
  });

  /** Run a call expected to fail; return its status, MCP code and trailer. */
  async function failure(call: (onTrailer: (t: Metadata) => void) => Promise<unknown>) {
    let trailer = new Metadata();
    const err = await call((t) => (trailer = t)).then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ClientError);
    return {
      status: (err as ClientError).code,
      mcpCode: Number(trailer.get("mcp-error-code")),
      trailer,
    };
  }

  it("reports identity, versions and capabilities on discover", async () => {
    const v2 = await start();

    const result = await v2.discover({ meta: META });

    expect(result.meta?.serverInfo).toEqual({ name: "v2-server", version: "1.2.3", icons: [] });
    expect(result.supportedVersions).toEqual(["2026-07-28"]);
    expect(result.capabilities?.tools?.listChanged).toBe(true);
    expect(result.capabilities?.resources).toBeDefined();
    expect(result.capabilities?.prompts).toBeDefined();
    expect(result.cache).toEqual({ ttlMs: 0n, scope: CacheScope.CACHE_SCOPE_PRIVATE });
  });

  it("omits capabilities for what is not registered", async () => {
    const v2 = await start({}, true);

    const result = await v2.discover({ meta: META });

    expect(result.capabilities?.tools).toBeUndefined();
    expect(result.capabilities?.resources).toBeUndefined();
    expect(result.capabilities?.prompts).toBeUndefined();
  });

  it("lists tools with schemas and only the hints that were set", async () => {
    const v2 = await start();

    const result = await v2.listTools({ meta: META, cursor: "" });
    const tools = new Map(result.tools.map((t) => [t.name, t]));

    expect([...tools.keys()].sort()).toEqual(["echo", "plain"]);
    expect(JSON.parse(tools.get("echo")!.inputSchema).properties.text.type).toBe("string");
    expect(tools.get("echo")!.annotations?.readOnlyHint).toBe(true);
    expect(tools.get("echo")!.annotations?.destructiveHint).toBeUndefined();
    expect(tools.get("plain")!.annotations).toBeUndefined();
    expect(result.meta?.serverInfo?.name).toBe("v2-server");
  });

  it("lists resources, templates and prompts", async () => {
    const v2 = await start();

    const resources = await v2.listResources({ meta: META, cursor: "" });
    const templates = await v2.listResourceTemplates({ meta: META, cursor: "" });
    const prompts = await v2.listPrompts({ meta: META, cursor: "" });

    expect(resources.resources.map((r) => [r.uri, r.description])).toEqual([["res://a", "A"]]);
    expect(templates.templates.map((t) => t.uriTemplate)).toEqual(["res://items/{id}"]);
    expect(prompts.prompts.map((p) => [p.name, p.arguments.map((a) => a.name)])).toEqual([
      ["greet", ["who"]],
    ]);
  });

  it("returns completion values", async () => {
    const v2 = await start();

    const result = await v2.complete({
      meta: META,
      ref: { type: "ref/prompt", name: "greet" },
      argument: { name: "who", value: "A" },
    });

    expect(result.values).toEqual(["Alice", "Ada"]);
    expect(result.total).toBe(2);
  });

  it("paginates lists and tolerates a garbage cursor", async () => {
    const v2 = await start({ pageSize: 1 });

    const first = await v2.listTools({ meta: META, cursor: "" });
    const second = await v2.listTools({ meta: META, cursor: first.nextCursor });
    const garbage = await v2.listTools({ meta: META, cursor: "not-a-cursor" });

    expect(first.tools.map((t) => t.name)).toEqual(["echo"]);
    expect(second.tools.map((t) => t.name)).toEqual(["plain"]);
    expect(second.nextCursor).toBe("");
    expect(garbage.tools.map((t) => t.name)).toEqual(["echo"]);
  });

  it("rejects a request without meta as invalid params", async () => {
    const v2 = await start();

    const { status, mcpCode } = await failure((onTrailer) => v2.listTools({}, { onTrailer }));

    expect([mcpCode, status]).toEqual([-32602, Status.INVALID_ARGUMENT]);
  });

  it("rejects a request without client capabilities as invalid params", async () => {
    const v2 = await start();

    const { mcpCode } = await failure((onTrailer) =>
      v2.discover({ meta: { protocolVersion: "2026-07-28" } }, { onTrailer }),
    );

    expect(mcpCode).toBe(-32602);
  });

  it("names the supported versions when the requested one is not", async () => {
    const v2 = await start();

    const { status, mcpCode, trailer } = await failure((onTrailer) =>
      v2.discover({ meta: { ...META, protocolVersion: "1900-01-01" } }, { onTrailer }),
    );
    const data = ErrorData.decode(trailer.get("mcp-error-data-bin")!);

    expect([mcpCode, status]).toEqual([-32022, Status.FAILED_PRECONDITION]);
    expect(data.supportedVersions).toEqual(["2026-07-28"]);
    expect(data.requestedVersion).toBe("1900-01-01");
  });

  it("requires the token on v2 calls when the server has auth", async () => {
    const v2 = await start({ auth: (token) => token === "s3cret" });

    const denied = await v2.discover({ meta: META }).then(
      () => null,
      (e: unknown) => e,
    );
    const allowed = await v2.discover(
      { meta: META },
      { metadata: Metadata({ authorization: "Bearer s3cret" }) },
    );

    expect((denied as ClientError).code).toBe(Status.UNAUTHENTICATED);
    expect(allowed.meta?.serverInfo?.name).toBe("v2-server");
  });

  it("keeps serving v1 clients on the same port", async () => {
    await start();
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect();

    const result = await client.callTool("echo", { text: "still v1" });
    await client.close();

    expect(result.content[0].text).toBe("still v1");
  });
});
