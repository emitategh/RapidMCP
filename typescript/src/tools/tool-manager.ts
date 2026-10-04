import { checkedIcons } from "../icons.js";
import type { ToolConfig, RegisteredTool } from "./tool.js";
import { isContentResult, toContentItems } from "../_utils.js";
import { ErrorCode, McpError, ToolError } from "../errors.js";
import type { CallToolResult } from "../middleware.js";
import { toJSONSchema, type ZodType } from "zod";

export type { CallToolResult };

function isZodType(value: ZodType | Record<string, unknown>): value is ZodType {
  return typeof (value as { safeParse?: unknown }).safeParse === "function";
}

export class ToolManager {
  private _tools = new Map<string, RegisteredTool>();
  private _maskErrorDetails: boolean;

  constructor(opts: { maskErrorDetails?: boolean } = {}) {
    this._maskErrorDetails = opts.maskErrorDetails ?? false;
  }

  addTool<T>(config: ToolConfig<T>): void {
    let inputSchema = "{}";
    if (config.parameters) {
      const jsonSchema = toJSONSchema(config.parameters);
      inputSchema = JSON.stringify(jsonSchema);
    }
    let outputSchema = "";
    if (config.outputSchema) {
      const schema = isZodType(config.outputSchema)
        ? toJSONSchema(config.outputSchema)
        : config.outputSchema;
      outputSchema = JSON.stringify(schema);
    }
    this._tools.set(config.name, {
      name: config.name,
      description: config.description ?? "",
      inputSchema,
      outputSchema,
      handler: config.execute,
      annotations: config.annotations,
      zodSchema: config.parameters,
      icons: checkedIcons(config.icons),
    });
  }

  /** Add an already-built registration (used when mounting another server). */
  register(tool: RegisteredTool): void {
    this._tools.set(tool.name, tool);
  }

  listTools(): RegisteredTool[] {
    return [...this._tools.values()];
  }

  getTool(name: string): RegisteredTool | undefined {
    return this._tools.get(name);
  }

  async callTool(name: string, args: Record<string, unknown>, ctx: any): Promise<CallToolResult> {
    const tool = this._tools.get(name);
    if (!tool) {
      throw new McpError(ErrorCode.InvalidParams, `Tool '${name}' not found`);
    }

    let validatedArgs = args;
    if (tool.zodSchema) {
      const result = tool.zodSchema.safeParse(args);
      if (!result.success) {
        return {
          content: [{ type: "text", text: `Validation error: ${result.error.message}`, data: new Uint8Array(), mimeType: "", uri: "" }],
          isError: true,
        };
      }
      validatedArgs = result.data as Record<string, unknown>;
    }

    try {
      const result = await tool.handler(validatedArgs, ctx);
      const isObject = typeof result === "object" && result !== null && !Array.isArray(result);
      return {
        content: toContentItems(result),
        isError: false,
        structuredContent: isObject && !isContentResult(result) ? result : undefined,
      };
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      let message: string;
      if (err instanceof ToolError) {
        // Thrown on purpose by the handler — the message is meant for the caller.
        message = detail;
      } else if (err instanceof McpError) {
        // A protocol-level failure (e.g. the client lacks a capability the tool
        // needs) is reported as an error, not as tool output.
        throw err;
      } else {
        message = this._maskErrorDetails
          ? `Error calling tool '${name}'`
          : `Error calling tool '${name}': ${detail}`;
      }
      return {
        content: [{ type: "text", text: message, data: new Uint8Array(), mimeType: "", uri: "" }],
        isError: true,
      };
    }
  }
}
