"""Measure real queries and loopback HTTP on a separate, CPU-limited API process."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import json
import os
from pathlib import Path
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import psutil

from compact_market.store import MarketStore, SORTS


def percentiles(samples: list[float]) -> dict:
    values = sorted(samples)
    def p(n):
        return round(values[min(len(values) - 1, int((len(values) - 1) * n))], 3)
    return {"requests": len(values), "p50_ms": p(.5), "p95_ms": p(.95),
            "p99_ms": p(.99), "max_ms": round(max(values), 3)}


def benchmark(database: Path, output: Path, *, concurrency: int = 16,
              requests: int = 600, cores: int = 4) -> dict:
    rng = random.Random(20261029)
    parent = psutil.Process()
    before_rss = parent.memory_info().rss
    started = time.perf_counter()
    from compact_market.unified import UnifiedMarketStore
    store_class = UnifiedMarketStore if os.environ.get("COMPACT_ENGINE") == "unified" else MarketStore
    store = store_class(database)
    load_ms = (time.perf_counter() - started) * 1000
    loaded_rss = parent.memory_info().rss
    scenarios = []
    for scan_id in store.scans:
        rows = store.items(scan_id=scan_id, sort="listings_desc", page_size=100)["items"]
        for i, row in enumerate(rows):
            scenarios.extend([
                ("search", "items", {"scan_id": scan_id, "q": row["name"][:2 + i % 5],
                                       "sort": SORTS[i % len(SORTS)]}),
                ("list", "items", {"scan_id": scan_id, "page": 1 + i % 5,
                                     "sort": SORTS[i % len(SORTS)]}),
                ("detail", "listings", {"scan_id": scan_id, "market_key": row["marketKey"],
                                         "page": 1, "page_size": 25}),
                ("history", "history", {"scan_id": scan_id, "market_key": row["marketKey"]}),
            ])
    rng.shuffle(scenarios)
    direct = defaultdict(list)
    for kind, method, params in scenarios[:400]:
        start = time.perf_counter()
        getattr(store, method)(**params)
        direct[kind].append((time.perf_counter() - start) * 1000)
    direct_report = {k: percentiles(v) for k, v in direct.items()}
    store.close()

    # Choose a free local port; the server binds loopback only.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    available = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    affinity = available[:cores]
    env = os.environ.copy()
    env.update({"COMPACT_DATABASE": str(database.resolve()), "COMPACT_THREADS": "2",
                "COMPACT_MEMORY_LIMIT": os.getenv("COMPACT_MEMORY_LIMIT", "1GB")})
    command = [sys.executable, "-m", "uvicorn", "compact_market.api:app", "--host", "127.0.0.1",
               "--port", str(port), "--workers", "1", "--no-access-log"]
    if affinity:
        command = ["taskset", "-c", ",".join(map(str, affinity)), *command]
    peak_rss = [0]
    stop = threading.Event()
    samples = []
    def monitor(process):
        while not stop.wait(.02):
            try:
                rss = process.memory_info().rss
                peak_rss[0] = max(peak_rss[0], rss)
                samples.append(rss)
            except psutil.NoSuchProcess:
                return
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        monitored = psutil.Process(process.pid)
        monitor_thread = threading.Thread(target=monitor, args=(monitored,), daemon=True)
        monitor_thread.start()
        try:
            with httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
                deadline = time.monotonic() + 60
                while True:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    try:
                        response = client.get("/health")
                        if response.status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError("API startup timed out")
                    time.sleep(.1)
                startup_rss = monitored.memory_info().rss
                first = {}
                for kind, method, params in scenarios[:40]:
                    if kind not in first:
                        start = time.perf_counter()
                        response = client.get(f"/api/market/{method}", params=params)
                        response.raise_for_status()
                        first[kind] = round((time.perf_counter() - start) * 1000, 3)
                # Warm a mix of SQL and hot-summary paths; no result memoization is enabled.
                for _, method, params in scenarios[:40]:
                    client.get(f"/api/market/{method}", params=params).raise_for_status()
            async def load():
                semaphore = asyncio.Semaphore(concurrency)
                latencies = defaultdict(list)
                errors = []
                server_latencies = defaultdict(list)
                limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
                async with httpx.AsyncClient(base_url=base, timeout=60, trust_env=False, limits=limits) as client:
                    async def one(index):
                        kind, method, params = scenarios[index % len(scenarios)]
                        async with semaphore:
                            start = time.perf_counter()
                            try:
                                response = await client.get(f"/api/market/{method}", params=params)
                                response.raise_for_status()
                                payload = response.json()
                                if not isinstance(payload, dict):
                                    raise ValueError("Non-object response")
                                latencies[kind].append((time.perf_counter() - start) * 1000)
                                header = response.headers.get("server-timing", "")
                                if "dur=" in header:
                                    server_latencies[kind].append(float(header.split("dur=")[1]))
                            except Exception as exc:
                                errors.append({"kind": kind, "error": str(exc)})
                    start = time.perf_counter()
                    await asyncio.gather(*(one(i) for i in range(requests)))
                    seconds = time.perf_counter() - start
                return {"concurrency": concurrency, "total_requests": requests,
                        "elapsed_seconds": round(seconds, 3),
                        "requests_per_second": round(sum(map(len, latencies.values())) / seconds, 2),
                        "errors": errors,
                        "loopback_http": {k: percentiles(v) for k, v in latencies.items()},
                        "server_time": {k: percentiles(v) for k, v in server_latencies.items()}}
            http_report = asyncio.run(load())
            final_rss = monitored.memory_info().rss
        finally:
            stop.set()
            monitor_thread.join(timeout=2)
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    report = {
        "database": str(database), "database_bytes": database.stat().st_size,
        "host": {"cpu": __import__("platform").processor(), "logical_cpus": os.cpu_count(),
                 "server_cpu_affinity": affinity, "host_memory_bytes": psutil.virtual_memory().total,
                 "python": sys.version.split()[0]},
        "conditions": [
            "Real archived inputs, no synthetic row multiplication.",
            "A fresh server process; OS file cache was not flushed.",
            f"API restricted to at most four host logical CPUs, DuckDB threads=2 and memory_limit={env["COMPACT_MEMORY_LIMIT"]}.",
            "DuckDB memory_limit is not a process RSS limit; no 8GB cgroup limit was applied.",
            "Loopback HTTP includes JSON serialization and local network; excludes Internet latency.",
            "Steady-state query load excludes snapshot import and old/new generation overlap.",
            "History test contains only the archived scan points, not months of hourly samples.",
            "No response cache; immutable summaries and sort/search indexes are precomputed at startup.",
        ],
        "store_load_ms": round(load_ms, 3),
        "store_load_rss_delta_bytes": loaded_rss - before_rss,
        "direct_queries": direct_report, "first_http_request_ms": first,
        "server_startup_rss_bytes": startup_rss, "server_peak_rss_bytes": peak_rss[0],
        "server_final_rss_bytes": final_rss, **http_report,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/compact/market.duckdb"))
    parser.add_argument("--output", type=Path, default=Path("data/compact/benchmark.json"))
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--requests", type=int, default=600)
    args = parser.parse_args()
    if args.concurrency < 1 or args.requests < 1:
        parser.error("concurrency and requests must be positive")
    benchmark(args.database, args.output, concurrency=args.concurrency, requests=args.requests)


if __name__ == "__main__":
    main()
