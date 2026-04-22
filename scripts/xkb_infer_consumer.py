#!/usr/bin/env python3
"""
XKB Infer Consumer
Claims jobs from xkb-infer queue and runs xkb_run_request.py synchronously.
This avoids detached shell state, .running markers, and polling races.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg2

GBRAIN_DB_URL = os.getenv("GBRAIN_DATABASE_URL", "postgresql://gbrain:REDACTED_ROTATE_THIS_PASSWORD@127.0.0.1:5432/gbrain")
INFER_QUEUE = "xkb-infer"
LOCK_TTL_SECONDS = 900
POLL_INTERVAL_SECONDS = 5
SCRIPT_DIR = Path(__file__).resolve().parent
RUNNER_PATH = SCRIPT_DIR / "xkb_run_request.py"


def claim_job(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, data FROM minion_jobs
            WHERE queue = %s
              AND status = 'waiting'
              AND (delay_until IS NULL OR delay_until <= NOW())
              AND (lock_until IS NULL OR lock_until <= NOW())
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            (INFER_QUEUE,),
        )
        row = cur.fetchone()
        if not row:
            return None

        job_id, raw_data = row
        data = raw_data if isinstance(raw_data, dict) else (json.loads(raw_data) if raw_data else {})

        cur.execute(
            """
            UPDATE minion_jobs
            SET status = 'active',
                lock_token = gen_random_uuid()::text,
                lock_until = NOW() + INTERVAL '%s seconds',
                attempts_started = attempts_started + 1,
                started_at = COALESCE(started_at, NOW()),
                updated_at = NOW()
            WHERE id = %s
            RETURNING lock_token
            """,
            (LOCK_TTL_SECONDS, job_id),
        )
        token_row = cur.fetchone()
        conn.commit()
        return {"id": job_id, "token": token_row[0] if token_row else None, "data": data}


def release_job(conn, job_id: int, error_text: str | None = None):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE minion_jobs
            SET status = 'waiting',
                lock_until = NOW() + INTERVAL '30 seconds',
                lock_token = NULL,
                attempts_made = attempts_made + 1,
                error_text = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (error_text, job_id),
        )
        conn.commit()


def complete_job(conn, job_id: int, result_data: dict | None = None):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE minion_jobs
            SET status = 'completed',
                finished_at = NOW(),
                updated_at = NOW(),
                lock_token = NULL,
                lock_until = NULL,
                result = %s
            WHERE id = %s
            """,
            (json.dumps(result_data or {}), job_id),
        )
        conn.commit()


def run_request(request_file: str, model: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    cmd = [
        "python3",
        str(RUNNER_PATH),
        "--request",
        request_file,
        "--model",
        model,
        "--timeout",
        str(timeout_seconds),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds + 15)


def main():
    print(f"[consumer] starting sync runner mode (queue={INFER_QUEUE}, lock_ttl={LOCK_TTL_SECONDS}s)", file=sys.stderr)

    conn = psycopg2.connect(GBRAIN_DB_URL)
    conn.autocommit = False

    idle_count = 0
    try:
        while True:
            job = claim_job(conn)
            if job is None:
                idle_count += 1
                if idle_count >= 3:
                    time.sleep(POLL_INTERVAL_SECONDS)
                continue

            idle_count = 0
            job_id = job["id"]
            data = job["data"]

            request_file = data.get("request_file", "")
            result_file = data.get("result_file", "")
            model = data.get("model") or "MiniMax-M2.7"
            timeout_seconds = max(60, min(LOCK_TTL_SECONDS - 30, 600))
            result_path = Path(result_file)

            print(f"[consumer] job #{job_id} request={request_file} model={model}", file=sys.stderr)

            if not Path(request_file).exists():
                release_job(conn, job_id, f"Request file not found: {request_file}")
                continue

            if result_path.exists():
                try:
                    existing = json.loads(result_path.read_text(encoding="utf-8"))
                    if existing.get("version") == "xkb.result.v1":
                        print(f"[consumer] job #{job_id} result already exists, skipping", file=sys.stderr)
                        complete_job(conn, job_id, {"result_file": result_file, "status": existing.get("ok")})
                        continue
                except Exception:
                    pass

            result_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                proc = run_request(request_file, model, timeout_seconds)
                if proc.stdout.strip():
                    print(proc.stdout.strip(), file=sys.stderr)
                if proc.stderr.strip():
                    print(proc.stderr.strip(), file=sys.stderr)
                if proc.returncode != 0:
                    raise RuntimeError(f"runner exited {proc.returncode}")
                if not result_path.exists():
                    raise RuntimeError(f"runner completed without result file: {result_path}")

                payload = json.loads(result_path.read_text(encoding="utf-8"))
                print(
                    f"[consumer] job #{job_id} done ok={payload.get('ok')} insights={len(payload.get('output', {}).get('insights', []))}",
                    file=sys.stderr,
                )
                complete_job(conn, job_id, {"result_file": result_file, "ok": payload.get("ok", False)})
            except subprocess.TimeoutExpired:
                release_job(conn, job_id, error_text=f"TimeoutExpired: exceeded {timeout_seconds}s")
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
                print(f"[consumer] job #{job_id} failed: {error}", file=sys.stderr)
                release_job(conn, job_id, error_text=error)

    except KeyboardInterrupt:
        print("[consumer] interrupted", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
