# Examples Stack Migration & End-to-End Verification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing `D:\Trabajo\mcp-test` harness into `D:\Trabajo\mcp-grpc\examples\` as a first-class examples stack, migrate both backends from the deleted `MCPToolkit` to the new `RapidMCPClient`, and verify end-to-end via Docker Compose against the full MCP spec matrix (tools, resources, prompts, elicitation, sampling, progress, logging, ToolError).

**Architecture:** The examples stack consists of four containers on one Docker network: `py-mcp-server` + `py-backend` (FastAPI + LangChain agent) and `ts-mcp-server` + `ts-backend` (NestJS + LangChain agent). Because the examples now live inside the library monorepo, both backends install `rapidmcp` / `@emitate/rapidmcp` directly from sibling source paths (`../../../python`, `../../../typescript`) — no wheel/tarball staging. Servers (`server.py`, `server.ts`) stay unchanged; only the backend adapter surface is migrated.

**Tech Stack:** rapidmcp 0.3.2 (Python) / @emitate/rapidmcp 0.1.4 (npm, in-tree), FastAPI, NestJS, Docker Compose, Claude Sonnet 4.6 via `@langchain/anthropic`.

**Target layout:**
```
mcp-grpc/
├── python/
├── typescript/
├── docs/
└── examples/                    ← new
    ├── README.md
    ├── docker-compose.yml
    ├── .env.example
    ├── .gitignore
    ├── fastapi/
    │   ├── backend/
    │   │   ├── Dockerfile
    │   │   ├── pyproject.toml
    │   │   └── app.py
    │   └── mcp-server/
    │       ├── Dockerfile
    │       ├── pyproject.toml
    │       └── server.py
    └── nestjs/
        ├── backend/
        │   ├── Dockerfile
        │   ├── package.json
        │   ├── tsconfig.json
        │   └── src/
        └── mcp-server/
            ├── Dockerfile
            ├── package.json
            ├── tsconfig.json
            └── server.ts
```

**Non-goals:**
- Preserving the git history of `mcp-test` (a fresh copy is cleaner)
- Changing the MCP server implementations — only the backend adapters migrate
- Publishing to PyPI / npm — deferred to Phase 8 once E2E passes

---

## Phase 0 — Bring the stack in-tree

### Task 0.1: Copy mcp-test contents into examples/

**Files:**
- Create: `D:\Trabajo\mcp-grpc\examples\` and every subdirectory + file from `D:\Trabajo\mcp-test\` (except `.env`, `.venv/`, `node_modules/`, `dist/`, `*.egg-info/`, lockfiles that will be regenerated).

- [ ] **Step 1: Copy with a filter**

From PowerShell or `bash`:
```bash
mkdir -p /d/Trabajo/mcp-grpc/examples
rsync -av \
  --exclude '.venv' --exclude 'node_modules' --exclude 'dist' \
  --exclude '*.egg-info' --exclude '.env' --exclude 'uv.lock' --exclude 'package-lock.json' \
  /d/Trabajo/mcp-test/ /d/Trabajo/mcp-grpc/examples/
```
(Substitute `xcopy` / `Copy-Item -Recurse` with equivalent excludes if rsync is unavailable.)

- [ ] **Step 2: Verify the layout**

```bash
ls -la /d/Trabajo/mcp-grpc/examples/
ls /d/Trabajo/mcp-grpc/examples/fastapi/backend/
ls /d/Trabajo/mcp-grpc/examples/nestjs/backend/src/
```
Expected: `docker-compose.yml`, `fastapi/`, `nestjs/`, `README.md`, `.gitignore`. Each backend has its source files.

- [ ] **Step 3: Rename README.md to keep the original as a template**

The copied README will be rewritten substantially in Phase 7. Do nothing here — just note that `examples/README.md` exists and will be updated.

### Task 0.2: Create `.env.example`

**Files:**
- Create: `D:\Trabajo\mcp-grpc\examples\.env.example`

- [ ] **Step 1: Write the template**

```
# Copy to .env and fill in a real key before `docker compose up`.
ANTHROPIC_API_KEY=sk-ant-...
```

The real `.env` is produced per-user and git-ignored; `.env.example` is committed as documentation.

### Task 0.3: Configure root-level .dockerignore

**Files:**
- Create: `D:\Trabajo\mcp-grpc\.dockerignore`

- [ ] **Step 1: Aggressive excludes**

Since the compose file will use `context: ../..` (the repo root) so Dockerfiles can `COPY` from sibling library folders, a strong `.dockerignore` keeps the build context small.

```
# Build artefacts and caches
**/.venv/
**/node_modules/
**/dist/
**/build/
**/*.egg-info/
**/__pycache__/
**/.pytest_cache/
**/.ruff_cache/

