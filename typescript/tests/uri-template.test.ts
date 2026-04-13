import { describe, it, expect } from "vitest";
import { matchUriTemplate } from "../src/resources/uri-template.js";

describe("matchUriTemplate", () => {
  it("matches simple path parameter", () => {
    expect(matchUriTemplate("res://items/42", "res://items/{id}")).toEqual({ id: "42" });
  });

  it("matches multiple path parameters", () => {
    expect(matchUriTemplate("res://users/5/posts/10", "res://users/{userId}/posts/{postId}"))
      .toEqual({ userId: "5", postId: "10" });
  });

  it("returns null on no match", () => {
    expect(matchUriTemplate("res://other/42", "res://items/{id}")).toBeNull();
  });

  it("matches wildcard parameter", () => {
    expect(matchUriTemplate("res://files/a/b/c.txt", "res://files/{path*}"))
      .toEqual({ path: "a/b/c.txt" });
  });

  it("matches exact URI with no parameters", () => {
    expect(matchUriTemplate("res://info", "res://info")).toEqual({});
  });

  it("returns null when URI doesn't match template", () => {
    expect(matchUriTemplate("res://items/42/extra", "res://items/{id}")).toBeNull();
  });
});
