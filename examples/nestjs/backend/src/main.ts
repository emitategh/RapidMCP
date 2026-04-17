import "reflect-metadata";
import { NestFactory } from "@nestjs/core";
import { AppModule } from "./app.module.js";

const app = await NestFactory.create(AppModule);
await app.listen(8001, "0.0.0.0");
console.log("NestJS MCP backend listening on :8001");