# Editor / OS / git
**/.git/
**/.idea/
**/.vscode/
**/.DS_Store

# Benchmark results and docs (not needed in images)
benchmark/results/
docs/

# Test artefacts
**/.coverage
**/coverage/

# Examples loopback — don't recursively include examples in examples' own images
examples/**/wheels/
examples/**/*.tgz
```

### Task 0.4: Update examples/.gitignore

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\examples\.gitignore`

- [ ] **Step 1: Ignore local-only artefacts**

```
# Environment
.env

# Python
**/.venv/
**/__pycache__/
**/*.egg-info/

# Node
**/node_modules/
**/dist/

# Lockfiles are committed — do NOT ignore uv.lock or package-lock.json
```

### Task 0.5: Wire Python backend to sibling source

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\examples\fastapi\backend\pyproject.toml`
- Modify: `D:\Trabajo\mcp-grpc\examples\fastapi\backend\Dockerfile`
- Create: `D:\Trabajo\mcp-grpc\examples\fastapi\mcp-server\pyproject.toml` (only modify if already exists)
- Modify: `D:\Trabajo\mcp-grpc\examples\fastapi\mcp-server\Dockerfile`

- [ ] **Step 1: Point backend pyproject at sibling rapidmcp**

Replace the backend `pyproject.toml`:
```toml
[project]
name = "mcp-example-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "langchain",
    "langchain-anthropic",
    "langchain-core",
    "langgraph",
    "rapidmcp",
]

[tool.uv.sources]
rapidmcp = { path = "../../../python" }

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
```

The `[tool.uv.sources]` entry tells uv to resolve `rapidmcp` from the repo's own Python library rather than PyPI. The relative path resolves against the pyproject's directory: `examples/fastapi/backend/../../../python` → `python/`.

- [ ] **Step 2: Same override for the mcp-server pyproject**

Apply the same `[tool.uv.sources]` block to `examples/fastapi/mcp-server/pyproject.toml`. The server side also imports `rapidmcp` and must resolve the in-tree version.

- [ ] **Step 3: Rewrite both Dockerfiles with a wide build context**

`examples/fastapi/backend/Dockerfile`:
```dockerfile
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Copy the library source first so the relative-path uv source resolves.
COPY python/ /python/
COPY proto/ /proto/

# Copy backend-specific files.
COPY examples/fastapi/backend/pyproject.toml ./pyproject.toml
RUN uv sync --no-dev

COPY examples/fastapi/backend/app.py ./app.py
CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

`examples/fastapi/mcp-server/Dockerfile`:
```dockerfile
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

COPY python/ /python/
COPY proto/ /proto/

COPY examples/fastapi/mcp-server/pyproject.toml ./pyproject.toml
RUN uv sync --no-dev

COPY examples/fastapi/mcp-server/server.py ./server.py
CMD ["uv", "run", "python", "server.py"]
```

Key: relative paths inside the uv source (`../../../python`) don't exist inside the container — but we copy the library to `/python/` at the same _relative offset_ from `/app/` as on the host. Because the pyproject is at `/app/pyproject.toml` and uv resolves `../../../python` → `/../../../python` outside the filesystem, it would fail. **Fix:** use an absolute path in Docker by overriding the source at image build time.

Simpler and more robust: in each Dockerfile, before `uv sync`, rewrite the source to the absolute path:
```dockerfile
RUN sed -i 's|path = "../../../python"|path = "/python"|' pyproject.toml && \
    uv sync --no-dev
```

Or cleaner: split the source config via `uv.toml` overlay. The `sed` approach keeps it in one file — prefer it unless the team has an aesthetic objection.

