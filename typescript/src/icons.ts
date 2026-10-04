/** An icon a client may show next to a tool, resource, prompt or server. */
export interface Icon {
  /** An https: URL or a data: URI. */
  src: string;
  mimeType?: string;
  /** e.g. "48x48", "any". */
  sizes?: string[];
  /** "light" | "dark". */
  theme?: string;
}

/** *icons* as a full list, refusing sources a client must not be asked to load. */
export function checkedIcons(icons: Icon[] | undefined): Icon[] {
  const result = icons ?? [];
  for (const icon of result) {
    const src = icon.src.toLowerCase();
    if (!src.startsWith("https://") && !src.startsWith("data:")) {
      throw new Error(`Icon src must be an https: or data: URI, got ${JSON.stringify(icon.src)}`);
    }
  }
  return result;
}

/** The wire form of an icon list. */
export function wireIcons(icons: Icon[]) {
  return icons.map((i) => ({
    src: i.src,
    mimeType: i.mimeType ?? "",
    sizes: i.sizes ?? [],
    theme: i.theme ?? "",
  }));
}
