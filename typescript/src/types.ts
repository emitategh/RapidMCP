/* Domain types returned by the Client public API. */

// ── Content ──────────────────────────────────────────────

export interface ContentItem {
  type: string;
  text: string;
  data: Uint8Array;
  mimeType: string;
  uri: string;
}

// ── Tool ─────────────────────────────────────────────────

export interface ToolAnnotationInfo {
  title: string;
  readOnlyHint: boolean;
  destructiveHint: boolean;
  idempotentHint: boolean;
  openWorldHint: boolean;
}

export interface Tool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  outputSchema: Record<string, unknown> | null;
  annotations: ToolAnnotationInfo;
}

export interface CallToolResult {
  content: ContentItem[];
  isError: boolean;
}

// ── Resource ─────────────────────────────────────────────

export interface Resource {
  uri: string;
  name: string;
  description: string;
  mimeType: string;
}

export interface ResourceTemplate {
  uriTemplate: string;
  name: string;
  description: string;
  mimeType: string;
}

export interface ReadResourceResult {
  content: ContentItem[];
}

// ── Prompt ───────────────────────────────────────────────

export interface PromptArgument {
  name: string;
  description: string;
  required: boolean;
}

export interface Prompt {
  name: string;
  description: string;
  arguments: PromptArgument[];
}

export interface PromptMessage {
  role: string;
  content: ContentItem;
}

export interface GetPromptResult {
  messages: PromptMessage[];
}

// ── Completion ───────────────────────────────────────────

export interface CompleteResult {
  values: string[];
  hasMore: boolean;
  total: number;
}

// ── Pagination ───────────────────────────────────────────

export interface ListResult<T> {
  items: T[];
  nextCursor: string | null;
}

// ── Server info ──────────────────────────────────────────

export interface ServerCapabilities {
  tools: boolean;
  toolsListChanged: boolean;
  resources: boolean;
  prompts: boolean;
}

export interface ServerInfo {
  serverName: string;
  serverVersion: string;
  capabilities: ServerCapabilities;
}

// ── Proto → domain converters ────────────────────────────

export function convertContentItem(p: {
  type: string;
  text: string;
  data: Uint8Array;
  mimeType: string;
  uri: string;
}): ContentItem {
  return {
    type: p.type,
    text: p.text,
    data: p.data,
    mimeType: p.mimeType,
    uri: p.uri,
  };
}

export function convertTool(p: {
  name: string;
  description: string;
  inputSchema: string;
  outputSchema: string;
  annotations: {
    title: string;
    readOnlyHint: boolean;
    destructiveHint: boolean;
    idempotentHint: boolean;
    openWorldHint: boolean;
  } | undefined;
}): Tool {
  const inputSchema: Record<string, unknown> = p.inputSchema ? JSON.parse(p.inputSchema) : {};
  const outputSchema: Record<string, unknown> | null = p.outputSchema
    ? JSON.parse(p.outputSchema)
    : null;
  const a = p.annotations;
  return {
    name: p.name,
    description: p.description,
    inputSchema,
    outputSchema,
    annotations: {
      title: a?.title ?? "",
      readOnlyHint: a?.readOnlyHint ?? false,
      destructiveHint: a?.destructiveHint ?? false,
      idempotentHint: a?.idempotentHint ?? false,
      openWorldHint: a?.openWorldHint ?? false,
    },
  };
}

export function convertResource(p: {
  uri: string;
  name: string;
  description: string;
  mimeType: string;
}): Resource {
  return { uri: p.uri, name: p.name, description: p.description, mimeType: p.mimeType };
}

export function convertResourceTemplate(p: {
  uriTemplate: string;
  name: string;
  description: string;
  mimeType: string;
}): ResourceTemplate {
  return {
    uriTemplate: p.uriTemplate,
    name: p.name,
    description: p.description,
    mimeType: p.mimeType,
  };
}

export function convertPrompt(p: {
  name: string;
  description: string;
  arguments: { name: string; description: string; required: boolean }[];
}): Prompt {
  return {
    name: p.name,
    description: p.description,
    arguments: p.arguments.map((a) => ({
      name: a.name,
      description: a.description,
      required: a.required,
    })),
  };
}

export function convertCallToolResult(p: {
  content: {
    type: string;
    text: string;
    data: Uint8Array;
    mimeType: string;
    uri: string;
  }[];
  isError: boolean;
}): CallToolResult {
  return {
    content: p.content.map(convertContentItem),
    isError: p.isError,
  };
}

export function convertReadResourceResult(p: {
  content: {
    type: string;
    text: string;
    data: Uint8Array;
    mimeType: string;
    uri: string;
  }[];
}): ReadResourceResult {
  return { content: p.content.map(convertContentItem) };
}

export function convertGetPromptResult(p: {
  messages: {
    role: string;
    content: {
      type: string;
      text: string;
      data: Uint8Array;
      mimeType: string;
      uri: string;
    };
  }[];
}): GetPromptResult {
  return {
    messages: p.messages.map((m) => ({
      role: m.role,
      content: convertContentItem(m.content),
    })),
  };
}

export function convertCompleteResult(p: {
  values: string[];
  hasMore: boolean;
  total: number;
}): CompleteResult {
  return { values: [...p.values], hasMore: p.hasMore, total: p.total };
}
