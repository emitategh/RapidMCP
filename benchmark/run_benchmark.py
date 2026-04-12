"""
Latency benchmark: fastermcp vs FastMCP Streamable HTTP.

Sequential calls only — measures single-client round-trip time, not throughput.

Usage:
    cd benchmark
    uv run python run_benchmark.py
"""
from __future__ import annotations

import argparse
import asyncio
import math
import socket
import subprocess
import sys
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from fastermcp import Client as McpClient

GRPC_PORT = 50052
HTTP_PORT = 8001
HTTP_ENDPOINT = f"http://127.0.0.1:{HTTP_PORT}/mcp"
DEFAULT_CALLS = 1000
WARMUP = 50

BENCHMARK_DIR = Path(__file__).parent


# ── Readiness ─────────────────────────────────────────────────────────────────


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    """Poll until TCP port accepts connections or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Port {port} not ready after {timeout}s")


# ── Measurement helpers ────────────────────────────────────────────────────────


def _percentile(data: list[float], p: float) -> float:
    sorted_data = sorted(data)
    idx = max(0, min(int(len(sorted_data) * p / 100), len(sorted_data) - 1))
    return sorted_data[idx]


def _mean(data: list[float]) -> float:
    return sum(data) / len(data)


def _stdev(data: list[float]) -> float:
    avg = _mean(data)
    variance = sum((x - avg) ** 2 for x in data) / len(data)
    return math.sqrt(variance)


def _print_table(results: dict[str, list[float]]) -> None:
    header = (
        f"{'Transport':<16} {'p50':>8} {'p95':>8} {'p99':>8}"
        f" {'min':>8} {'max':>8} {'mean':>8} {'stdev':>8}"
    )
    print(f"\n{header}")
    print("-" * len(header))
    for name, latencies in results.items():
        row = (
            f"{name:<16}"
            f" {_percentile(latencies, 50):>7.2f}ms"
            f" {_percentile(latencies, 95):>7.2f}ms"
            f" {_percentile(latencies, 99):>7.2f}ms"
            f" {min(latencies):>7.2f}ms"
            f" {max(latencies):>7.2f}ms"
            f" {_mean(latencies):>7.2f}ms"
            f" {_stdev(latencies):>7.2f}ms"
        )
        print(row)
    print()


# ── Benchmarks ────────────────────────────────────────────────────────────────


async def _bench_grpc(n_calls: int) -> list[float]:
    latencies: list[float] = []
    async with McpClient(f"localhost:{GRPC_PORT}") as client:
        for _ in range(WARMUP):
            await client.call_tool("echo", {"text": "hello"})
        for _ in range(n_calls):
            t0 = time.perf_counter()
            await client.call_tool("echo", {"text": "hello"})
            latencies.append((time.perf_counter() - t0) * 1000)
    return latencies


async def _bench_fastmcp(n_calls: int) -> list[float]:
    latencies: list[float] = []
    async with streamablehttp_client(HTTP_ENDPOINT) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for _ in range(WARMUP):
                await session.call_tool("echo", arguments={"text": "hello"})
            for _ in range(n_calls):
                t0 = time.perf_counter()
                await session.call_tool("echo", arguments={"text": "hello"})
                latencies.append((time.perf_counter() - t0) * 1000)
    return latencies


# ── Orchestrator ──────────────────────────────────────────────────────────────


async def main(n_calls: int) -> None:
    procs: list[subprocess.Popen] = []
    scripts = ["grpc_server.py", "fastmcp_server.py"]
    try:
        for script in scripts:
            procs.append(subprocess.Popen(
                [sys.executable, str(BENCHMARK_DIR / script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            ))

        print("Waiting for servers to start...")
        try:
            _wait_for_port("127.0.0.1", GRPC_PORT)
            _wait_for_port("127.0.0.1", HTTP_PORT)
        except TimeoutError as e:
            print(f"\nERROR: {e}")
            for proc, name in zip(procs, scripts):
                stderr = proc.stderr.read().decode() if proc.stderr else ""
                if stderr.strip():
                    print(f"\n--- {name} ---")
                    print(stderr)
            raise SystemExit(1)

        print(
            f"Both servers ready. "
            f"Running {n_calls} calls each ({WARMUP} warm-up discarded)..."
        )

        print("\nBenchmarking fastermcp...")
        grpc_latencies = await _bench_grpc(n_calls)

        print("Benchmarking FastMCP HTTP...")
        http_latencies = await _bench_fastmcp(n_calls)

        _print_table({"fastermcp": grpc_latencies, "FastMCP HTTP": http_latencies})

    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="fastermcp vs FastMCP latency benchmark")
    parser.add_argument(
        "-n", "--calls", type=int, default=DEFAULT_CALLS,
        help=f"number of measured calls per transport (default: {DEFAULT_CALLS})",
    )
    args = parser.parse_args()
    asyncio.run(main(args.calls))
