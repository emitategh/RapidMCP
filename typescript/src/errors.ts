export class McpError extends Error {
  public readonly code: number;

  constructor(code: number, message: string) {
    super(message);
    this.name = "McpError";
    this.code = code;
  }
}
