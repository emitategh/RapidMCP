import { describe, it, expect } from "vitest";
import { McpDefinition, ToolAnnotations } from "../generated/mcp_v2.js";

describe("v2 generated stubs", () => {
  it("describe the phase 1 service", () => {
    expect(McpDefinition.fullName).toBe("mcp.v2.Mcp");
    expect(Object.keys(McpDefinition.methods).sort()).toEqual([
      "complete",
      "discover",
      "listPrompts",
      "listResourceTemplates",
      "listResources",
      "listTools",
    ]);
  });

  it("let annotation hints be left unset", () => {
    const decoded = ToolAnnotations.decode(ToolAnnotations.encode({ title: "t" }).finish());
    expect(decoded.destructiveHint).toBeUndefined();
  });
});
