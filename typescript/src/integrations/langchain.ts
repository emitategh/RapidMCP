/**
 * LangChain integration — RapidMCPClient for RapidMCP gRPC servers.
 *
 * Wraps one or more rapidmcp Client instances and exposes MCP tools as
 * LangChain DynamicStructuredTool instances for use with createReactAgent.
 *
 * Requires @langchain/core as a peer dependency:
 *   npm install @langchain/core
 *
 * Usage:
 *   import { RapidMCPClient } from "rapidmcp/integrations/langchain";
 *   const rc = new RapidMCPClient({ myServer: { address: "mcp-server:50051" } });
 *   await rc.connect();
 *   const tools = await rc.getTools();
 *   await rc.close();
 */

import { Client } from "../client.js";
import type { CallToolResult } from "../types.js";
import type { ClientOptions } from "../auth.js";
import { z } from "zod";

// ---------------------------------------------------------------------------
// JSON Schema → Zod shape (top-level properties only, covers all real MCP tools)
// ---------------------------------------------------------------------------

function jsonSchemaToZod(schema: Record<string, unknown>): z.ZodObject<z.ZodRawShape> {
  const props = (schema["properties"] ?? {}) as Record<string, Record<string, unknown>>;
  const required = new Set((schema["required"] ?? []) as string[]);
  const shape: Record<string, z.ZodTypeAny> = {};

  for (const [key, prop] of Object.entries(props)) {
    let zodType: z.ZodTypeAny;
    switch (prop["type"]) {
      case "string":
        zodType = z.string();
        break;
      case "integer":
        zodType = z.number().int();
        break;
      case "number":
        zodType = z.number();
        break;
      case "boolean":
        zodType = z.boolean();
        break;
      case "array":
        zodType = z.array(z.unknown());
        break;
      case "object":
        zodType = z.record(z.string(), z.unknown());
        break;
      default:
        zodType = z.unknown();
    }
    if (prop["description"]) {
      zodType = zodType.describe(prop["description"] as string);
    }
    shape[key] = required.has(key) ? zodType : zodType.optional();
  }

  return z.object(shape);
}

// ---------------------------------------------------------------------------
// CallToolResult → [content, artifact] for LangChain ToolMessage.content
// ---------------------------------------------------------------------------

// What goes into ToolMessage.content — shown to the LLM.
export type ContentBlock = { type: string; [k: string]: unknown };
export type ToolContent = string | ContentBlock[];
export type ToolArtifact =
  | Array<{ type: "image" | "audio"; mime_type: string; data: string }>
  | null;

/**
 * Convert a CallToolResult to a [content, artifact] tuple matching
 * LangChain's `response_format: "content_and_artifact"` convention.
 *
 * Errors become `Error: <msg>` (never thrown — let the LLM see the failure).
 * Single-text results collapse to a plain string to avoid unnecessary
 * multi-modal wrapping.
 */
export function convertResult(result: CallToolResult): [ToolContent, ToolArtifact] {
  if (result.isError) {
    const text = result.content
      .map((c) => c.text)
      .filter(Boolean)
      .join(" ");
    return [`Error: ${text || "Tool returned an error with no message"}`, null];
  }
  if (result.content.length === 0) return ["", null];

  const blocks: ContentBlock[] = [];
  const artifacts: NonNullable<ToolArtifact> = [];

  const toBase64 = (bytes: Uint8Array): string =>
    Buffer.from(bytes).toString("base64");

  for (const c of result.content) {
    if (c.type === "text") {
      blocks.push({ type: "text", text: c.text });
    } else if (c.type === "image" && c.data && c.data.length > 0) {
      const b64 = toBase64(c.data);
      blocks.push({
        type: "image_url",
        image_url: { url: `data:${c.mimeType};base64,${b64}` },
      });
      artifacts.push({ type: "image", mime_type: c.mimeType, data: b64 });
    } else if (c.type === "audio" && c.data && c.data.length > 0) {
      const b64 = toBase64(c.data);
      blocks.push({
        type: "text",
        text: `[audio: ${c.mimeType}, ${c.data.length} bytes]`,
      });
      artifacts.push({ type: "audio", mime_type: c.mimeType, data: b64 });
    } else if (c.type === "resource") {
      blocks.push({ type: "text", text: `[resource: ${c.uri}]` });
    }
  }

  const artifact: ToolArtifact = artifacts.length > 0 ? artifacts : null;

  if (blocks.length === 1 && blocks[0].type === "text") {
    return [blocks[0].text as string, artifact];
  }
  return [blocks, artifact];
}

// ---------------------------------------------------------------------------
// RapidMCPClient — multi-server LangChain adapter
// ---------------------------------------------------------------------------

export interface ServerConfig extends ClientOptions {
  address: string;
  allowedTools?: readonly string[];
}

export class RapidMCPClient {
  private _clients: Map<string, Client> = new Map();
  private _allowed: Map<string, Set<string> | null> = new Map();

