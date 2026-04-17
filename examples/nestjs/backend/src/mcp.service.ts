import { Injectable, OnModuleInit, OnModuleDestroy, Logger } from "@nestjs/common";
import { RapidMCPClient } from "@emitate/rapidmcp/integrations/langchain";
import type { Client } from "@emitate/rapidmcp";

const MCP_ADDRESS = process.env["MCP_ADDRESS"] ?? "ts-mcp-server:50051";

@Injectable()
export class McpService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(McpService.name);
  private _rc!: RapidMCPClient;

  async onModuleInit(): Promise<void> {
    this._rc = new RapidMCPClient({ default: { address: MCP_ADDRESS } });
    const defaultClient = this._rc.client("default");

    // Register mock handlers BEFORE connect() so capabilities are declared correctly.
    defaultClient.setSamplingHandler(async (req: any) => {
      const texts: string[] = (req.messages ?? [])
        .flatMap((m: any) => (m.content ?? []))
        .filter((c: any) => Boolean(c.text))
        .map((c: any) => c.text as string);
      const input = texts.join(" ").slice(0, 120);
      return {
        role: "assistant",
        content: [{ type: "text", text: `Summary of: ${input}` }],
        model: "mock",
        stopReason: "end_turn",
      } as any;
    });

    defaultClient.setElicitationHandler(async (_req: any) => ({
      action: "accept",
      content: JSON.stringify({ confirm: true }),
    }));

    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        await this._rc.connect();
        this.logger.log(`Connected to MCP server at ${MCP_ADDRESS}`);
        return;
      } catch (err) {
        if (attempt === 3) {
          throw new Error(`Could not connect to MCP server after 3 attempts: ${String(err)}`);
        }
        this.logger.warn(`MCP server not ready (attempt ${attempt}/3), retrying in 2s…`);
        await new Promise<void>((resolve) => setTimeout(resolve, 2000));
      }
    }
  }

  async onModuleDestroy(): Promise<void> {
    await this._rc.close();
    this.logger.log("Disconnected from MCP server");
  }

  get rc(): RapidMCPClient {
    return this._rc;
  }

  get client(): Client {
    return this._rc.client("default");
  }
}
