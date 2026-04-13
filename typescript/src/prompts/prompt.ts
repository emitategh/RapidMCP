export interface CompletionResult {
  values: string[];
  hasMore?: boolean;
  total?: number;
}

export interface PromptArgumentConfig {
  name: string;
  description?: string;
  required?: boolean;
  complete?: (value: string) => Promise<CompletionResult>;
}

export interface PromptConfig {
  name: string;
  description?: string;
  arguments?: PromptArgumentConfig[];
  load: (args: Record<string, string>) => Promise<string>;
}

export interface RegisteredPrompt {
  name: string;
  description: string;
  arguments: PromptArgumentConfig[];
  load: (args: Record<string, string>) => Promise<string>;
}
