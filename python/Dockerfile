FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies first (cached layer)
RUN pip install --no-cache-dir grpcio>=1.57.0 protobuf>=4.24.0

# Copy and install the package
COPY src/ src/
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy test server scripts
COPY tests/servers/ tests/servers/

# Run any server script: docker run ... tests/servers/echo.py <port>
ENTRYPOINT ["python"]
CMD ["tests/servers/echo.py", "50051"]
