import { describe, it, expect, afterEach } from "vitest";
import { z } from "zod";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("server features shared with the Python implementation", () => {
  let server: RapidMCP;
  let client: Client;

  async function start(configure: (s: RapidMCP) => void, prepare?: (c: Client) => void) {
    server = new RapidMCP({ name: "parity", version: "0.1.0" });
    configure(server);
    const port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { requestTimeout: 3000 });
    prepare?.(client);
    await client.connect();
  }

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  describe("capabilities", () => {
    it("advertises only what is registered", async () => {
      await start((s) => s.addTool({ name: "only_tool", execute: async () => "ok" }));

      expect(client.serverInfo?.capabilities).toEqual({
        tools: true,
        toolsListChanged: true,
        resources: false,
        prompts: false,
      });
    });

    it("advertises resources and prompts when present", async () => {
      await start((s) => {
        s.addResourceTemplate({
          uriTemplate: "res://items/{id}",
          name: "item",
          load: async () => ({ text: "x" }),
        });
        s.addPrompt({ name: "greet", load: async () => "hi" });
      });

      expect(client.serverInfo?.capabilities).toEqual({
        tools: false,
        toolsListChanged: true,
        resources: true,
        prompts: true,
      });
    });
  });

  describe("binary resources", () => {
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47]);

    it("returns raw bytes for a Uint8Array blob, typed by mime", async () => {
      await start((s) => {
        s.addResource({
          uri: "res://logo",
          name: "logo",
          mimeType: "image/png",
          load: async () => ({ blob: png }),
        });
      });

      const result = await client.readResource("res://logo");

      expect(result.content).toHaveLength(1);
      expect(result.content[0].type).toBe("image");
      expect(result.content[0].mimeType).toBe("image/png");
      expect([...result.content[0].data]).toEqual([0x89, 0x50, 0x4e, 0x47]);
    });

    it("decodes a base64 string blob and types unknown binary as resource", async () => {
      await start((s) => {
        s.addResourceTemplate({
          uriTemplate: "res://files/{name}",
          name: "file",
          mimeType: "application/octet-stream",
          load: async () => ({ blob: "iVBORw==" }), // base64 of 89 50 4E 47
        });
      });

      const result = await client.readResource("res://files/a.bin");

      expect(result.content[0].type).toBe("resource");
      expect([...result.content[0].data]).toEqual([0x89, 0x50, 0x4e, 0x47]);
    });
  });

  describe("output schema", () => {
    it("advertises a zod output schema as JSON Schema", async () => {
      await start((s) =>
        s.addTool({
          name: "weather",
          outputSchema: z.object({ tempC: z.number() }),
          execute: async () => ({ tempC: 21 }),
        }),
      );

      const { items } = await client.listTools();

      expect(items[0].outputSchema).toMatchObject({
        type: "object",
        properties: { tempC: { type: "number" } },
        required: ["tempC"],
      });
    });

    it("advertises a plain JSON Schema object as given", async () => {
      const schema = { type: "object", properties: { ok: { type: "boolean" } } };
      await start((s) =>
        s.addTool({ name: "status", outputSchema: schema, execute: async () => ({ ok: true }) }),
      );

      const { items } = await client.listTools();

      expect(items[0].outputSchema).toEqual(schema);
    });
  });

  describe("context", () => {
    it("lists the client's roots", async () => {
      await start(
        (s) =>
          s.addTool({
            name: "roots",
            execute: async (_a: unknown, ctx: any) => {
              const roots = await ctx.listRoots();
              return roots.map((r: { uri: string; name: string }) => `${r.name}=${r.uri}`).join(",");
            },
          }),
        (c) => c.setRootsHandler(async () => [{ uri: "file:///work", name: "work" }]),
      );

      const result = await client.callTool("roots");

      expect(result.content[0].text).toBe("work=file:///work");
    });

    it("forwards tools, tool choice, model preferences and tool-use content when sampling", async () => {
      let seen: any = null;
      await start(
        (s) =>
          s.addTool({
            name: "sampler",
            execute: async (_a: unknown, ctx: any) => {
              await ctx.sample({
                messages: [
                  {
                    role: "assistant",
                    content: [
                      { type: "tool_use", toolUseId: "tu_1", toolName: "lookup", toolInput: '{"q":"x"}' },
                    ],
                  },
                ],
                maxTokens: 64,
                tools: [{ name: "lookup", description: "Look up", inputSchema: '{"type":"object"}' }],
                toolChoice: "required",
                modelPreferences: { hints: ["claude"], speedPriority: 0.5 },
              });
              return "ok";
            },
          }),
        (c) =>
          c.setSamplingHandler(async (req) => {
            seen = req;
            return { role: "assistant", content: [], model: "m", stopReason: "end" };
          }),
      );

      await client.callTool("sampler");

      expect(seen.toolChoice).toBe("required");
      expect(seen.tools).toEqual([
        { name: "lookup", description: "Look up", inputSchema: '{"type":"object"}' },
      ]);
      expect(seen.modelPreferences.hints).toEqual([{ name: "claude" }]);
      expect(seen.modelPreferences.speedPriority).toBe(0.5);
      const item = seen.messages[0].content[0];
      expect([item.type, item.toolUseId, item.toolName, item.toolInput]).toEqual([
        "tool_use",
        "tu_1",
        "lookup",
        '{"q":"x"}',
      ]);
    });
  });

  describe("client notifications", () => {
    it("calls resource-subscribe handlers with the uri", async () => {
      const subscribed: string[] = [];
      await start((s) => s.onResourceSubscribe((uri) => void subscribed.push(uri)));

      client.subscribeResource("res://feed");
      await client.ping();
      await sleep(20);

      expect(subscribed).toEqual(["res://feed"]);
    });

    it("calls roots-list-changed handlers", async () => {
      let calls = 0;
      await start((s) => s.onRootsListChanged(() => void calls++));

      client.notifyRootsListChanged();
      await client.ping();
      await sleep(20);

      expect(calls).toBe(1);
    });

    it("keeps the session alive when a notification handler throws", async () => {
      await start((s) =>
        s.onResourceSubscribe(() => {
          throw new Error("handler bug");
        }),
      );

      client.subscribeResource("res://feed");

      expect(await client.ping()).toBe(true);
    });
  });
});
