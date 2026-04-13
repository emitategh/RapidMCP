import type { ResourceConfig, RegisteredResource, ResourceTemplateConfig, RegisteredResourceTemplate } from "./resource.js";
import { matchUriTemplate } from "./uri-template.js";
import { McpError } from "../errors.js";

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

  listResources(): RegisteredResource[] {
    return [...this._resources.values()];
  }

  listResourceTemplates(): RegisteredResourceTemplate[] {
    return [...this._templates.values()];
  }

  async readResource(uri: string): Promise<Array<{ type: string; text: string; data: Uint8Array; mimeType: string; uri: string }>> {
    const resource = this._resources.get(uri);
    if (resource) {
      const result = await resource.load();
      return [{ type: "text", text: result.text ?? "", data: new Uint8Array(), mimeType: resource.mimeType, uri }];
    }

    for (const template of this._templates.values()) {
      const params = matchUriTemplate(uri, template.uriTemplate);
      if (params) {
        const result = await template.load(params);
        return [{ type: "text", text: result.text ?? "", data: new Uint8Array(), mimeType: template.mimeType, uri }];
      }
    }

    throw new McpError(404, `Resource '${uri}' not found`);
  }
}
