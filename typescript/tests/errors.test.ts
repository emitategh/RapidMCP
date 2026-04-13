import { describe, it, expect } from "vitest";
import { McpError } from "../src/errors.js";

describe("McpError", () => {
  it("stores code and message", () => {
    const err = new McpError(404, "Not found");
    expect(err.code).toBe(404);
    expect(err.message).toBe("Not found");
  });

  it("is an instance of Error", () => {
    const err = new McpError(500, "Internal");
    expect(err).toBeInstanceOf(Error);
  });

  it("has a name property", () => {
    const err = new McpError(408, "Timeout");
    expect(err.name).toBe("McpError");
  });
});
