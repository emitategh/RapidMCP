# Docker Stress Tests

`test_stress_subprocess.py` runs the gRPC server inside a Docker container so client
and server have completely separate processes, event loops, and grpc.aio thread pools,
communicating over real TCP sockets.

This catches deadlock classes that same-process tests miss:
- **TCP backpressure** — client receive buffer fills while server writes
- **grpc.aio thread races** — each side has its own C completion queue
- **Sampling round-trips under real network latency/buffering**

## Requirements

- Docker daemon running with `docker` CLI on PATH
- Image built once before running:

```bash
cd python
docker build -t rapidmcp-test-server .
```

## How it works

`_DockerServer` is a context manager that:
1. Runs `docker run --rm --detach -p {free_host_port}:50051 rapidmcp-test-server tests/servers/script.py 50051`
2. Polls TCP with a **double-probe** (two successful connections ~300ms apart) — the first proves the port is bound, the second ensures grpc.aio has finished its HTTP/2 initialisation
3. Calls `docker stop` on exit regardless of test outcome

The pytest process acts as the gRPC client directly; the server runs in an isolated container.

## Tests

| Test | What it catches |
|------|----------------|
| `test_docker_concurrent_tool_calls` | 50 concurrent calls — write queue saturation |
| `test_docker_concurrent_clients` | 20 simultaneous clients — per-session resource contention |
| `test_docker_concurrent_sampling_no_deadlock` | sampling futures resolved by a different task than the waiter |
| `test_docker_fast_not_blocked_by_slow` | head-of-line blocking — fast tool must not wait for slow tool |
| `test_docker_pending_requests_no_leak` | 200 sequential calls — `PendingRequests` dict must be empty after all calls |

All tests wrap the client side in `asyncio.timeout()` so a deadlock surfaces as `TimeoutError`
rather than a hung test runner.

## Server scripts

Located in `tests/servers/`:

| Script | Tools registered |
|--------|-----------------|
| `echo.py` | `echo(text)` — returns text after a 5ms sleep |
| `mixed.py` | `slow(x)` — 500ms sleep, `fast(x)` — immediate return |
| `sampling.py` | `do_sample(n)` — performs a sampling round-trip via `ctx.sample()` |

## Running

```bash
cd python
uv run pytest tests/test_stress_subprocess.py -v
```
