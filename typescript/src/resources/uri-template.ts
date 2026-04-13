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

export function matchUriTemplate(uri: string, uriTemplate: string): Record<string, string> | null {
  const [uriPath] = uri.split("?", 1);
  const regex = buildRegex(uriTemplate);
  if (!regex) return null;
  const match = regex.exec(uriPath);
  if (!match) return null;
  return { ...match.groups } as Record<string, string>;
}
