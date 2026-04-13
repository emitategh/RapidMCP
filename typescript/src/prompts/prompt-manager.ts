import type { PromptConfig, RegisteredPrompt, CompletionResult } from "./prompt.js";
import { McpError } from "../errors.js";

export class PromptManager {
  private _prompts = new Map<string, RegisteredPrompt>();

  addPrompt(config: PromptConfig): void {
    this._prompts.set(config.name, {
      name: config.name,
      description: config.description ?? "",
      arguments: config.arguments ?? [],
      load: config.load,
    });
  }

  listPrompts(): RegisteredPrompt[] {
    return [...this._prompts.values()];
  }

  async getPrompt(
    name: string,
    args: Record<string, string>,
  ): Promise<Array<{ role: string; content: { type: string; text: string; data: Uint8Array; mimeType: string; uri: string } }>> {
    const prompt = this._prompts.get(name);
    if (!prompt) {
      throw new McpError(404, `Prompt '${name}' not found`);
    }
    const text = await prompt.load(args);
    return [{
      role: "user",
      content: { type: "text", text, data: new Uint8Array(), mimeType: "", uri: "" },
    }];
  }

  async complete(
    refType: string,
    refName: string,
    argumentName: string,
    value: string,
  ): Promise<CompletionResult> {
    const prompt = this._prompts.get(refName);
    if (!prompt) {
      return { values: [] };
    }
    const arg = prompt.arguments.find((a) => a.name === argumentName);
    if (!arg?.complete) {
      return { values: [] };
    }
    return arg.complete(value);
  }
}
