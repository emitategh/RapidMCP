import { describe, it, expect } from "vitest";
import { McpError } from "../src/errors.js";
import { operationDigest, principalDigest, seal, unseal } from "../src/v2/state.js";

const SECRET = new TextEncoder().encode("shared-secret");
const OP = operationDigest("tools/call", "deploy", '{"service": "api"}');
const ANSWERS = { "elicit-0": { action: "accept", content: '{"confirm": true}' } };

// The same bytes as python/tests/test_v2_state.py: this is what keeps the two
// implementations able to read each other's state.
const VECTOR = Buffer.from(
  "c16779a64fde2278de3d869db2efc16ee1cdda58fab040d26f7d9e16552ae276" +
    "7b2276223a312c22657870223a343130323434343830302c226f70223a2234616632363130643935373039" +
    "623365303133343562616461656538323633363032626465306431623332373337303638633138656561" +
    "313936646362613831222c22737562223a22222c22616e7377657273223a7b22656c696369742d30223a" +
    "7b22616374696f6e223a22616363657074222c22636f6e74656e74223a227b5c22636f6e6669726d5c22" +
    "3a20747275657d227d7d7d",
  "hex",
);

function rejected(fn: () => unknown): string {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(McpError);
    expect((err as McpError).code).toBe(-32602);
    return (err as McpError).message;
  }
  throw new Error("expected the state to be rejected");
}

describe("request_state", () => {
  it("computes the documented operation digest", () => {
    expect(OP).toBe("4af2610d95709b3e01345badaee8263602bde0d1b32737068c18eea196dcba81");
  });

  it("opens the vector shared with the Python implementation", () => {
    expect(unseal(SECRET, VECTOR, OP, "")).toEqual(ANSWERS);
  });

  it("round-trips sealed state", () => {
    const who = principalDigest("Bearer abc");

    expect(unseal(SECRET, seal(SECRET, ANSWERS, OP, who), OP, who)).toEqual(ANSWERS);
  });

  it("rejects altered state", () => {
    const state = seal(SECRET, ANSWERS, OP, "");
    state[state.length - 5] ^= 1;

    expect(rejected(() => unseal(SECRET, state, OP, ""))).toContain("altered");
  });

  it("rejects state signed with another secret", () => {
    const state = seal(new TextEncoder().encode("other-secret"), ANSWERS, OP, "");

    expect(rejected(() => unseal(SECRET, state, OP, ""))).toContain("altered");
  });

  it("rejects expired state", () => {
    const state = seal(SECRET, ANSWERS, OP, "", { now: 1000 });

    expect(rejected(() => unseal(SECRET, state, OP, "", { now: 1601 }))).toContain("expired");
    expect(unseal(SECRET, state, OP, "", { now: 1599 })).toEqual(ANSWERS);
  });

  it("rejects state that belongs to another request", () => {
    const other = operationDigest("tools/call", "deploy", '{"service": "db"}');

    expect(rejected(() => unseal(SECRET, seal(SECRET, ANSWERS, OP, ""), other, ""))).toContain(
      "different request",
    );
  });

  it("rejects state that belongs to another caller", () => {
    const state = seal(SECRET, ANSWERS, OP, principalDigest("Bearer alice"));

    expect(rejected(() => unseal(SECRET, state, OP, principalDigest("Bearer bob")))).toContain(
      "different caller",
    );
  });

  it.each([
    ["empty", new Uint8Array()],
    ["short", new TextEncoder().encode("short")],
    ["mac only", new Uint8Array(32).fill(120)],
  ])("rejects garbage (%s)", (_label, junk) => {
    rejected(() => unseal(SECRET, junk, OP, ""));
  });

  it("uses an empty principal when there are no credentials", () => {
    expect(principalDigest(undefined)).toBe("");
    expect(principalDigest("")).toBe("");
  });
});
