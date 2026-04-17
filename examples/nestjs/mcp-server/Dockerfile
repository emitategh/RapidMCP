FROM node:22-alpine
WORKDIR /app

COPY typescript/ /typescript/

COPY examples/nestjs/mcp-server/package.json examples/nestjs/mcp-server/tsconfig.json ./
RUN sed -i 's|file:../../../typescript|file:/typescript|' package.json && \
    npm install --install-links --no-audit --no-fund

COPY examples/nestjs/mcp-server/server.ts ./
RUN npm run build
CMD ["node", "dist/server.js"]
