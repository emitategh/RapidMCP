/**
 * TestServer — minimal nice-grpc MCP server for integration tests.
 */
import { createServer } from "nice-grpc";
import type { CallContext } from "nice-grpc-common";
import {
  McpDefinition,
  type McpServiceImplementation,
  type ClientEnvelope,
  type ServerEnvelope,
  type DeepPartial,
  type ToolDefinition,
  type ResourceDefinition,
  type ResourceTemplateDefinition,
  type PromptDefinition,
} from "../generated/mcp.js";

export interface TestHandler {
  tools?: ToolDefinition[];
  resources?: ResourceDefinition[];
  resourceTemplates?: ResourceTemplateDefinition[];
  prompts?: PromptDefinition[];
}

export class TestServer {
  private _server = createServer();
  private _port = 0;

  constructor(private handler: TestHandler = {}) {
    const self = this;
    const impl: McpServiceImplementation = {
      async *session(
        request: AsyncIterable<ClientEnvelope>,
        _context: CallContext,
      ): AsyncGenerator<DeepPartial<ServerEnvelope>> {
        for await (const envelope of request) {
          const msg = envelope.message;
          if (!msg) continue;
          const rid = envelope.requestId;

          switch (msg.$case) {
            case "initialize":
              yield {
                requestId: rid,
                message: {
                  $case: "initialize",
                  initialize: {
                    serverName: "test-server",
                    serverVersion: "0.1.0",
                    capabilities: {
                      tools: true,
                      toolsListChanged: false,
                      resources: true,
                      prompts: true,
                    },
                  },
                },
              };
              break;

            case "initialized":
              // no-op
              break;

            case "listTools":
              yield {
                requestId: rid,
                message: {
                  $case: "listTools",
                  listTools: {
                    tools: self.handler.tools ?? [],
                    nextCursor: "",
                  },
                },
              };
              break;

            case "callTool": {
              const name = msg.callTool.name;
              yield {
                requestId: rid,
                message: {
                  $case: "callTool",
                  callTool: {
                    content: [
                      {
                        type: "text",
                        text: `called ${name}`,
                        data: new Uint8Array(),
                        mimeType: "",
                        uri: "",
                        toolUseId: "",
                        toolName: "",
                        toolInput: "",
                        toolResultId: "",
                      },
                    ],
                    isError: false,
                  },
                },
              };
              break;
            }

            case "listResources":
              yield {
                requestId: rid,
                message: {
                  $case: "listResources",
                  listResources: {
                    resources: self.handler.resources ?? [],
                    nextCursor: "",
                  },
                },
              };
              break;

            case "readResource": {
              const uri = msg.readResource.uri;
              yield {
                requestId: rid,
                message: {
                  $case: "readResource",
                  readResource: {
                    content: [
                      {
                        type: "text",
                        text: `content of ${uri}`,
                        data: new Uint8Array(),
                        mimeType: "text/plain",
                        uri,
                        toolUseId: "",
                        toolName: "",
                        toolInput: "",
                        toolResultId: "",
                      },
                    ],
                  },
                },
              };
              break;
            }

            case "listResourceTemplates":
              yield {
                requestId: rid,
                message: {
                  $case: "listResourceTemplates",
                  listResourceTemplates: {
                    templates: self.handler.resourceTemplates ?? [],
                    nextCursor: "",
                  },
                },
              };
              break;

            case "listPrompts":
              yield {
                requestId: rid,
                message: {
                  $case: "listPrompts",
                  listPrompts: {
                    prompts: self.handler.prompts ?? [],
                    nextCursor: "",
                  },
                },
              };
              break;

            case "getPrompt":
              yield {
                requestId: rid,
                message: {
                  $case: "getPrompt",
                  getPrompt: {
                    messages: [
                      {
                        role: "assistant",
                        content: {
                          type: "text",
                          text: `prompt ${msg.getPrompt.name}`,
                          data: new Uint8Array(),
                          mimeType: "",
                          uri: "",
                          toolUseId: "",
                          toolName: "",
                          toolInput: "",
                          toolResultId: "",
                        },
                      },
                    ],
                  },
                },
              };
              break;

            case "complete":
              yield {
                requestId: rid,
                message: {
                  $case: "complete",
                  complete: {
                    values: ["completion1"],
                    hasMore: false,
                    total: 1,
                  },
                },
              };
              break;

            case "ping":
              yield {
                requestId: rid,
                message: { $case: "pong", pong: {} },
              };
              break;

            case "cancel":
              // no-op
              break;

            case "subscribeRes":
              // no-op — just acknowledge
              break;

            case "clientNotification":
              // no-op
              break;

            default:
              break;
          }
        }
      },
    };

    this._server.add(McpDefinition, impl);
  }

  get address(): string {
    return `127.0.0.1:${this._port}`;
  }

  async start(): Promise<void> {
    this._port = await this._server.listen("127.0.0.1:0");
  }

  async stop(): Promise<void> {
    this._server.forceShutdown();
  }
}