### Task 0.6: Wire TypeScript backend to sibling source

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\examples\nestjs\backend\package.json`
- Modify: `D:\Trabajo\mcp-grpc\examples\nestjs\backend\Dockerfile`
- Modify: `D:\Trabajo\mcp-grpc\examples\nestjs\mcp-server\package.json`
- Modify: `D:\Trabajo\mcp-grpc\examples\nestjs\mcp-server\Dockerfile`

- [ ] **Step 1: Point backend package.json at sibling typescript/**

Replace the `@emitate/rapidmcp` dep:
```json
"dependencies": {
    ...,
    "@emitate/rapidmcp": "file:../../../typescript"
}
```

`npm` resolves `file:` dependencies by pointing at a directory (treats it like a local package). Since `typescript/` has a valid `package.json` + compiled `dist/`, this works as long as the TS package has been `npm run build`-ed at least once.

- [ ] **Step 2: Same override for the mcp-server package.json**

The NestJS `mcp-server` also imports `@emitate/rapidmcp`. Apply the same `file:../../../typescript` override.

- [ ] **Step 3: Rewrite both Dockerfiles with a wide build context**

`examples/nestjs/backend/Dockerfile`:
```dockerfile
FROM node:22-alpine
WORKDIR /app

# Copy the library and make sure dist/ is pre-built on the host before docker build.
COPY typescript/ /typescript/

# Backend files
COPY examples/nestjs/backend/package.json examples/nestjs/backend/package-lock.json* examples/nestjs/backend/tsconfig.json ./
# Rewrite the file: path to the absolute in-image location.
RUN sed -i 's|file:../../../typescript|file:/typescript|' package.json && \
    npm ci

COPY examples/nestjs/backend/src/ ./src/
RUN npm run build
CMD ["node", "dist/main.js"]
```

`examples/nestjs/mcp-server/Dockerfile`: same shape, replacing `backend/` with `mcp-server/` and the command with `node dist/server.js`.

- [ ] **Step 4: Host-side prep — ensure `typescript/dist` is built**

`npm install file:../../../typescript` only works if the target directory has the compiled output. Before building images, on the host:
```bash
cd /d/Trabajo/mcp-grpc/typescript
npm run build   # produces dist/
```

Note: with compose's `--no-cache` rebuild, this step must be re-run any time the TS library changes.

### Task 0.7: Update docker-compose.yml to use repo-root context

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\examples\docker-compose.yml`

- [ ] **Step 1: Rewrite compose with repo-root context**

```yaml
services:
  py-mcp-server:
    build:
      context: ../..
      dockerfile: examples/fastapi/mcp-server/Dockerfile
    ports:
      - "50051:50051"
    networks:
      - mcp-net

  py-backend:
    build:
      context: ../..
      dockerfile: examples/fastapi/backend/Dockerfile
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      MCP_ADDRESS: py-mcp-server:50051
    depends_on:
      - py-mcp-server
    networks:
      - mcp-net

  ts-mcp-server:
    build:
      context: ../..
      dockerfile: examples/nestjs/mcp-server/Dockerfile
    ports:
      - "50052:50051"
    networks:
      - mcp-net

  ts-backend:
    build:
      context: ../..
      dockerfile: examples/nestjs/backend/Dockerfile
    ports:
      - "8001:8001"
    env_file:
      - .env
    environment:
      MCP_ADDRESS: ts-mcp-server:50051
    depends_on:
      - ts-mcp-server
    networks:
      - mcp-net

networks:
  mcp-net:
    driver: bridge
```

The compose file stays in `examples/`, but every `context: ../..` climbs to the repo root so each Dockerfile can `COPY python/`, `COPY typescript/`, and `COPY examples/...` independently.

### Task 0.8: Commit the import

**Files:**
- All new files under `examples/`, plus `.dockerignore` at repo root.

- [ ] **Step 1: Stage and inspect**

```bash
cd /d/Trabajo/mcp-grpc
git status
git diff --stat HEAD
```
Expected: all new files under `examples/`; no modifications to `python/`, `typescript/`, `proto/`, `docs/`.

- [ ] **Step 2: Commit**

