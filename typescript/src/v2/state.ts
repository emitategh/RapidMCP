/**
 * request_state — the server's memory of earlier answers, carried by the client.
 *
 * v2 is stateless, so when a tool asks a second question the first answer has
 * to come back with the retry. It travels in `request_state`, which passes
 * through the client and is therefore attacker-controlled: it is signed, bound
 * to the operation and the caller, and expires.
 *
 * Wire format (the same in the Python implementation):
 * 32 bytes of HMAC-SHA256, then the UTF-8 JSON payload the MAC covers.
 */
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { ErrorCode, McpError } from "../errors.js";

export type Answers = Record<string, { action: string; content: string }>;

export const STATE_TTL_SECONDS = 600;
const MAC_BYTES = 32;

/** Identify the request a state belongs to. */
export function operationDigest(method: string, name: string, argumentsText: string): string {
  return createHash("sha256").update(`${method}\n${name}\n${argumentsText}`).digest("hex");
}

/** Identify the caller a state belongs to; empty when the request carries no credentials. */
export function principalDigest(authorization: string | undefined): string {
  return authorization ? createHash("sha256").update(authorization).digest("hex") : "";
}

const nowSeconds = () => Date.now() / 1000;

export function seal(
  secret: Uint8Array,
  answers: Answers,
  operation: string,
  principal: string,
  opts: { now?: number; ttl?: number; asked?: string[] } = {},
): Uint8Array {
  const issued = opts.now ?? nowSeconds();
  const payload = Buffer.from(
    JSON.stringify({
      v: 1,
      exp: Math.floor(issued + (opts.ttl ?? STATE_TTL_SECONDS)),
      op: operation,
      sub: principal,
      answers,
      // The questions this state is waiting on; only their answers are accepted.
      ...(opts.asked && opts.asked.length > 0 ? { asked: [...opts.asked].sort() } : {}),
    }),
  );
  const mac = createHmac("sha256", secret).update(payload).digest();
  return new Uint8Array(Buffer.concat([mac, payload]));
}

function rejected(reason: string): McpError {
  return new McpError(ErrorCode.InvalidParams, `Invalid request_state: ${reason}`);
}

/** The answers inside *state* and the questions it is waiting on; McpError(-32602) if it cannot be trusted. */
export function unsealState(
  secret: Uint8Array,
  state: Uint8Array,
  operation: string,
  principal: string,
  opts: { now?: number } = {},
): { answers: Answers; asked: string[] } {
  const bytes = Buffer.from(state);
  const mac = bytes.subarray(0, MAC_BYTES);
  const payload = bytes.subarray(MAC_BYTES);
  const expected = createHmac("sha256", secret).update(payload).digest();
  if (mac.length !== MAC_BYTES || !timingSafeEqual(mac, expected)) {
    throw rejected("it was not issued by this server, or has been altered");
  }
  let data: { exp?: unknown; op?: unknown; sub?: unknown; answers?: unknown; asked?: unknown };
  try {
    data = JSON.parse(payload.toString("utf8"));
  } catch {
    throw rejected("it is malformed");
  }
  if (typeof data?.exp !== "number" || typeof data.answers !== "object" || data.answers === null) {
    throw rejected("it is malformed");
  }
  if (data.exp < (opts.now ?? nowSeconds())) throw rejected("it has expired");
  if (data.op !== operation) throw rejected("it belongs to a different request");
  if (data.sub !== principal) throw rejected("it belongs to a different caller");
  const asked = data.asked ?? [];
  if (!Array.isArray(asked)) throw rejected("it is malformed");
  return { answers: data.answers as Answers, asked: asked as string[] };
}

/** The answers inside *state*, or McpError(-32602) if it cannot be trusted. */
export function unseal(
  secret: Uint8Array,
  state: Uint8Array,
  operation: string,
  principal: string,
  opts: { now?: number } = {},
): Answers {
  return unsealState(secret, state, operation, principal, opts).answers;
}
