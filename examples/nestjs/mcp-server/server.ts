import {
  RapidMCP,
  TimingMiddleware,
  LoggingMiddleware,
  ToolError,
  Context,
} from "@emitate/rapidmcp";
import { z } from "zod";

const server = new RapidMCP({ name: "ts-mcp-test", version: "0.1.0" });
server.use(new TimingMiddleware());
server.use(new LoggingMiddleware());

// ── Simple tools ─────────────────────────────────────────────────────────────

server.addTool({
  name: "add",
  description: "Add two numbers",
  parameters: z.object({ a: z.number(), b: z.number() }),
  annotations: { readOnly: true },
  execute: async ({ a, b }: { a: number; b: number }) => String(a + b),
});

server.addTool({
  name: "echo",
  description: "Echo text back unchanged",
  parameters: z.object({ text: z.string() }),
  annotations: { readOnly: true },
  execute: async ({ text }: { text: string }) => text,
});

// ── Elicitation ───────────────────────────────────────────────────────────────

server.addTool({
  name: "confirm_action",
  description: "Perform an action after user confirmation",
  parameters: z.object({ action: z.string() }),
  execute: async ({ action }: { action: string }, ctx: Context) => {
    const result = await ctx.elicit(`Please confirm: ${action}`, {
      type: "object",
      properties: {
        confirm: {
          type: "boolean",
          title: "Confirm?",
          description: `Do you want to: ${action}`,
        },
      },
      required: ["confirm"],
    });
    if (result.action === "accept") {
      const data = JSON.parse(result.content) as { confirm?: boolean };
      if (data.confirm) return `Action confirmed and executed: ${action}`;
    }
    return `Action declined: ${action}`;
  },
});

// ── Sampling ──────────────────────────────────────────────────────────────────

server.addTool({
  name: "summarize_with_llm",
  description: "Summarize text using the client LLM via sampling",
  parameters: z.object({ text: z.string() }),
  execute: async ({ text }: { text: string }, ctx: Context) => {
    const response = (await ctx.sample({
      messages: [
        {
          role: "user",
          content: [{ type: "text", text: `Summarize in one sentence: ${text}` }],
        },
      ],
      maxTokens: 200,
    })) as { content?: Array<{ text?: string }> } | null;
    if (response?.content?.[0]?.text) return response.content[0].text;
    return "No summary returned";
  },
});

// ── Progress ──────────────────────────────────────────────────────────────────

server.addTool({
  name: "long_running_task",
  description: "Simulate a long-running task with progress reporting",
  parameters: z.object({ steps: z.number().int() }),
  execute: async ({ steps }: { steps: number }, ctx: Context) => {
    const clamped = Math.min(Math.max(steps, 1), 10);
    for (let i = 1; i <= clamped; i++) {
      ctx.reportProgress(i, clamped);
      await new Promise<void>((resolve) => setTimeout(resolve, 200));
    }
    return `Completed ${clamped} steps`;
  },
});

// ── Logging ───────────────────────────────────────────────────────────────────

server.addTool({
  name: "log_demo",
  description: "Demo of server-to-client logging at all levels",
  parameters: z.object({}),
  execute: async (_args: Record<string, never>, ctx: Context) => {
    ctx.log.debug("debug: low-level detail");
    ctx.log.info("info: normal operation");
    ctx.log.warning("warning: something to watch");
    ctx.log.error("error: something went wrong");
    return "All four log levels emitted";
  },
});

// ── ToolError ─────────────────────────────────────────────────────────────────

server.addTool({
  name: "fail_tool",
  description: "A tool that always fails with a ToolError",
  parameters: z.object({}),
  execute: async () => {
    throw new ToolError("This tool always fails on purpose");
  },
});

// ── Static resources ──────────────────────────────────────────────────────────

server.addResource({
  uri: "res://server-info",
  name: "server-info",
  description: "Server name, version, and current timestamp",
  mimeType: "application/json",
  load: async () => ({
    text: JSON.stringify({
      name: "ts-mcp-test",
      version: "0.1.0",
      timestamp: new Date().toISOString(),
    }),
  }),
});

server.addResource({
  uri: "res://config",
  name: "config",
  description: "Static server configuration",
  mimeType: "application/json",
  load: async () => ({
    text: JSON.stringify({ debug: true, max_retries: 3, timeout_seconds: 30 }),
  }),
});

// ── Resource template ─────────────────────────────────────────────────────────

server.addResourceTemplate({
  uriTemplate: "res://items/{item_id}",
  name: "item",
  description: "Fetch an item by its ID",
  mimeType: "application/json",
  load: async (args: Record<string, string>) => ({
    text: JSON.stringify({
      id: args["item_id"],
      name: `Item ${args["item_id"]}`,
      status: "active",
    }),
  }),
});

// ── Prompt + completion ───────────────────────────────────────────────────────

server.addPrompt({
  name: "greet",
  description: "Generate a greeting in a given style",
  arguments: [
    { name: "name", description: "Name to greet", required: true },
    {
      name: "style",
      description: "Greeting style",
      required: false,
      complete: async (value: string) => {
        const options = ["formal", "casual", "pirate", "shakespearean"];
        return { values: options.filter((o) => o.startsWith(value)) };
      },
    },
  ],
  load: async (args: Record<string, string>) => {
    const { name, style = "formal" } = args;
    const greetings: Record<string, string> = {
      formal: `Dear ${name}, I hope this message finds you well.`,
      casual: `Hey ${name}! What's up?`,
      pirate: `Ahoy, ${name}! Shiver me timbers!`,
      shakespearean: `Hark! ${name}, thou art most welcome.`,
    };
    return greetings[style] ?? `Hello, ${name}!`;
  },
});

// ── Start ─────────────────────────────────────────────────────────────────────

await server.listen({ port: 50051, host: "0.0.0.0" });
console.log("ts-mcp-test server listening on :50051");
