#!/usr/bin/env python3
"""Run one approved XKB worker while reporting a durable job history.

This first adapter is intentionally narrow: it runs the existing
``build_vector_index.py`` through ``subprocess`` without a shell, and reports
queued/running/succeeded/failed events to the local Knowledge Service. The
worker's arguments and exit semantics are unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "tools"))
from runtime_config import runtime_env

DEFAULT_WORKER = SCRIPT_DIR / "build_vector_index.py"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def post_event(service_url: str, event: dict) -> bool:
    if not service_url:
        return False
    request = Request(
        f"{service_url.rstrip('/')}/v1/pipeline/jobs/events",
        data=json.dumps(event, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return 200 <= response.status < 300
    except (OSError, URLError, ValueError):
        return False


def event(job_id: str, status: str, *, worker: str, started_at: str | None = None,
          finished_at: str | None = None, error: str = "", retryable: bool = False,
          input_ref: str = "", output_ref: str = "", metadata: dict | None = None) -> dict:
    payload = {
        "job_id": job_id,
        "stage": "index",
        "worker": worker,
        "status": status,
        "retryable": retryable,
        "metadata": metadata or {},
    }
    for key, value in {
        "started_at": started_at, "finished_at": finished_at, "error": error,
        "input_ref": input_ref, "output_ref": output_ref,
    }.items():
        if value:
            payload[key] = value
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run build_vector_index with XKB job reporting")
    parser.add_argument("--job-id", default=f"index:{uuid.uuid4()}")
    parser.add_argument("--service-url", default=os.getenv("XKB_MEMORY_SERVICE_URL", "http://127.0.0.1:18972"))
    parser.add_argument("--worker", type=Path, default=DEFAULT_WORKER)
    parser.add_argument("--env-file", help="dotenv file passed to the worker (process environment wins)")
    parser.add_argument("--dry-run", action="store_true", help="Report the job lifecycle without running the worker")
    parser.add_argument("worker_args", nargs=argparse.REMAINDER, help="Arguments passed to the worker after --")
    args = parser.parse_args(argv)
    worker = args.worker.resolve()
    if not worker.is_file():
        print(f"worker not found: {worker}", file=sys.stderr)
        return 2
    worker_args = list(args.worker_args)
    if worker_args and worker_args[0] == "--":
        worker_args.pop(0)
    started_at = timestamp()
    base = {"worker": worker.name, "input_ref": " ".join(worker_args), "metadata": {"argv": [str(worker), *worker_args]}}
    post_event(args.service_url, event(args.job_id, "queued", **base))
    if args.dry_run:
        post_event(args.service_url, event(args.job_id, "succeeded", finished_at=timestamp(), output_ref="dry-run", **base))
        print(json.dumps({"job_id": args.job_id, "status": "dry-run", "worker": str(worker)}, ensure_ascii=False))
        return 0
    child_env = runtime_env(args.env_file)
    post_event(args.service_url, event(args.job_id, "running", started_at=started_at, **base))
    command = [sys.executable, str(worker), *worker_args]
    try:
        completed = subprocess.run(command, check=False, cwd=str(SCRIPT_DIR.parent.parent), env=child_env)
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
        post_event(args.service_url, event(args.job_id, "failed", started_at=started_at, finished_at=timestamp(), error=error, retryable=True, **base))
        return 1
    if completed.returncode == 0:
        post_event(args.service_url, event(args.job_id, "succeeded", started_at=started_at, finished_at=timestamp(), output_ref="worker_exit:0", **base))
        return 0
    error = f"worker exited with code {completed.returncode}"
    post_event(args.service_url, event(args.job_id, "failed", started_at=started_at, finished_at=timestamp(), error=error, retryable=True, **base))
    return completed.returncode if completed.returncode > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
