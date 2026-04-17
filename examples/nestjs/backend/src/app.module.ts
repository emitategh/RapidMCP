import { Module } from "@nestjs/common";
import { AppController } from "./app.controller.js";
import { McpService } from "./mcp.service.js";

@Module({
  controllers: [AppController],
  providers: [McpService],
})
export class AppModule {}
