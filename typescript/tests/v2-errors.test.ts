import { describe, it, expect } from "vitest";
import { Metadata, Status } from "nice-grpc-common";
import { McpError } from "../src/errors.js";
import { errorFromRpc, setErrorTrailers, statusFor } from "../src/v2/errors.js";

describe("MCP errors over gRPC statuses", () => {
  it.each([
    [-32602, Status.INVALID_ARGUMENT],
    [-32601, Status.UNIMPLEMENTED],
    [-32603, Status.INTERNAL],
    [-32021, Status.FAILED_PRECONDITION],
    [-32022, Status.FAILED_PRECONDITION],
    [1234, Status.UNKNOWN],
  ])("maps MCP code %i to its gRPC status", (code, status) => {
    expect(statusFor(code)).toBe(status);
  });

  it("round-trips an error with its exact code and data", () => {
    const sent = new McpError(-32022, "Unsupported protocol version", {
      supported: ["2026-07-28"],
      requested: "1900-01-01",
    });
    const trailer = new Metadata();
    setErrorTrailers(trailer, sent);

    const received = errorFromRpc(statusFor(sent.code), sent.message, trailer);

    expect(received?.code).toBe(-32022);
    expect(received?.message).toBe("Unsupported protocol version");
    expect(received?.data).toEqual({ supported: ["2026-07-28"], requested: "1900-01-01" });
  });

  it("round-trips an error without data as null data", () => {
    const trailer = new Metadata();
    setErrorTrailers(trailer, new McpError(-32602, "bad"));

    expect(errorFromRpc(Status.INVALID_ARGUMENT, "bad", trailer)?.data).toBeNull();
  });

  it("turns transport failures into local client errors", () => {
    expect(errorFromRpc(Status.DEADLINE_EXCEEDED, "deadline", null)?.code).toBe(408);
    expect(errorFromRpc(Status.UNAVAILABLE, "no connection", null)?.code).toBe(503);
  });

  it("leaves other gRPC failures alone", () => {
    expect(errorFromRpc(Status.UNAUTHENTICATED, "Invalid token", null)).toBeNull();
    expect(errorFromRpc(Status.UNIMPLEMENTED, "no such method", new Metadata())).toBeNull();
  });
});
