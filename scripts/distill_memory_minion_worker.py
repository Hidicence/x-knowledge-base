#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent))
import distill_memory_to_wiki as distill

GBRAIN_DB_URL = os.getenv("GBRAIN_DATABASE_URL", "postgresql://gbrain:REDACTED_ROTATE_THIS_PASSWORD@127.0.0.1:5432/gbrain")
QUEUE = "xkb-memory-distill"
CHUNK_QUEUE = "xkb-memory-distill-chunk"
LOCK_DURATION_S = 900
POLL_INTERVAL_S = 15
RUNTIME_DIR = Path(os.getenv("XKB_DISTILL_RUNTIME_DIR", str(Path.home() / ".openclaw" / "workspace" / "skills" / "x-knowledge-base" / "runtime" / "memory-distill")))
_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    print("\nShutting down after current job...")
    _shutdown = True



def claim_job(conn):
    lock_token = str(uuid.uuid4())
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            UPDATE minion_jobs SET status='active', lock_token=%s,
                lock_until=NOW()+INTERVAL '{LOCK_DURATION_S} seconds',
                started_at=COALESCE(started_at, NOW()), attempts_started=attempts_started+1, updated_at=NOW()
            WHERE id=(SELECT id FROM minion_jobs WHERE queue=%s AND status='waiting'
                AND (delay_until IS NULL OR delay_until<=NOW())
                ORDER BY priority DESC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING *
            """,
            (lock_token, QUEUE),
        )
        conn.commit()
        row = cur.fetchone()
        return dict(row) if row else None



def complete_job(conn, job_id, result):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE minion_jobs SET status='completed', result=%s, finished_at=NOW(), updated_at=NOW(), attempts_made=attempts_made+1, lock_token=NULL, lock_until=NULL WHERE id=%s",
            (json.dumps(result), job_id),
        )
        conn.commit()



def fail_job(conn, job_id, error, max_attempts, attempts_made, backoff_delay):
    new_attempts = attempts_made + 1
    is_dead = new_attempts >= max_attempts
    new_status = "dead" if is_dead else "waiting"
    delay_ms = backoff_delay * (2 ** attempts_made) if not is_dead else 0
    delay_expr = f"NOW()+INTERVAL '{delay_ms} milliseconds'" if delay_ms else "NULL"
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE minion_jobs SET status=%s, error_text=%s, attempts_made=%s, finished_at=CASE WHEN %s THEN NOW() ELSE NULL END, delay_until={delay_expr}, updated_at=NOW(), lock_token=NULL, lock_until=NULL WHERE id=%s",
            (new_status, error[:2000], new_attempts, is_dead, job_id),
        )
        conn.commit()



def normalize_job_data(raw_data):
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, str):
        return json.loads(raw_data)
    if raw_data is None:
        raise ValueError("job.data is null")
    raise TypeError(f"unsupported job.data type: {type(raw_data).__name__}")



def create_chunk_jobs(conn, source_date: str, runtime_dir: Path, chunks: list[str], topics_str: str) -> list[int]:
    job_ids: list[int] = []
    with conn.cursor() as cur:
        for idx, chunk in enumerate(chunks, start=1):
            chunk_file = f"chunk-{idx:04d}.txt"
            (runtime_dir / chunk_file).write_text(chunk, encoding="utf-8")
            payload = {
                "kind": "memory_distill_chunk",
                "source_date": source_date,
                "runtime_dir": str(runtime_dir),
                "chunk_index": idx,
                "chunk_file": chunk_file,
                "topics_str": topics_str,
            }
            cur.execute(
                """
                INSERT INTO minion_jobs (name, queue, data, max_attempts, timeout_ms, backoff_delay, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    f"memory-distill-chunk:{source_date}:{idx}",
                    CHUNK_QUEUE,
                    json.dumps(payload),
                    3,
                    300000,
                    60000,
                    f"memory-distill-chunk:{source_date}:{idx}",
                ),
            )
            row = cur.fetchone()
            if row:
                job_ids.append(row[0])
        conn.commit()
    return job_ids



