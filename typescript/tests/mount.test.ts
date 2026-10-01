import { describe, it, expect } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Middleware, type ToolCallContext, type CallToolResult } from "../src/middleware.js";

function subServer(): RapidMCP {
  const sub = new RapidMCP({ name: "users" });
  sub.addTool({ name: "get", execute: async () => "user" });
  sub.addResource({ uri: "res://profile", name: "profile", load: async () => ({ text: "p" }) });
  sub.addResourceTemplate({
    uriTemplate: "res://items/{id}",
    name: "item",
    load: async (args) => ({ text: `item ${args.id}` }),
  });
  sub.addPrompt({
    name: "greet",
    arguments: [{ name: "who", complete: async (v) => ({ values: [`${v}lice`] }) }],
    load: async (args) => `hi ${args.who}`,
  });
  return sub;
}

describe("mount", () => {
  it("registers the sub-server's tools and prompts under the prefix", async () => {
    const main = new RapidMCP({ name: "main" });
    main.mount(subServer(), { prefix: "users" });

    expect(main.toolManager.listTools().map((t) => t.name)).toEqual(["users_get"]);
    expect((await main.toolManager.callTool("users_get", {}, null)).content[0].text).toBe("user");
    expect(main.promptManager.listPrompts().map((p) => p.name)).toEqual(["users_greet"]);
    const messages = await main.promptManager.getPrompt("users_greet", { who: "Ada" });
    expect(messages[0].content.text).toBe("hi Ada");
  });

  it("keeps argument completions working for mounted prompts", async () => {
    const main = new RapidMCP({ name: "main" });
    main.mount(subServer(), { prefix: "users" });

    const result = await main.promptManager.complete("ref/prompt", "users_greet", "who", "A");

    expect(result.values).toEqual(["Alice"]);
  });

  it("inserts the prefix as the first path segment of resource URIs", async () => {
    const main = new RapidMCP({ name: "main" });
    main.mount(subServer(), { prefix: "users" });

    expect(main.resourceManager.listResources().map((r) => r.uri)).toEqual([
      "res://users/profile",
    ]);
    expect(main.resourceManager.listResourceTemplates().map((t) => t.uriTemplate)).toEqual([
      "res://users/items/{id}",
    ]);
    const content = await main.resourceManager.readResource("res://users/items/7");
    expect(content[0].text).toBe("item 7");
  });

  it("refuses a colliding name and registers nothing", () => {
    const main = new RapidMCP({ name: "main" });
    main.addPrompt({ name: "users_greet", load: async () => "mine" });

    expect(() => main.mount(subServer(), { prefix: "users" })).toThrow(/users_greet/);
    expect(main.toolManager.listTools()).toEqual([]);
    expect(main.resourceManager.listResources()).toEqual([]);
  });

  it("does not adopt the sub-server's middleware", async () => {
    class Reject extends Middleware {
      async onToolCall(_ctx: ToolCallContext): Promise<CallToolResult> {
        return { content: [], isError: true };
      }
    }
    const sub = subServer();
    sub.use(new Reject());
    const main = new RapidMCP({ name: "main" });
    main.mount(sub, { prefix: "users" });
    const port = await main.listen();

    const { Client } = await import("../src/client.js");
    const client = new Client(`127.0.0.1:${port}`);
    await client.connect();
    const result = await client.callTool("users_get");
    await client.close();
    await main.close();

    expect(result.isError).toBe(false);
    expect(result.content[0].text).toBe("user");
  });
});
