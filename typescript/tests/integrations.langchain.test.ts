import { describe, it, expect, vi } from "vitest";
import { RapidMCPClient } from "../src/integrations/langchain.js";
import type { CallToolResult } from "../src/types.js";
import { convertResult } from "../src/integrations/langchain.js";

describe("langchain convertResult", () => {
  it("preserves image content as image_url block", () => {
    const result: CallToolResult = {
      isError: false,
      content: [
        {
          type: "image",
          data: new Uint8Array([1, 2, 3]),
          mimeType: "image/png",
          text: "",
          uri: "",
        },
      ],
    };

    const [content, artifact] = convertResult(result);
    expect(Array.isArray(content)).toBe(true);
    expect((content as Array<{ type: string }>)[0].type).toBe("image_url");
    expect(artifact).toHaveLength(1);
    expect((artifact as Array<{ type: string }>)[0].type).toBe("image");
  });

  it("collapses a single text block to a plain string", () => {
    const result: CallToolResult = {
      isError: false,
      content: [
        { type: "text", text: "hi", data: new Uint8Array(), mimeType: "", uri: "" },
      ],
    };
    const [content, artifact] = convertResult(result);
    expect(content).toBe("hi");
    expect(artifact).toBeNull();
  });

  it("formats errors as Error: <msg>", () => {
    const result: CallToolResult = {
      isError: true,
      content: [
        { type: "text", text: "boom", data: new Uint8Array(), mimeType: "", uri: "" },
      ],
    };
    const [content, artifact] = convertResult(result);
    expect(content).toBe("Error: boom");
    expect(artifact).toBeNull();
  });
});

describe("RapidMCPClient (TS)", () => {
  it("aggregates tools across servers", async () => {
    const rc = new RapidMCPClient({
      a: { address: "a:1" },
      b: { address: "b:1" },
    });

    const fakeA = {
      connect: vi.fn().mockResolvedValue(undefined),
      close: vi.fn().mockResolvedValue(undefined),
      listTools: vi.fn().mockResolvedValue({
        items: [{ name: "alpha", description: "", inputSchema: { type: "object" } }],
        nextCursor: null,
      }),
    };
    const fakeB = {
      connect: vi.fn().mockResolvedValue(undefined),
      close: vi.fn().mockResolvedValue(undefined),
      listTools: vi.fn().mockResolvedValue({
        items: [{ name: "beta", description: "", inputSchema: { type: "object" } }],
        nextCursor: null,
      }),
    };
    // @ts-expect-error — swap private clients for test
    rc._clients = new Map([
      ["a", fakeA],
      ["b", fakeB],
    ]);

    const tools = await rc.getTools();
    expect(tools.map((t: { name: string }) => t.name).sort()).toEqual(["alpha", "beta"]);
  });

  it("rejects empty config", () => {
    expect(() => new RapidMCPClient({})).toThrow(/at least one server/);
  });

  it("respects allowedTools per server", async () => {
    const rc = new RapidMCPClient({
      a: { address: "a:1", allowedTools: ["keep"] },
    });
    // @ts-expect-error
    rc._clients = new Map([
      [
        "a",
        {
          connect: vi.fn(),
          close: vi.fn(),
          listTools: vi.fn().mockResolvedValue({
            items: [
              { name: "keep", description: "", inputSchema: { type: "object" } },
              { name: "drop", description: "", inputSchema: { type: "object" } },
            ],
            nextCursor: null,
          }),
        },
      ],
    ]);

    const tools = await rc.getTools();
    expect(tools.map((t: { name: string }) => t.name)).toEqual(["keep"]);
  });
});

describe("RapidMCPClient.getResources", () => {
  it("reads supplied URIs and returns blob-like objects with metadata", async () => {
    const rc = new RapidMCPClient({ a: { address: "a:1" } });
    const fake = {
      readResource: vi.fn().mockImplementation(async (uri: string) => {
        if (uri === "file:///t.txt") {
          return {
            content: [
              { type: "text", text: "hi", mimeType: "text/plain",
                data: new Uint8Array(), uri: "" },
            ],
          };
        }
        return {
          content: [
            { type: "resource", text: "", mimeType: "application/octet-stream",
              data: new Uint8Array([9, 9]), uri },
          ],
        };
      }),
    };
    // @ts-expect-error
    rc._clients = new Map([["a", fake]]);

    const blobs = await rc.getResources("a", { uris: ["file:///t.txt", "file:///bin"] });
    expect(blobs).toHaveLength(2);
    expect(blobs[0].mimeType).toBe("text/plain");
    expect(blobs[0].asString()).toBe("hi");
    expect(blobs[0].metadata).toEqual({ uri: "file:///t.txt" });
    expect(blobs[1].asBytes()).toEqual(new Uint8Array([9, 9]));
  });
});

describe("RapidMCPClient.getPrompt", () => {
  it("returns role-tagged messages", async () => {
    const rc = new RapidMCPClient({ a: { address: "a:1" } });
    const fake = {
      getPrompt: vi.fn().mockResolvedValue({
        messages: [
          { role: "user", content: { type: "text", text: "hi",
            mimeType: "", data: new Uint8Array(), uri: "" } },
          { role: "assistant", content: { type: "text", text: "hello",
            mimeType: "", data: new Uint8Array(), uri: "" } },
        ],
      }),
    };
    // @ts-expect-error
    rc._clients = new Map([["a", fake]]);

    const msgs = await rc.getPrompt("a", "greet", { name: "Ada" });
    expect(msgs).toEqual([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]);
    expect(fake.getPrompt).toHaveBeenCalledWith("greet", { name: "Ada" });
  });
});
