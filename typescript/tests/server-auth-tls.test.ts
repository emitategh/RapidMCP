import { describe, it, expect, afterEach } from "vitest";
import { fileURLToPath } from "node:url";
import { RapidMCP } from "../src/server.js";
import { Client } from "../src/client.js";

// Self-signed certificate for localhost / 127.0.0.1, generated for these tests only.
const CERT = fileURLToPath(new URL("./fixtures/test-server.crt", import.meta.url));
const KEY = fileURLToPath(new URL("./fixtures/test-server.key", import.meta.url));

describe("server authentication and TLS", () => {
  let server: RapidMCP;
  let client: Client | null = null;

  afterEach(async () => {
    await client?.close();
    client = null;
    await server.close();
  });

  it("accepts a client presenting a token the verifier approves", async () => {
    const seen: string[] = [];
    server = new RapidMCP({
      name: "auth",
      auth: (token) => {
        seen.push(token);
        return token === "s3cret";
      },
    });
    const port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { token: "s3cret", requestTimeout: 3000 });

    await client.connect();

    expect(await client.ping()).toBe(true);
    expect(seen).toEqual(["s3cret"]); // the "Bearer " prefix is stripped
  });

  it("rejects a client with the wrong token", async () => {
    server = new RapidMCP({ name: "auth", auth: (token) => token === "s3cret" });
    const port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { token: "nope", requestTimeout: 3000 });

    await expect(client.connect()).rejects.toThrow(/UNAUTHENTICATED/);
  });

  it("rejects a client with no token", async () => {
    server = new RapidMCP({ name: "auth", auth: async (token) => token === "s3cret" });
    const port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { requestTimeout: 3000 });

    await expect(client.connect()).rejects.toThrow(/UNAUTHENTICATED/);
  });

  it("rejects the client when the verifier throws", async () => {
    server = new RapidMCP({
      name: "auth",
      auth: () => {
        throw new Error("verifier bug");
      },
    });
    const port = await server.listen();
    client = new Client(`127.0.0.1:${port}`, { token: "s3cret", requestTimeout: 3000 });

    await expect(client.connect()).rejects.toThrow(/UNAUTHENTICATED/);
  });

  it("serves over TLS to a client that trusts the certificate", async () => {
    server = new RapidMCP({ name: "tls", tls: { cert: CERT, key: KEY } });
    server.addTool({ name: "echo", execute: async () => "over tls" });
    const port = await server.listen();
    client = new Client(`localhost:${port}`, { tls: { rootCert: CERT }, requestTimeout: 3000 });

    await client.connect();

    expect((await client.callTool("echo")).content[0].text).toBe("over tls");
  });

  it("does not talk plaintext on a TLS port", async () => {
    server = new RapidMCP({ name: "tls", tls: { cert: CERT, key: KEY } });
    const port = await server.listen();
    client = new Client(`localhost:${port}`, { requestTimeout: 1500 });

    await expect(client.connect()).rejects.toThrow();
  });
});
