import { describe, it, expect } from "vitest";
import { toContentItems, paginate } from "../src/_utils.js";

describe("toContentItems", () => {
  it("converts string to text content", () => {
    const items = toContentItems("hello");
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe("text");
    expect(items[0].text).toBe("hello");
  });

  it("converts null/undefined to empty array", () => {
    expect(toContentItems(undefined)).toEqual([]);
    expect(toContentItems(null)).toEqual([]);
  });

  it("passes through content array objects", () => {
    const input = { content: [{ type: "text", text: "hi" }] };
    const items = toContentItems(input);
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe("text");
    expect(items[0].text).toBe("hi");
  });

  it("converts dict/object to JSON text", () => {
    const items = toContentItems({ key: "value" });
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe("text");
    expect(JSON.parse(items[0].text)).toEqual({ key: "value" });
  });
});

describe("paginate", () => {
  it("returns all items when pageSize is undefined", () => {
    const [page, next] = paginate(["a", "b", "c"], "", undefined);
    expect(page).toEqual(["a", "b", "c"]);
    expect(next).toBe("");
  });

  it("returns first page with cursor", () => {
    const [page, next] = paginate(["a", "b", "c"], "", 2);
    expect(page).toEqual(["a", "b"]);
    expect(next).toBe("2");
  });

  it("returns second page from cursor", () => {
    const [page, next] = paginate(["a", "b", "c"], "2", 2);
    expect(page).toEqual(["c"]);
    expect(next).toBe("");
  });

  it("handles invalid cursor as 0", () => {
    const [page, next] = paginate(["a", "b"], "invalid", 1);
    expect(page).toEqual(["a"]);
    expect(next).toBe("1");
  });
});
