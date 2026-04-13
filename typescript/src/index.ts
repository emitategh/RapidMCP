export { Client } from "./client.js";
export { McpError, ToolError } from "./errors.js";
export {
  type ContentItem,
  type Tool,
  type ToolAnnotationInfo,
  type CallToolResult,
  type Resource,
  type ResourceTemplate,
  type ReadResourceResult,
  type PromptArgument,
  type Prompt,
  type PromptMessage,
  type GetPromptResult,
  type CompleteResult,
  type ListResult,
  type ServerInfo,
  type ServerCapabilities,
} from "./types.js";
export { type ClientOptions, type TlsConfig } from "./auth.js";

// Server exports
export { RapidMCP, type RapidMCPOptions, type ListenOptions } from "./server.js";
export { Context } from "./context.js";
export {
  Middleware,
  TimingMiddleware,
  LoggingMiddleware,
  TimeoutMiddleware,
  ValidationMiddleware,
  type ToolCallContext,
  type CallToolResult as MiddlewareCallToolResult,
} from "./middleware.js";
export { type ToolConfig, type ToolAnnotationsConfig } from "./tools/tool.js";
export { type ResourceConfig, type ResourceTemplateConfig } from "./resources/resource.js";
export { type PromptConfig, type PromptArgumentConfig } from "./prompts/prompt.js";
