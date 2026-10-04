import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function until(condition: () => boolean, timeout = 3000): Promise<void> {
  const deadline = Date.now() + timeout;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error("condition never became true");
    await sleep(20);
  }
}

describe("notifications behave the same on both protocol versions", () => {
  let server: RapidMCP;
  let port: number;
  let client: Client;

  beforeEach(async () => {
    server = new RapidMCP({ name: "listen" });
    port = await server.listen();
  });

  afterEach(async () => {
    await client.close();
    await server.close();
  });

  const listeners = () => (server as any)._listeners.size as number;

  it.each(["legacy", "modern"] as const)("delivers list-changed notifications (%s)", async (mode) => {
    client = new Client(`127.0.0.1:${port}`, { mode });
    const seen: Array<[string, string]> = [];
    for (const kind of ["tools_list_changed", "prompts_list_changed", "resources_list_changed"]) {
      client.onNotification(kind, (payload) => void seen.push([kind, payload]));
    }
    await client.connect();
    await client.ping(); // on v1 this proves the session is registered for broadcasts

    server.notifyToolsListChanged();
    server.notifyPromptsListChanged();
    server.notifyResourcesListChanged();
    await until(() => seen.length === 3);

    expect(seen).toEqual([
      ["tools_list_changed", ""],
      ["prompts_list_changed", ""],
      ["resources_list_changed", ""],
    ]);
  });

  it.each(["legacy", "modern"] as const)("delivers updates for a subscribed resource (%s)", async (mode) => {
    client = new Client(`127.0.0.1:${port}`, { mode });
    const updates: unknown[] = [];
    client.onNotification("resource_updated", (payload) => void updates.push(JSON.parse(payload)));
    await client.connect();

    await client.subscribeResource("res://mine");
    await client.ping();
    server.notifyResourceUpdated("res://mine");
    await until(() => updates.length === 1);

    expect(updates).toEqual([{ uri: "res://mine" }]);
  });

  it("hears only about uris it subscribed to on v2", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const updates: string[] = [];
    client.onNotification("resource_updated", (payload) => void updates.push(JSON.parse(payload).uri));
    await client.connect();
    await client.subscribeResource("res://mine");

    server.notifyResourceUpdated("res://other");
    server.notifyResourceUpdated("res://mine");
    await until(() => updates.length === 1);

    expect(updates).toEqual(["res://mine"]);
  });

  it("opens no stream when it has no notification handlers", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    await client.connect();
    await client.ping();

    expect(listeners()).toBe(0);
  });

  it("starts receiving when a handler is registered after connecting", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    const seen: string[] = [];
    await client.connect();

    client.onNotification("tools_list_changed", () => void seen.push("tools"));
    await until(() => listeners() === 1);
    server.notifyToolsListChanged();

    await until(() => seen.length === 1);
  });

  it("ends its subscription when closed", async () => {
    client = new Client(`127.0.0.1:${port}`, { mode: "modern" });
    client.onNotification("tools_list_changed", () => {});
    await client.connect();
    expect(listeners()).toBe(1);

    await client.close();

    await until(() => listeners() === 0);
  });
});
