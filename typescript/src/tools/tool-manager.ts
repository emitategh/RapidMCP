import type { ToolConfig, RegisteredTool } from "./tool.js";
import { toContentItems } from "../_utils.js";
import { McpError } from "../errors.js";
import type { CallToolResult } from "../middleware.js";
import { toJSONSchema, type ZodType } from "zod";

export type { CallToolResult };

function isZodType(value: ZodType | Record<string, unknown>): value is ZodType {
  return typeof (value as { safeParse?: unknown }).safeParse === "function";
}

export class ToolManager {
  private _tools = new Map<string, RegisteredTool>();

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
    });
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
      throw new McpError(404, `Tool '${name}' not found`);
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
      return { content: toContentItems(result), isError: false };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return {
        content: [{ type: "text", text: message, data: new Uint8Array(), mimeType: "", uri: "" }],
        isError: true,
      };
    }
  }
}