```bash
git add .dockerignore examples/
git commit -F - <<'EOF'
feat(examples): import mcp-test harness as examples/ (local-install layout)

Pulled the FastAPI + NestJS E2E stack into the monorepo as examples/.
Both backends install rapidmcp from sibling source paths via
[tool.uv.sources] / file: deps — no wheel or tarball staging.
EOF
```

---

## Phase 1 — Migrate the Python (FastAPI) backend

> Prereq: Phase 0 done — examples stack is in-tree and resolves rapidmcp from `../../../python`.

### Task 1.1: Rewrite app.py to use RapidMCPClient

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\examples\fastapi\backend\app.py`

- [ ] **Step 1: Replace MCPToolkit import and constructor**

Replace the lifespan block, currently:
```python
from rapidmcp.integrations.langchain import MCPToolkit

toolkit = MCPToolkit(MCP_ADDRESS)
toolkit.client.set_sampling_handler(mock_sampling_handler)
toolkit.client.set_elicitation_handler(mock_elicitation_handler)

for attempt in range(1, 4):
    try:
        await toolkit.client.connect()
        ...
```
with:
```python
from rapidmcp.integrations.langchain import RapidMCPClient

rc = RapidMCPClient({"default": {"address": MCP_ADDRESS}})
default_client = rc.client("default")
default_client.set_sampling_handler(mock_sampling_handler)
default_client.set_elicitation_handler(mock_elicitation_handler)

for attempt in range(1, 4):
    try:
        await rc.connect()
        ...
```

- [ ] **Step 2: Swap app.state and shutdown**

```python
app.state.rc = rc
yield
await rc.close()
```

- [ ] **Step 3: Replace the DI helper**

```python
def get_rc(request: Request) -> "RapidMCPClient":
    return request.app.state.rc
```

- [ ] **Step 4: Update every endpoint**

Replace `toolkit = get_toolkit(request)` + `toolkit.client.X(...)` with `client = get_rc(request).client("default")` + `client.X(...)`. The `/chat` endpoint becomes:
```python
@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    from langchain_anthropic import ChatAnthropic
    from langgraph.prebuilt import create_react_agent

    rc = get_rc(request)
    tools = await rc.get_tools("default")

    llm = ChatAnthropic(model="claude-sonnet-4-6", max_tokens=1024)
    agent = create_react_agent(
        llm,
        tools,
        prompt="You are a helpful assistant with access to MCP tools. Use them when appropriate.",
    )
    result = await agent.ainvoke({"messages": [("user", req.message)]})
    return ChatResponse(response=result["messages"][-1].content)
```

The `/tools`, `/resources`, `/resources/{uri}`, `/prompts`, `/prompts/{name}`, `/health` endpoints all call `get_rc(request).client("default").<method>(...)`.

- [ ] **Step 5: Verify module imports**

```bash
cd /d/Trabajo/mcp-grpc/examples/fastapi/backend
uv sync
uv run python -c "import app; print('ok')"
```
Expected: `ok` (app defines the FastAPI instance without raising).

### Task 1.2: Commit Python migration

- [ ] **Step 1: Commit**

```bash
cd /d/Trabajo/mcp-grpc
git add examples/fastapi/backend/app.py examples/fastapi/backend/uv.lock
git commit -m "feat(examples-py): migrate backend from MCPToolkit to RapidMCPClient"
```

---

## Phase 2 — Migrate the TypeScript (NestJS) backend

> Prereq: `cd typescript && npm run build` has been run on the host so `file:../../../typescript` resolves.

### Task 2.1: Rewrite mcp.service.ts to use RapidMCPClient

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\examples\nestjs\backend\src\mcp.service.ts`

- [ ] **Step 1: Full file replacement**

