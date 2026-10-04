import { describe, it, expect } from "vitest";
import { McpDefinition, ToolAnnotations } from "../generated/mcp_v2.js";

describe("v2 generated stubs", () => {
  it("describe the service", () => {
    expect(McpDefinition.fullName).toBe("mcp.v2.Mcp");
    expect(Object.keys(McpDefinition.methods).sort()).toEqual([
      "callTool",
      "complete",
      "discover",
      "getPrompt",
      "listPrompts",
      "listResourceTemplates",
      "listResources",
      "listTools",
      "listen",
      "readResource",
    ]);
    expect(McpDefinition.methods.callTool.responseStream).toBe(true);
    expect(McpDefinition.methods.readResource.responseStream).toBe(true);
    expect(McpDefinition.methods.getPrompt.responseStream).toBe(true);
    expect(McpDefinition.methods.listen.responseStream).toBe(true);
  });

  it("let annotation hints be left unset", () => {
    const decoded = ToolAnnotations.decode(ToolAnnotations.encode({ title: "t" }).finish());
    expect(decoded.destructiveHint).toBeUndefined();
  });
});
