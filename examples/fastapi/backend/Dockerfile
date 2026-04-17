FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Copy the library source so the relative uv source resolves inside the image.
COPY python/ /python/
COPY proto/ /proto/

# Backend-specific files.
COPY examples/fastapi/backend/pyproject.toml ./pyproject.toml
RUN sed -i 's|path = "../../../python"|path = "/python"|' pyproject.toml && \
    uv sync --no-dev

COPY examples/fastapi/backend/app.py ./app.py
CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
