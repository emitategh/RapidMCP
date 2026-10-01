export interface ContentResult {
  content: Array<{ type: string; text?: string; data?: Uint8Array; mimeType?: string; uri?: string }>;
}

function isContentResult(value: unknown): value is ContentResult {
  return (
    typeof value === "object" &&
    value !== null &&
    "content" in value &&
    Array.isArray((value as ContentResult).content)
  );
}

export function toContentItems(
  result: unknown,
): Array<{ type: string; text: string; data: Uint8Array; mimeType: string; uri: string }> {
  if (result === null || result === undefined) {
    return [];
  }
  if (typeof result === "string") {
    return [{ type: "text", text: result, data: new Uint8Array(), mimeType: "", uri: "" }];
  }
  if (isContentResult(result)) {
    return result.content.map((c) => ({
      type: c.type,
      text: c.text ?? "",
      data: c.data ?? new Uint8Array(),
      mimeType: c.mimeType ?? "",
      uri: c.uri ?? "",
    }));
  }
  if (typeof result === "object") {
    return [{ type: "text", text: JSON.stringify(result), data: new Uint8Array(), mimeType: "", uri: "" }];
  }
  return [{ type: "text", text: String(result), data: new Uint8Array(), mimeType: "", uri: "" }];
}

/**
 * Insert *prefix* as the first path segment after the scheme:
 * "res://greeting" -> "res://users/greeting"; "plain/path" -> "users/plain/path".
 */
export function prefixResourceUri(uri: string, prefix: string): string {
  const at = uri.indexOf("://");
  if (at === -1) return `${prefix}/${uri}`;
  return `${uri.slice(0, at)}://${prefix}/${uri.slice(at + 3)}`;
}

export function paginate<T>(items: T[], cursorStr: string, pageSize: number | undefined): [T[], string] {
  if (pageSize === undefined) {
    return [items, ""];
  }
  let offset = 0;
  if (cursorStr) {
    const parsed = parseInt(cursorStr, 10);
    offset = Number.isNaN(parsed) || parsed < 0 ? 0 : parsed;
  }
  const page = items.slice(offset, offset + pageSize);
  const nextOffset = offset + pageSize;
  const nextCursor = nextOffset < items.length ? String(nextOffset) : "";
  return [page, nextCursor];
}