```typescript
import { Injectable, OnModuleInit, OnModuleDestroy, Logger } from "@nestjs/common";
import { RapidMCPClient } from "@emitate/rapidmcp/integrations/langchain";
import type { Client } from "@emitate/rapidmcp";

const MCP_ADDRESS = process.env["MCP_ADDRESS"] ?? "ts-mcp-server:50051";

@Injectable()
export class McpService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(McpService.name);
  private _rc!: RapidMCPClient;

  async onModuleInit(): Promise<void> {
    this._rc = new RapidMCPClient({ default: { address: MCP_ADDRESS } });
    const defaultClient = this._rc.client("default");

    defaultClient.setSamplingHandler(async (req: any) => {
      const texts: string[] = (req.messages ?? [])
        .flatMap((m: any) => (m.content ?? []))
        .filter((c: any) => Boolean(c.text))
        .map((c: any) => c.text as string);
      const input = texts.join(" ").slice(0, 120);
      return {
        role: "assistant",
        content: [{ type: "text", text: `Summary of: ${input}` }],
        model: "mock",
        stopReason: "end_turn",
      } as any;
    });

    defaultClient.setElicitationHandler(async () => ({
      action: "accept",
      content: JSON.stringify({ confirm: true }),
    }));

    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        await this._rc.connect();
        this.logger.log(`Connected to MCP server at ${MCP_ADDRESS}`);
        return;
      } catch (err) {
        if (attempt === 3) {
          throw new Error(`Could not connect to MCP server after 3 attempts: ${String(err)}`);
        }
        this.logger.warn(`MCP server not ready (attempt ${attempt}/3), retrying in 2s…`);
        await new Promise<void>((resolve) => setTimeout(resolve, 2000));
      }
    }
  }

  async onModuleDestroy(): Promise<void> {
    await this._rc.close();
    this.logger.log("Disconnected from MCP server");
  }

  get rc(): RapidMCPClient {
    return this._rc;
  }

  get client(): Client {
    return this._rc.client("default");
  }
}
```

### Task 2.2: Update app.controller.ts

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\examples\nestjs\backend\src\app.controller.ts`

- [ ] **Step 1: Swap `toolkit.client` and `toolkit.getTools`**

Replace every `this.mcpService.toolkit.client.X(...)` with `this.mcpService.client.X(...)`. The `/chat` endpoint's `getTools` call becomes:
```typescript
const tools = await this.mcpService.rc.getTools();
```

### Task 2.3: Type-check and commit

- [ ] **Step 1: Type check**

```bash
cd /d/Trabajo/mcp-grpc/examples/nestjs/backend
npm install   # picks up the in-tree typescript/ package
npx tsc --noEmit
```
Expected: clean.

- [ ] **Step 2: Commit**

```bash
cd /d/Trabajo/mcp-grpc
git add examples/nestjs/backend/package.json examples/nestjs/backend/package-lock.json \
        examples/nestjs/backend/src/mcp.service.ts examples/nestjs/backend/src/app.controller.ts
git commit -m "feat(examples-ts): migrate backend from MCPToolkit to RapidMCPClient"
```

---

## Phase 3 — Rebuild and boot the full stack

### Task 3.1: Prerequisite check

- [ ] **Step 1: Confirm `.env` exists with a real key**

```bash
cp /d/Trabajo/mcp-grpc/examples/.env.example /d/Trabajo/mcp-grpc/examples/.env
# then edit .env and fill in ANTHROPIC_API_KEY
grep -c "^ANTHROPIC_API_KEY=sk-" /d/Trabajo/mcp-grpc/examples/.env   # expect: 1
```

- [ ] **Step 2: Pre-build the TS library on host**

```bash
cd /d/Trabajo/mcp-grpc/typescript
npm run build   # ensures dist/ exists for the file: dep
```

- [ ] **Step 3: Docker availability**

```bash
docker version && docker compose version
```

### Task 3.2: Clean build and start

- [ ] **Step 1: Build all four images**

```bash
cd /d/Trabajo/mcp-grpc/examples
docker compose down -v
docker compose build --no-cache
```
Expected: four images built, no errors. Common failure points to investigate if this fails:
- `context` path wrong → `COPY python/` fails
- `sed` not rewriting uv sources → `uv sync` tries to resolve `rapidmcp` from PyPI
- `npm ci` complains about mismatched lockfile → re-run `npm install` on host to regenerate

- [ ] **Step 2: Start the stack**

```bash
docker compose up -d
sleep 15
docker compose ps
```
Expected: all four services `Up`, with published ports `8000`, `8001`, `50051`, `50052`.

- [ ] **Step 3: Tail logs to confirm clean boot**

```bash
docker compose logs --tail=20 py-backend ts-backend
```
Expected: each backend logs `Connected to MCP server at <addr>:50051` within ~2 seconds of startup.

---

## Phase 4 — End-to-end test matrix (Python backend, port 8000)

All tests target `http://localhost:8000`. Record pass/fail per cell.

