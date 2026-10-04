/** Server for the cross-language tests. Prints `PORT <n>` once it is listening. */
import { RapidMCP } from "../../src/server.js";

const server = new RapidMCP({ name: "interop-typescript", version: "1.0", stateSecret: "interop" });

server.addTool({ name: "echo", execute: async (args: any) => args.text });
server.addTool({ name: "add", execute: async (args: any) => ({ sum: args.a + args.b }) });
server.addTool({
  name: "chatty",
  execute: async (_args: unknown, ctx: any) => {
    ctx.log.info("working");
    ctx.reportProgress(1, 2);
    return "done";
  },
});
server.addTool({
  name: "ask",
  execute: async (_args: unknown, ctx: any) => {
    const answer = await ctx.elicit("Confirm?", {
      type: "object",
      properties: { confirm: { type: "boolean" } },
    });
    return answer.action === "accept" ? "confirmed" : "declined";
  },
});
server.addTool({
  name: "poke",
  execute: async () => {
    server.notifyToolsListChanged();
    return "poked";
  },
});
server.addResource({ uri: "res://greeting", name: "greeting", load: async () => ({ text: "hello" }) });
server.addResourceTemplate({
  uriTemplate: "res://items/{id}",
  name: "item",
  load: async (args) => ({ text: `item ${args.id}` }),
});
server.addPrompt({
  name: "greet",
  arguments: [{ name: "who", required: true }],
  load: async (args) => `hi ${args.who}`,
});

const port = await server.listen();
console.log(`PORT ${port}`);