def create_consolidate_job(conn, source_date: str, runtime_dir: Path) -> int | None:
    payload = {
        "kind": "memory_distill_consolidate",
        "source_date": source_date,
        "runtime_dir": str(runtime_dir),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO minion_jobs (name, queue, data, max_attempts, timeout_ms, backoff_delay, idempotency_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                f"memory-distill-consolidate:{source_date}",
                "xkb-memory-distill-consolidate",
                json.dumps(payload),
                6,
                300000,
                60000,
                f"memory-distill-consolidate:{source_date}",
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None



def process_job(job, conn):
    data = normalize_job_data(job.get("data"))
    print(f"[worker] job_id={job.get('id')} data_type={type(job.get('data')).__name__} normalized_type={type(data).__name__}", flush=True)
    memory_path = Path(data["memory_path"])
    print(f"[worker] source_date={data['source_date']} memory_path={memory_path}", flush=True)
    if not memory_path.exists():
        return {"status": "skipped", "reason": "file_not_found", "memory_path": str(memory_path)}

    content = memory_path.read_text(encoding="utf-8")
    cleaned = distill.extract_conversation_content(content)
    print(f"[worker] source_date={data['source_date']} raw_chars={len(content)} cleaned_chars={len(cleaned)}", flush=True)
    chunks = [cleaned[i:i + distill.CHUNK_SIZE] for i in range(0, max(len(cleaned), 1), distill.CHUNK_SIZE) if cleaned[i:i + distill.CHUNK_SIZE].strip()]
    runtime_dir = RUNTIME_DIR / data["source_date"]
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "cleaned.txt").write_text(cleaned, encoding="utf-8")
    topics = distill.load_topic_slugs()
    topics_str = "\n".join(f"- {s}" for s in topics) if topics else "(no topics yet)"
    chunk_job_ids = create_chunk_jobs(conn, data["source_date"], runtime_dir, chunks, topics_str)
    consolidate_job_id = create_consolidate_job(conn, data["source_date"], runtime_dir)
    manifest = {
        "source_date": data["source_date"],
        "memory_path": str(memory_path),
        "cleaned_chars": len(cleaned),
        "chunk_count": len(chunks),
        "chunk_job_ids": chunk_job_ids,
        "consolidate_job_id": consolidate_job_id,
        "label": data.get("label") or "",
        "auto_apply_high": bool(data.get("auto_apply_high")),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (runtime_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[worker] source_date={data['source_date']} chunk_jobs={len(chunk_job_ids)} consolidate_job_id={consolidate_job_id} runtime_dir={runtime_dir}", flush=True)

    return {
        "status": "chunk_jobs_submitted",
        "source_date": data["source_date"],
        "cleaned_chars": len(cleaned),
        "chunk_count": len(chunks),
        "chunk_job_ids": chunk_job_ids,
        "consolidate_job_id": consolidate_job_id,
        "runtime_dir": str(runtime_dir),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }



def run_worker(poll_interval=POLL_INTERVAL_S):
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    print(f"Memory Distill Minion Worker started (queue={QUEUE}, poll={poll_interval}s)")
    conn = psycopg2.connect(GBRAIN_DB_URL)
    conn.autocommit = False
    idle = 0
    while not _shutdown:
        try:
            job = claim_job(conn)
        except Exception as e:
            print(f"DB error: {e}, reconnecting...")
            try:
                conn.close()
            except Exception:
                pass
            time.sleep(5)
            conn = psycopg2.connect(GBRAIN_DB_URL)
            conn.autocommit = False
            continue

        if not job:
            if idle == 0:
                print(f"  idle, polling every {poll_interval}s...")
            idle += 1
            time.sleep(poll_interval)
            continue

        idle = 0
        job_id = job["id"]
        source_date = job.get("data", {}).get("source_date", "?")
        print(f"  Job #{job_id} [{source_date}]", end="  ", flush=True)
        try:
            result = process_job(job, conn)
            complete_job(conn, job_id, result)
            print(f"done ({result.get('insights_found', 0)} insights)", flush=True)
        except Exception as exc:
            tb = traceback.format_exc()
            print(f"FAIL: {str(exc)[:120]}", flush=True)
            print(tb, flush=True)
            try:
                fail_job(conn, job_id, tb, job.get("max_attempts", 3), job.get("attempts_made", 0), job.get("backoff_delay", 60000))
            except Exception as e2:
                print(f"  failed to update status: {e2}", flush=True)

    conn.close()
    print("Worker stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL_S)
    args = parser.parse_args()
    run_worker(args.poll_interval)
