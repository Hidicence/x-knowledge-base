#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

import sys
sys.path.insert(0, str(Path(__file__).parent))
import distill_memory_to_wiki as distill

GBRAIN_DB_URL = os.getenv("GBRAIN_DATABASE_URL", "postgresql://gbrain:REDACTED_ROTATE_THIS_PASSWORD@127.0.0.1:5432/gbrain")
QUEUE = "xkb-memory-distill-consolidate"
LOCK_DURATION_S = 600
POLL_INTERVAL_S = 15
_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    print("\nShutting down after current job...")
    _shutdown = True



def normalize_job_data(raw_data):
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, str):
        return json.loads(raw_data)
    raise TypeError(f"unsupported job.data type: {type(raw_data).__name__}")



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



def all_chunk_jobs_completed(conn, chunk_job_ids: list[int]) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT id, status FROM minion_jobs WHERE id = ANY(%s)", (chunk_job_ids,))
        rows = cur.fetchall()
    status_map = {row[0]: row[1] for row in rows}
    return all(status_map.get(job_id) == 'completed' for job_id in chunk_job_ids)



def load_result_insights(runtime_dir: Path) -> list[dict]:
    insights = []
    results_dir = runtime_dir / 'results'
    for path in sorted(results_dir.glob('chunk-*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('ok'):
                insights.extend(data.get('output', {}).get('insights', []))
        except Exception:
            continue
    return insights



def process_job(job, conn):
    data = normalize_job_data(job.get('data'))
    runtime_dir = Path(data['runtime_dir'])
    manifest_path = runtime_dir / 'manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(f'manifest not found: {manifest_path}')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    chunk_job_ids = manifest.get('chunk_job_ids', [])
    if chunk_job_ids and not all_chunk_jobs_completed(conn, chunk_job_ids):
        raise RuntimeError(f'chunk jobs not completed yet: {chunk_job_ids}')

    insights = load_result_insights(runtime_dir)
    reranked = distill.rerank_and_consolidate_insights(insights)
    staging_file = distill.write_staging(reranked, manifest['source_date'], label=manifest.get('label') or '')

    applied = 0
    skipped = 0
    slugs: list[str] = []
    if manifest.get('auto_apply_high'):
        applied, skipped, slugs = distill.apply_staging_file(staging_file, auto_approve_high=True)

    return {
        'status': 'done',
        'source_date': manifest['source_date'],
        'partial_count': len(insights),
        'insights_found': len(reranked),
        'staging_file': str(staging_file),
        'applied': applied,
        'skipped': skipped,
        'topics': slugs,
        'finished_at': datetime.now(timezone.utc).isoformat(),
    }



def run_worker(poll_interval=POLL_INTERVAL_S):
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    print(f"Memory Distill Consolidate Worker started (queue={QUEUE}, poll={poll_interval}s)")
    conn = psycopg2.connect(GBRAIN_DB_URL)
    conn.autocommit = False
    while not _shutdown:
        try:
            job = claim_job(conn)
        except Exception as e:
            print(f"DB error: {e}, reconnecting...")
            try:
                conn.close()
            except Exception:
                pass
            conn = psycopg2.connect(GBRAIN_DB_URL)
            conn.autocommit = False
            continue
        if not job:
            time.sleep(poll_interval)
            continue
        try:
            result = process_job(job, conn)
            complete_job(conn, job['id'], result)
            print(f"consolidate job #{job['id']} done ({result.get('insights_found', 0)} insights)", flush=True)
        except Exception:
            tb = traceback.format_exc()
            print(tb, flush=True)
            fail_job(conn, job['id'], tb, job.get('max_attempts', 3), job.get('attempts_made', 0), job.get('backoff_delay', 60000))
    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--poll-interval', type=int, default=POLL_INTERVAL_S)
    args = parser.parse_args()
    run_worker(args.poll_interval)
