function buildRegex(template: string): RegExp | null {
  const clean = template.replace(/\{\?[^}]+\}/g, "");
  const parts = clean.split(/(\{[^}]+\})/);
  let pattern = "";
  for (const part of parts) {
    if (part.startsWith("{") && part.endsWith("}")) {
      let name = part.slice(1, -1);
      if (name.endsWith("*")) {
        name = name.slice(0, -1);
        pattern += `(?<${name}>.+)`;
      } else {
        pattern += `(?<${name}>[^/]+)`;
      }
    } else {
      pattern += part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }
  }
  try {
    return new RegExp(`^${pattern}$`);
  } catch {
    return null;
  }
}

/** Names declared in RFC 6570 `{?a,b}` query syntax. */
function queryParamNames(template: string): string[] {
  const match = /\{\?([^}]+)\}/.exec(template);
  return match ? match[1].split(",").map((p) => p.trim()) : [];
}

function decode(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null; // malformed percent-escape
  }
}

export function matchUriTemplate(uri: string, uriTemplate: string): Record<string, string> | null {
  const queryStart = uri.indexOf("?");
  const uriPath = queryStart === -1 ? uri : uri.slice(0, queryStart);
  const regex = buildRegex(uriTemplate);
  if (!regex) return null;
  const match = regex.exec(uriPath);
  if (!match) return null;

  const wildcards = new Set(
    [...uriTemplate.matchAll(/\{([^{}?]+)\*\}/g)].map((m) => m[1]),
  );
  const params: Record<string, string> = {};
  for (const [name, raw] of Object.entries(match.groups ?? {})) {
    const value = decode(raw);
    // `{var}` promises a single path segment; an encoded slash must not turn
    // it into several (`..%2F..%2Fsecret` -> `../../secret`).
    if (value === null || (!wildcards.has(name) && value.includes("/"))) return null;
    params[name] = value;
  }

  if (queryStart !== -1) {
    const query = new URLSearchParams(uri.slice(queryStart + 1));
    for (const name of queryParamNames(uriTemplate)) {
      const value = query.get(name);
      if (value !== null) params[name] = value;
    }
  }
  return params;
}
