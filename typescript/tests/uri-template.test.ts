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

  it("decodes percent-escapes in a variable", () => {
    expect(matchUriTemplate("res://files/my%20file.txt", "res://files/{name}")).toEqual({
      name: "my file.txt",
    });
  });

  it("does not let an encoded slash smuggle extra segments into a single variable", () => {
    expect(matchUriTemplate("res://files/..%2F..%2Fsecret", "res://files/{name}")).toBeNull();
  });

  it("allows an encoded slash in a wildcard variable", () => {
    expect(matchUriTemplate("res://files/a%2Fb.txt", "res://files/{path*}")).toEqual({
      path: "a/b.txt",
    });
  });

  it("extracts query parameters declared in the template", () => {
    expect(matchUriTemplate("res://search?q=cat&limit=5&other=x", "res://search{?q,limit}")).toEqual({
      q: "cat",
      limit: "5",
    });
  });
});