### Task 4.1: Health + tools

- [ ] `curl http://localhost:8000/health` → `{"status":"ok","mcp_server":"py-mcp-server:50051"}`
- [ ] `curl http://localhost:8000/tools | jq '.tools | length'` → `7`
- [ ] `curl http://localhost:8000/tools | jq -r '.tools[].name' | sort` → `add`, `confirm_action`, `echo`, `fail_tool`, `log_demo`, `long_running_task`, `summarize_with_llm`

### Task 4.2: Resources

- [ ] `curl http://localhost:8000/resources | jq '.resources | length, .templates | length'` → `2` and `1`
- [ ] `curl "http://localhost:8000/resources/res%3A%2F%2Fserver-info"` → content includes `"name":"mcp-test-server"`
- [ ] `curl "http://localhost:8000/resources/res%3A%2F%2Fconfig"` → content includes `"debug":true`
- [ ] `curl "http://localhost:8000/resources/res%3A%2F%2Fitems%2F42"` → content includes `"id":"42"`

### Task 4.3: Prompts

- [ ] `curl http://localhost:8000/prompts | jq '.prompts[0].name'` → `"greet"`
- [ ] `curl "http://localhost:8000/prompts/greet?name=Alice"` → message `Dear Alice, I hope this message finds you well.`
- [ ] `curl "http://localhost:8000/prompts/greet?name=Alice&style=pirate"` → `Ahoy, Alice! Shiver me timbers!`
- [ ] `curl "http://localhost:8000/prompts/greet?name=Alice&style=shakespearean"` → `Hark! Alice, thou art most welcome.`

### Task 4.4: Chat — 7 agent scenarios

Helper (once per shell):
```bash
post() { echo "{\"message\":\"$1\"}" > /tmp/body.json && \
         curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d @/tmp/body.json | jq -r '.response'; }
```

- [ ] `post "What is 1234 plus 5678?"` → output contains `6912`
- [ ] `post "Echo back the phrase: hello world"` → contains `hello world`
- [ ] `post "Use confirm_action to confirm deleting old backups"` → mentions confirmation/execution; `docker compose logs py-mcp-server | grep -i elicitation` shows one roundtrip
- [ ] `post "Summarize this text using summarize_with_llm: The quick brown fox jumps over the lazy dog"` → contains `Mock summary:`
- [ ] `post "Run a long_running_task with 5 steps"` → contains `Completed 5 steps`; `docker compose logs py-mcp-server | grep -i Timing` shows completion
- [ ] `post "Run the log_demo tool"` → contains `All four log levels emitted`; `docker compose logs py-backend` shows `debug`, `info`, `warning`, `error` lines from the server
- [ ] `post "Call fail_tool and tell me what happened"` → agent explains the failure `This tool always fails on purpose`

### Task 4.5: Log regression sweep

- [ ] `docker compose logs py-mcp-server 2>&1 | grep -iE "(traceback|unexpected)" | grep -v "fail_tool"` → empty
- [ ] `docker compose logs py-backend 2>&1 | grep -iE "(traceback|5[0-9][0-9] internal|unhandled)"` → empty

---

## Phase 5 — End-to-end test matrix (TypeScript backend, port 8001)

Identical matrix to Phase 4, against `http://localhost:8001`.

### Task 5.1: Health + tools + resources + prompts

- [ ] `curl http://localhost:8001/health` → `{"status":"ok"}`
- [ ] `curl http://localhost:8001/tools | jq '.tools | length'` → `7`
- [ ] `curl http://localhost:8001/resources | jq '.resources | length, .templates | length'` → `2` and `1`
- [ ] `curl "http://localhost:8001/resources/res%3A%2F%2Fserver-info"` → valid JSON response
- [ ] `curl "http://localhost:8001/prompts/greet?name=Alice&style=shakespearean"` → `Hark! Alice, thou art most welcome.`

### Task 5.2: Chat — 7 agent scenarios (re-use the `post` helper against port 8001)