  /**
   * @param servers Map of server name → config. Each server opens its own
   *                gRPC stream. `allowedTools` filters per server.
   */
  constructor(servers: Record<string, ServerConfig>) {
    const names = Object.keys(servers);
    if (names.length === 0) {
      throw new Error("RapidMCPClient requires at least one server config");
    }
    for (const name of names) {
      const { address, allowedTools, ...rest } = servers[name];
      this._clients.set(name, new Client(address, rest));
      this._allowed.set(name, allowedTools ? new Set(allowedTools) : null);
    }
  }

  get servers(): string[] {
    return [...this._clients.keys()];
  }

  client(serverName: string): Client {
    const c = this._clients.get(serverName);
    if (!c)
      throw new Error(
        `Unknown server ${JSON.stringify(serverName)}. Configured: ${this.servers.join(", ")}`,
      );
    return c;
  }

  async connect(): Promise<void> {
    await Promise.all([...this._clients.values()].map((c) => c.connect()));
  }

  async close(): Promise<void> {
    await Promise.all([...this._clients.values()].map((c) => c.close()));
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async getTools(opts: { serverName?: string } = {}): Promise<any[]> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let DSTool: any;
    try {
      ({ DynamicStructuredTool: DSTool } = await import("@langchain/core/tools"));
    } catch {
      throw new Error(
        "@langchain/core is required for RapidMCPClient.getTools().\n" +
          "Install it with: npm install @langchain/core",
      );
    }

    const names = opts.serverName ? [opts.serverName] : this.servers;
    for (const n of names) {
      if (!this._clients.has(n)) throw new Error(`Unknown server ${JSON.stringify(n)}`);
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const all: any[] = [];
    for (const name of names) {
      const client = this._clients.get(name)!;
      const allowed = this._allowed.get(name) ?? null;
      let cursor: string | undefined;
      while (true) {
        const result = await client.listTools(cursor);
        for (const mcpTool of result.items) {
          if (allowed && !allowed.has(mcpTool.name)) continue;
          const schema = jsonSchemaToZod(mcpTool.inputSchema);
          const toolName = mcpTool.name;
          all.push(
            new DSTool({
              name: toolName,
              description: mcpTool.description ?? "",
              schema,
              func: async (args: Record<string, unknown>) => {
                const callResult = await client.callTool(toolName, args);
                const [content] = convertResult(callResult);
                return content;
              },
            }),
          );
        }
        if (!result.nextCursor) break;
        cursor = result.nextCursor;
      }
    }
    return all;
  }

  async getResources(
    serverName: string,
    opts: { uris?: string[] } = {},
  ): Promise<Array<{
    data: Uint8Array | string;
    mimeType: string;
    metadata: { uri: string };
    asString(): string;
    asBytes(): Uint8Array;
  }>> {
    const client = this.client(serverName);

    let uris: string[];
    if (opts.uris) {
      uris = opts.uris;
    } else {
      uris = [];
      let cursor: string | undefined;
      while (true) {
        const listing = await client.listResources(cursor);
        uris.push(...listing.items.map((r) => r.uri));
        if (!listing.nextCursor) break;
        cursor = listing.nextCursor;
      }
    }

    const reads = await Promise.all(uris.map((u) => client.readResource(u)));
    return reads.map((result, i) => {
      const textParts: string[] = [];
      let binary: Uint8Array | null = null;
      let mime = "";
      for (const c of result.content) {
        if (c.mimeType && !mime) mime = c.mimeType;
        if (c.type === "text") {
          textParts.push(c.text);
        } else if (c.data && c.data.length > 0) {
          binary = binary
            ? new Uint8Array([...binary, ...c.data])
            : new Uint8Array(c.data);
        }
      }
      const uri = uris[i];
      if (binary) {
        return {
          data: binary,
          mimeType: mime || "application/octet-stream",
          metadata: { uri },
          asString: () => new TextDecoder().decode(binary!),
          asBytes: () => binary!,
        };
      }
      const text = textParts.join("");
      return {
        data: text,
        mimeType: mime || "text/plain",
        metadata: { uri },
        asString: () => text,
        asBytes: () => new TextEncoder().encode(text),
      };
    });
  }

  async getPrompt(
    serverName: string,
    promptName: string,
    args: Record<string, string> = {},
  ): Promise<Array<{ role: string; content: string }>> {
    const result = await this.client(serverName).getPrompt(promptName, args);
    return result.messages.map((pm) => {
      const c = pm.content;
      let body: string;
      if (c.type === "text") body = c.text;
      else if (c.type === "image") body = `[image: ${c.mimeType}, ${c.data.length} bytes]`;
      else if (c.type === "audio") body = `[audio: ${c.mimeType}, ${c.data.length} bytes]`;
      else body = `[resource: ${c.uri}]`;
      return { role: pm.role, content: body };
    });
  }
}
