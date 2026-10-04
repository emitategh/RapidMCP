import type { ZodType } from "zod";

export interface ToolAnnotationsConfig {
  title?: string;
  readOnly?: boolean;
  destructive?: boolean;
  idempotent?: boolean;
  openWorld?: boolean;
}

import type { Icon } from "../icons.js";

export interface ToolConfig<T = any> {
  name: string;
  description?: string;
  parameters?: ZodType<T>;
  /** Shape of the structured result: a zod schema or a plain JSON Schema object. */
  outputSchema?: ZodType | Record<string, unknown>;
  annotations?: ToolAnnotationsConfig;
  /** Icons a client may show for the tool (https: or data: sources). */
  icons?: Icon[];
  execute: (args: T, ctx: any) => Promise<unknown>;
}

export interface RegisteredTool {
  name: string;
  description: string;
  inputSchema: string;
  outputSchema: string;
  handler: (args: any, ctx: any) => Promise<unknown>;
  annotations?: ToolAnnotationsConfig;
  zodSchema?: ZodType;
  icons: Icon[];
}
