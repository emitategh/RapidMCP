FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

COPY python/ /python/
COPY proto/ /proto/

COPY examples/fastapi/mcp-server/pyproject.toml ./pyproject.toml
RUN sed -i 's|path = "../../../python"|path = "/python"|' pyproject.toml && \
    uv sync --no-dev

COPY examples/fastapi/mcp-server/server.py ./server.py
CMD ["uv", "run", "python", "server.py"]
