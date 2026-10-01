import { describe, it, expect } from "vitest";
import { RapidMCP } from "../src/server.js";
import { ToolError } from "../src/errors.js";

function server(opts: { maskErrorDetails?: boolean } = {}): RapidMCP {
  const s = new RapidMCP({ name: "errors", ...opts });
  s.addTool({
    name: "friendly",
    execute: async () => {
      throw new ToolError("The order id must start with ORD-");
    },
  });
  s.addTool({
    name: "boom",
    execute: async () => {
      throw new Error("db password is hunter2");
    },
  });
  return s;
}

describe("what a client sees when a tool throws", () => {
  it("returns a ToolError message verbatim", async () => {
    const result = await server().toolManager.callTool("friendly", {}, null);

    expect(result.isError).toBe(true);
    expect(result.content.map((c) => c.text)).toEqual(["The order id must start with ORD-"]);
  });

  it("names the tool for any other exception", async () => {
    const result = await server().toolManager.callTool("boom", {}, null);

    expect(result.isError).toBe(true);
    expect(result.content.map((c) => c.text)).toEqual([
      "Error calling tool 'boom': db password is hunter2",
    ]);
  });

  it("hides the exception text when maskErrorDetails is set", async () => {
    const result = await server({ maskErrorDetails: true }).toolManager.callTool("boom", {}, null);

    expect(result.content.map((c) => c.text)).toEqual(["Error calling tool 'boom'"]);
  });

  it("still shows ToolError messages when maskErrorDetails is set", async () => {
    const result = await server({ maskErrorDetails: true }).toolManager.callTool("friendly", {}, null);

    expect(result.content.map((c) => c.text)).toEqual(["The order id must start with ORD-"]);
  });
});
