import { describe, it, expect } from "vitest";
import {
  convertTool,
  convertResource,
  convertResourceTemplate,
  convertPrompt,
  convertContentItem,
  convertCallToolResult,
  convertReadResourceResult,
  convertGetPromptResult,
  convertCompleteResult,
} from "../src/types.js";

describe("convertContentItem", () => {
  it("converts a text content item", () => {
    const proto = {
      type: "text",
      text: "hello",
      data: new Uint8Array(),
      mimeType: "",
      uri: "",
      toolUseId: "",
      toolName: "",
      toolInput: "",
      toolResultId: "",
    };
    const result = convertContentItem(proto);
    expect(result).toEqual({
      type: "text",
      text: "hello",
      data: new Uint8Array(),
      mimeType: "",
      uri: "",
    });
  });
});

describe("convertTool", () => {
  it("parses inputSchema JSON and annotations", () => {
    const proto = {
      name: "my_tool",
      description: "A tool",
      inputSchema: '{"type":"object","properties":{}}',
      outputSchema: "",
      annotations: {
        title: "My Tool",
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    };
    const result = convertTool(proto);
    expect(result.name).toBe("my_tool");
    expect(result.description).toBe("A tool");
    expect(result.inputSchema).toEqual({ type: "object", properties: {} });
    expect(result.outputSchema).toBeNull();
    expect(result.annotations.title).toBe("My Tool");
    expect(result.annotations.readOnlyHint).toBe(true);
  });

  it("parses outputSchema when present", () => {
    const proto = {
      name: "t",
      description: "",
      inputSchema: "{}",
      outputSchema: '{"type":"string"}',
      annotations: {
        title: "",
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    };
    const result = convertTool(proto);
    expect(result.outputSchema).toEqual({ type: "string" });
  });
});

describe("convertResource", () => {
  it("converts all fields", () => {
    const proto = { uri: "file:///a", name: "A", description: "desc", mimeType: "text/plain" };
    const result = convertResource(proto);
    expect(result).toEqual({ uri: "file:///a", name: "A", description: "desc", mimeType: "text/plain" });
  });
});

describe("convertResourceTemplate", () => {
  it("converts all fields", () => {
    const proto = { uriTemplate: "file:///{path}", name: "T", description: "d", mimeType: "" };
    const result = convertResourceTemplate(proto);
    expect(result).toEqual({ uriTemplate: "file:///{path}", name: "T", description: "d", mimeType: "" });
  });
});

describe("convertPrompt", () => {
  it("converts prompt with arguments", () => {
    const proto = {
      name: "greet",
      description: "Greeting",
      arguments: [
        { name: "name", description: "Who", required: true },
      ],
    };
    const result = convertPrompt(proto);
    expect(result.name).toBe("greet");
    expect(result.arguments).toHaveLength(1);
    expect(result.arguments[0]).toEqual({ name: "name", description: "Who", required: true });
  });
});

describe("convertCallToolResult", () => {
  it("converts content and isError", () => {
    const proto = {
      content: [{ type: "text", text: "ok", data: new Uint8Array(), mimeType: "", uri: "", toolUseId: "", toolName: "", toolInput: "", toolResultId: "" }],
      isError: false,
    };
    const result = convertCallToolResult(proto);
    expect(result.content).toHaveLength(1);
    expect(result.content[0].text).toBe("ok");
    expect(result.isError).toBe(false);
  });
});

describe("convertReadResourceResult", () => {
  it("converts content list", () => {
    const proto = {
      content: [{ type: "text", text: "data", data: new Uint8Array(), mimeType: "", uri: "", toolUseId: "", toolName: "", toolInput: "", toolResultId: "" }],
    };
    const result = convertReadResourceResult(proto);
    expect(result.content).toHaveLength(1);
    expect(result.content[0].text).toBe("data");
  });
});

describe("convertGetPromptResult", () => {
  it("converts messages", () => {
    const proto = {
      messages: [{
        role: "user",
        content: { type: "text", text: "hi", data: new Uint8Array(), mimeType: "", uri: "", toolUseId: "", toolName: "", toolInput: "", toolResultId: "" },
      }],
    };
    const result = convertGetPromptResult(proto);
    expect(result.messages).toHaveLength(1);
    expect(result.messages[0].role).toBe("user");
    expect(result.messages[0].content.text).toBe("hi");
  });
});

describe("convertCompleteResult", () => {
  it("converts values, hasMore, total", () => {
    const proto = { values: ["a", "b"], hasMore: true, total: 10 };
    const result = convertCompleteResult(proto);
    expect(result).toEqual({ values: ["a", "b"], hasMore: true, total: 10 });
  });
});
