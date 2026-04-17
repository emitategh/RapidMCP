FROM node:22-alpine
WORKDIR /app

# Copy the library source; dist/ must be pre-built on the host before docker build.
COPY typescript/ /typescript/

# Backend-specific files.
COPY examples/nestjs/backend/package.json examples/nestjs/backend/tsconfig.json ./
RUN sed -i 's|file:../../../typescript|file:/typescript|' package.json && \
    npm install --install-links --no-audit --no-fund

COPY examples/nestjs/backend/src/ ./src/
RUN npm run build
CMD ["node", "dist/main.js"]
