export interface ResourceLoadResult {
  text?: string;
  /** Binary content: raw bytes, or a base64-encoded string. */
  blob?: string | Uint8Array;
}

export interface ResourceConfig {
  uri: string;
  name: string;
  description?: string;
  mimeType?: string;
  load: () => Promise<ResourceLoadResult>;
}

export interface RegisteredResource {
  uri: string;
  name: string;
  description: string;
  mimeType: string;
  load: () => Promise<ResourceLoadResult>;
}

export interface ResourceTemplateArgument {
  name: string;
  description?: string;
  required?: boolean;
}

export interface ResourceTemplateConfig {
  uriTemplate: string;
  name: string;
  description?: string;
  mimeType?: string;
  arguments?: ResourceTemplateArgument[];
  load: (args: Record<string, string>) => Promise<ResourceLoadResult>;
}

export interface RegisteredResourceTemplate {
  uriTemplate: string;
  name: string;
  description: string;
  mimeType: string;
  arguments: ResourceTemplateArgument[];
  load: (args: Record<string, string>) => Promise<ResourceLoadResult>;
}
