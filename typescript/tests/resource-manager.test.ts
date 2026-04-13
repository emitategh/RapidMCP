import { describe, it, expect } from "vitest";
import { ResourceManager } from "../src/resources/resource-manager.js";

describe("ResourceManager", () => {
  it("registers and lists a resource", () => {
    const rm = new ResourceManager();
    rm.addResource({ uri: "res://info", name: "Info", mimeType: "application/json", load: async () => ({ text: "{}" }) });
    const list = rm.listResources();
    expect(list).toHaveLength(1);
    expect(list[0].uri).toBe("res://info");
  });

  it("reads a resource", async () => {
    const rm = new ResourceManager();
    rm.addResource({ uri: "res://info", name: "Info", load: async () => ({ text: "hello" }) });
    const result = await rm.readResource("res://info");
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe("hello");
  });

  it("registers and lists a resource template", () => {
    const rm = new ResourceManager();
    rm.addResourceTemplate({
      uriTemplate: "res://items/{id}",
      name: "Item",
      arguments: [{ name: "id", required: true }],
      load: async (args) => ({ text: args.id }),
    });
    const list = rm.listResourceTemplates();
    expect(list).toHaveLength(1);
    expect(list[0].uriTemplate).toBe("res://items/{id}");
  });

  it("reads a resource template with matched URI", async () => {
    const rm = new ResourceManager();
    rm.addResourceTemplate({
      uriTemplate: "res://items/{id}",
      name: "Item",
      arguments: [{ name: "id", required: true }],
      load: async (args) => ({ text: `item-${args.id}` }),
    });
    const result = await rm.readResource("res://items/42");
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe("item-42");
  });

  it("throws for unknown resource URI", async () => {
    const rm = new ResourceManager();
    await expect(rm.readResource("res://nope")).rejects.toThrow();
  });
});
