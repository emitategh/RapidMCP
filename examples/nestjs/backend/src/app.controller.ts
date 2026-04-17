import {
  Controller,
  Get,
  Post,
  Body,
  Param,
  Req,
  HttpException,
  HttpStatus,
} from "@nestjs/common";
import type { Request } from "express";
import { McpService } from "./mcp.service.js";

@Controller()
export class AppController {
  constructor(private readonly mcpService: McpService) {}

  @Get("health")
  async health(): Promise<{ status: string }> {
    await this.mcpService.client.ping();
    return { status: "ok" };
  }

  @Get("tools")
  async listTools(): Promise<unknown> {
    const result = await this.mcpService.client.listTools();
    return {
      tools: result.items.map((t) => ({
        name: t.name,
        description: t.description,
        input_schema: t.inputSchema,
      })),
    };
  }

  @Post("chat")
  async chat(@Body() body: { message: string }): Promise<{ response: string }> {
    const { ChatAnthropic } = await import("@langchain/anthropic");
    const { createReactAgent } = await import("@langchain/langgraph/prebuilt");

    const llm = new ChatAnthropic({ model: "claude-sonnet-4-6", maxTokens: 1024, temperature: 1 });
    const tools = await this.mcpService.rc.getTools();
    const agent = createReactAgent({ llm, tools: tools as any });
    const result = await agent.invoke({
      messages: [{ role: "user", content: body.message }],
    });
    const last = result.messages[result.messages.length - 1];
    const content =
      typeof last.content === "string" ? last.content : JSON.stringify(last.content);
    return { response: content };
  }

  @Get("resources")
  async listResources(): Promise<unknown> {
    const resources = await this.mcpService.client.listResources();
    const templates = await this.mcpService.client.listResourceTemplates();
    return {
      resources: resources.items.map((r) => ({
        uri: r.uri,
        name: r.name,
        description: r.description,
      })),
      templates: templates.items.map((t) => ({
        uri_template: t.uriTemplate,
        name: t.name,
        description: t.description,
      })),
    };
  }

  @Get("resources/*")
  async readResource(@Param("0") uri: string): Promise<unknown> {
    try {
      const result = await this.mcpService.client.readResource(uri);
      return {
        uri,
        content: result.content.map((c) => ({
          type: c.type,
          text: c.text,
          mime_type: c.mimeType,
        })),
      };
    } catch (err) {
      throw new HttpException(String(err), HttpStatus.NOT_FOUND);
    }
  }

  @Get("prompts")
  async listPrompts(): Promise<unknown> {
    const result = await this.mcpService.client.listPrompts();
    return {
      prompts: result.items.map((p) => ({
        name: p.name,
        description: p.description,
        arguments: p.arguments.map((a) => ({
          name: a.name,
          description: a.description,
          required: a.required,
        })),
      })),
    };
  }

  @Get("prompts/:name")
  async getPrompt(@Param("name") name: string, @Req() req: Request): Promise<unknown> {
    const args = req.query as Record<string, string>;
    try {
      const result = await this.mcpService.client.getPrompt(name, args);
      return {
        name,
        messages: result.messages.map((m) => ({ role: m.role, text: m.content.text })),
      };
    } catch (err) {
      throw new HttpException(String(err), HttpStatus.NOT_FOUND);
    }
  }
}
