import type {
  ResourceConfig,
  RegisteredResource,
  ResourceLoadResult,
  ResourceTemplateConfig,
  RegisteredResourceTemplate,
} from "./resource.js";
import { matchUriTemplate } from "./uri-template.js";
import { ErrorCode, McpError } from "../errors.js";

type Content = { type: string; text: string; data: Uint8Array; mimeType: string; uri: string };

/** Text stays text; a blob becomes image/audio/resource content according to its mime type. */
function toContent(result: ResourceLoadResult, mimeType: string, uri: string): Content[] {
  if (result.blob === undefined) {
    return [{ type: "text", text: result.text ?? "", data: new Uint8Array(), mimeType, uri }];
  }
  const data =
    typeof result.blob === "string"
      ? new Uint8Array(Buffer.from(result.blob, "base64"))
      : result.blob;
  const type = mimeType.startsWith("image/")
    ? "image"
    : mimeType.startsWith("audio/")
      ? "audio"
      : "resource";
  return [{ type, text: "", data, mimeType, uri }];
}

export class ResourceManager {
  private _resources = new Map<string, RegisteredResource>();
  private _templates = new Map<string, RegisteredResourceTemplate>();

  addResource(config: ResourceConfig): void {
    this._resources.set(config.uri, {
      uri: config.uri,
      name: config.name,
      description: config.description ?? "",
      mimeType: config.mimeType ?? "text/plain",
      load: config.load,
    });
  }

  addResourceTemplate(config: ResourceTemplateConfig): void {
    this._templates.set(config.uriTemplate, {
      uriTemplate: config.uriTemplate,
      name: config.name,
      description: config.description ?? "",
      mimeType: config.mimeType ?? "text/plain",
      arguments: config.arguments ?? [],
      load: config.load,
    });
  }

  /** Add already-built registrations (used when mounting another server). */
  registerResource(resource: RegisteredResource): void {
    this._resources.set(resource.uri, resource);
  }

  registerResourceTemplate(template: RegisteredResourceTemplate): void {
    this._templates.set(template.uriTemplate, template);
  }

  hasResource(uri: string): boolean {
    return this._resources.has(uri);
  }

  hasResourceTemplate(uriTemplate: string): boolean {
    return this._templates.has(uriTemplate);
  }

  listResources(): RegisteredResource[] {
    return [...this._resources.values()];
  }

  listResourceTemplates(): RegisteredResourceTemplate[] {
    return [...this._templates.values()];
  }

  async readResource(uri: string): Promise<Array<{ type: string; text: string; data: Uint8Array; mimeType: string; uri: string }>> {
    const resource = this._resources.get(uri);
    if (resource) {
      return toContent(await resource.load(), resource.mimeType, uri);
    }

    for (const template of this._templates.values()) {
      const params = matchUriTemplate(uri, template.uriTemplate);
      if (params) {
        return toContent(await template.load(params), template.mimeType, uri);
      }
    }

    throw new McpError(ErrorCode.InvalidParams, `Resource '${uri}' not found`);
  }
}