- [ ] Math `1234 + 5678` → `6912`
- [ ] Echo `hello world`
- [ ] Elicitation `confirm_action`
- [ ] Sampling `summarize_with_llm` → `Summary of:` (TS mock uses this prefix)
- [ ] Progress `long_running_task` with 5 steps
- [ ] Logging `log_demo` — all four levels in backend logs
- [ ] ToolError `fail_tool`

### Task 5.3: Log regression sweep

- [ ] `docker compose logs ts-mcp-server 2>&1 | grep -iE "(error|exception)" | grep -v "fail_tool"` → empty
- [ ] `docker compose logs ts-backend 2>&1 | grep -iE "(unhandled|uncaught)"` → empty

---

## Phase 6 — Multi-server smoke (optional but recommended)

Proves the new `RapidMCPClient` multi-server surface works end-to-end.

### Task 6.1: Scratch Python REPL

**Files:**
- Scratch: `D:\Trabajo\mcp-grpc\scratch_multi.py` (untracked)

- [ ] **Step 1: Write and run**

```python
import asyncio
from rapidmcp.integrations.langchain import RapidMCPClient

async def main():
    async with RapidMCPClient({
        "py": {"address": "localhost:50051"},
        "ts": {"address": "localhost:50052"},
    }) as rc:
        py_tools = await rc.get_tools("py")
        ts_tools = await rc.get_tools("ts")
        print(f"py: {len(py_tools)} tools  ts: {len(ts_tools)} tools")
        assert len(py_tools) == 7 and len(ts_tools) == 7, "expected 7 tools each"
        print("OK")

asyncio.run(main())
```

```bash
cd /d/Trabajo/mcp-grpc/python
uv run python /d/Trabajo/mcp-grpc/scratch_multi.py
```
Expected: `py: 7 tools  ts: 7 tools\nOK`.

- [ ] **Step 2: Delete scratch**

```bash
rm /d/Trabajo/mcp-grpc/scratch_multi.py
```

---

## Phase 7 — Docs and cleanup

### Task 7.1: Rewrite examples/README.md

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\examples\README.md`

- [ ] **Step 1: Header + quickstart**

Lead with a brief description, the repo-local install approach (`[tool.uv.sources]` + `file:../../../typescript`), and `docker compose up`. Remove any reference to building a wheel from the mcp-grpc parent directory — that step no longer applies since the repo is now the parent.

- [ ] **Step 2: Replace every `MCPToolkit` reference with `RapidMCPClient`**

The "Architecture notes" section currently says `MCPToolkit wraps tools only`. Rewrite:
> **`RapidMCPClient` exposes tools only via `get_tools()`.**
> Resources, prompts, and completions are accessed directly via `rc.client("<name>")` in the REST endpoints.

- [ ] **Step 3: Update test matrix commands**

The curl examples already use port 8000 / 8001 — confirm they match the compose file.

- [ ] **Step 4: Commit**

```bash
git add examples/README.md
git commit -m "docs(examples): RapidMCPClient + repo-local install"
```

### Task 7.2: Root CLAUDE.md — mention examples/

**Files:**
- Modify: `D:\Trabajo\mcp-grpc\CLAUDE.md`

- [ ] **Step 1: Add `examples/` to the project structure block**

Insert after `benchmark/`:
```
examples/                         ← E2E stack: FastAPI + NestJS backends using RapidMCPClient
  docker-compose.yml
  fastapi/{backend,mcp-server}
  nestjs/{backend,mcp-server}
```

- [ ] **Step 2: Add `## Examples stack` section mirroring the LangChain section**

