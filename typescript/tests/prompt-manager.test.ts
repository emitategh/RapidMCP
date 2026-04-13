import { describe, it, expect } from "vitest";
import { PromptManager } from "../src/prompts/prompt-manager.js";

describe("PromptManager", () => {
  it("registers and lists a prompt", () => {
    const pm = new PromptManager();
    pm.addPrompt({
      name: "greet",
      description: "Greeting",
      arguments: [{ name: "name", required: true }],
      load: async (args) => `Hello, ${args.name}!`,
    });
    const list = pm.listPrompts();
    expect(list).toHaveLength(1);
    expect(list[0].name).toBe("greet");
    expect(list[0].arguments).toHaveLength(1);
  });

  it("gets a prompt with arguments", async () => {
    const pm = new PromptManager();
    pm.addPrompt({
      name: "greet",
      description: "Greeting",
      arguments: [{ name: "name", required: true }],
      load: async (args) => `Hello, ${args.name}!`,
    });
    const messages = await pm.getPrompt("greet", { name: "World" });
    expect(messages).toHaveLength(1);
    expect(messages[0].role).toBe("user");
    expect(messages[0].content.text).toBe("Hello, World!");
  });

  it("throws for unknown prompt", async () => {
    const pm = new PromptManager();
    await expect(pm.getPrompt("nope", {})).rejects.toThrow("not found");
  });

  it("completes argument values", async () => {
    const pm = new PromptManager();
    pm.addPrompt({
      name: "greet",
      description: "Greeting",
      arguments: [{
        name: "style",
        required: false,
        complete: async (value) => ({
          values: ["formal", "casual"].filter(s => s.startsWith(value)),
        }),
      }],
      load: async () => "hi",
    });
    const result = await pm.complete("ref/prompt", "greet", "style", "for");
    expect(result.values).toEqual(["formal"]);
  });
});
