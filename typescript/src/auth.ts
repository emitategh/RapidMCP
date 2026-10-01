import { ChannelCredentials } from "nice-grpc";
import { ServerCredentials } from "@grpc/grpc-js";
import {
  Metadata,
  ServerError,
  Status,
  type CallContext,
  type ServerMiddlewareCall,
} from "nice-grpc-common";
import { readFileSync } from "node:fs";

export interface TlsConfig {
  rootCert: string | Buffer;
  clientCert?: string | Buffer;
  clientKey?: string | Buffer;
}

export interface ClientOptions {
  token?: string;
  tls?: TlsConfig;
  requestTimeout?: number;
}

/** PEM material for the server: a file path or the bytes themselves. */
export interface ServerTlsConfig {
  cert: string | Buffer;
  key: string | Buffer;
  /** CA bundle — when set, clients must present a certificate signed by it (mTLS). */
  ca?: string | Buffer;
}

/** Decides whether a bearer token may open a session. */
export type TokenVerifier = (token: string) => boolean | Promise<boolean>;

function resolveCert(value: string | Buffer): Buffer {
  if (Buffer.isBuffer(value)) return value;
  return readFileSync(value);
}

export function buildServerCredentials(tls: ServerTlsConfig): ServerCredentials {
  const ca = tls.ca ? resolveCert(tls.ca) : null;
  return ServerCredentials.createSsl(
    ca,
    [{ private_key: resolveCert(tls.key), cert_chain: resolveCert(tls.cert) }],
    ca !== null,
  );
}

/**
 * Server middleware that checks the `authorization` metadata before any
 * message is handled. The optional `Bearer ` prefix is stripped; a verifier
 * that returns false or throws ends the call with UNAUTHENTICATED.
 */
export function authMiddleware(verify: TokenVerifier) {
  return async function* <Request, Response>(
    call: ServerMiddlewareCall<Request, Response>,
    context: CallContext,
  ) {
    const raw = (context.metadata.get("authorization") ?? "").trim();
    const token = /^bearer /i.test(raw) ? raw.slice(7).trim() : raw;
    let ok = false;
    try {
      ok = Boolean(await verify(token));
    } catch (err) {
      console.error("[rapidmcp] auth verifier failed:", err);
    }
    if (!ok) {
      throw new ServerError(Status.UNAUTHENTICATED, "Invalid token");
    }
    return yield* call.next(call.request, context);
  };
}

export function buildChannelCredentials(opts: ClientOptions): ChannelCredentials {
  if (!opts.tls) {
    return ChannelCredentials.createInsecure();
  }
  const rootCert = resolveCert(opts.tls.rootCert);
  const clientKey = opts.tls.clientKey ? resolveCert(opts.tls.clientKey) : null;
  const clientCert = opts.tls.clientCert ? resolveCert(opts.tls.clientCert) : null;
  return ChannelCredentials.createSsl(rootCert, clientKey, clientCert);
}

export function buildMetadata(opts: ClientOptions): Metadata {
  const metadata = new Metadata();
  if (opts.token) {
    metadata.set("authorization", `Bearer ${opts.token}`);
  }
  return metadata;
}