One or two paragraphs explaining the stack, how to run it, and what it exercises. Keep it short — the real details live in `examples/README.md`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: mention examples/ E2E stack in CLAUDE.md"
```

### Task 7.3: Shut down

```bash
cd /d/Trabajo/mcp-grpc/examples
docker compose down
```

### Task 7.4: Remove the old mcp-test repo (manual)

Once the examples stack is committed and tests pass, the `D:\Trabajo\mcp-test\` repo is obsolete.

- [ ] **Step 1: Archive** (user decision — skip if not wanted)

Rename to `mcp-test.bak.2026-04-16/` or push a final "archived — superseded by mcp-grpc/examples" commit and leave it on the shelf.

- [ ] **Step 2: Or delete outright**

```bash
rm -rf /d/Trabajo/mcp-test/
```

---

## Phase 8 — Publish (after Phases 4 + 5 are green)

### Task 8.1: Publish Python wheel to PyPI

- [ ] **Step 1: Build**

```bash
cd /d/Trabajo/mcp-grpc/python
rm -rf dist/
uv build
```
Expected: `dist/rapidmcp-0.3.2-py3-none-any.whl` + sdist.

- [ ] **Step 2: Publish**

```bash
uv run --with twine twine upload dist/rapidmcp-0.3.2*
```
Expected: `pip index versions rapidmcp` shows `0.3.2`.

### Task 8.2: Publish TypeScript package to npm

- [ ] **Step 1: Build and publish**

```bash
cd /d/Trabajo/mcp-grpc/typescript
npm run build
npm publish --access public
```
Expected: `npm view @emitate/rapidmcp version` reports `0.1.4`.

### Task 8.3: Flip examples backends to registry installs

**Files:**
- Modify: `examples/fastapi/backend/pyproject.toml` (remove `[tool.uv.sources]`)
- Modify: `examples/fastapi/mcp-server/pyproject.toml` (remove `[tool.uv.sources]`)
- Modify: `examples/nestjs/backend/package.json` (`"@emitate/rapidmcp": "^0.1.4"`)
- Modify: `examples/nestjs/mcp-server/package.json` (same)
- Modify: all four Dockerfiles (drop the `COPY python/` / `COPY typescript/` and the `sed` override)
- Regenerate `uv.lock` and `package-lock.json` in both locations

- [ ] **Step 1: Swap source overrides for registry deps**

For each Python side, drop `[tool.uv.sources]` entirely. `[project].dependencies = ["...rapidmcp"]` now resolves from PyPI.

For each TS side, change `"file:../../../typescript"` to `"^0.1.4"`.

- [ ] **Step 2: Revert Dockerfiles to the minimal form**

```dockerfile
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml .
RUN uv sync --no-dev
COPY app.py .
CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```
And analogous for TS. Build context can shrink back to the backend directory itself — update `docker-compose.yml` `context:` accordingly.

- [ ] **Step 3: Regenerate lockfiles**

```bash
cd /d/Trabajo/mcp-grpc/examples/fastapi/backend && uv sync
cd /d/Trabajo/mcp-grpc/examples/fastapi/mcp-server && uv sync
cd /d/Trabajo/mcp-grpc/examples/nestjs/backend && npm install
cd /d/Trabajo/mcp-grpc/examples/nestjs/mcp-server && npm install
```

- [ ] **Step 4: Re-run Phases 3–5 against published artefacts**

Full rebuild + boot + E2E matrix. Expected: identical pass rate. This confirms the registry artefacts are byte-equivalent to the in-tree versions.

- [ ] **Step 5: Commit**

```bash
git add examples/ docker-compose.yml   # paths as updated
git commit -m "chore(examples): switch to published rapidmcp 0.3.2 / @emitate/rapidmcp 0.1.4"
```

---

## Exit criteria

All the following must be true before closing this plan:

1. `examples/` exists at repo root, mirroring the mcp-test layout.
2. Both backends build and start without errors (Phase 3).
3. All 7 `/chat` tool scenarios pass on both backends (14 cells total, Phases 4-5).
4. Resource lists, resource templates, and prompt rendering pass on both backends.
5. Multi-server smoke works (Phase 6).
6. No unexpected errors in server logs (only the intentional `fail_tool` error).
7. `examples/README.md` + root `CLAUDE.md` reference `RapidMCPClient`, not `MCPToolkit`.
8. The old `D:\Trabajo\mcp-test\` repo is archived or deleted.

## Roll-back plan

If critical regressions surface in Phase 3 or later:

1. `docker compose down -v` to stop.
2. Identify the offending phase; `git revert` its commit(s).
3. If the regression is in the library itself (not the examples), the fastest fix is to revert the library change and rebuild — the local-install path means no version bump is needed.

## Open questions

- **Should `examples/` have its own CI job?** A matrix step that runs Phases 4-5 against a compose-up stack would catch breakage in adapter changes before they ship. Out of scope for this plan.
- **Should the mcp-server services also be migrated to `RapidMCPClient`?** No — they're servers, not clients. They use `RapidMCP`, which is unchanged.
