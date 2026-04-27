#!/usr/bin/env python3
"""
XKB Infer Enqueuer
Submits request artifacts to the xkb-infer Minions queue.
The actual inference is done by a separate consumer (isolated agent turn).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2

GBRAIN_DB_URL = os.environ["GBRAIN_DATABASE_URL"]  # required: set in env, no fallback
INFER_QUEUE = "xkb-infer"


def enqueue_request(request_path: str, dry_run: bool = False) -> str:
    request_path = Path(request_path).resolve()
    if not request_path.exists():
        raise FileNotFoundError(f"Request file not found: {request_path}")

    with request_path.open(encoding="utf-8") as f:
        req = json.load(f)

    source_date = req.get("source_date", "")
    chunk_index = req.get("chunk_index", 0)
    request_kind = req.get("task") or req.get("kind") or "generic"
    request_identity = req.get("card_id") or req.get("bookmark_file") or req.get("meta", {}).get("request_kind") or chunk_index
    safe_identity = str(request_identity).replace('/', '_')
    job_name = f"xkb-infer:{request_kind}:{safe_identity}"
    idem = f"xkb-infer:{request_kind}:{safe_identity}"

    task = req.get("task", "")
    card_id = req.get("card_id") or ""
    if task == "bookmark_enrich_card" and card_id:
        result_file = str(request_path.parent.parent / "results" / f"bookmark-{card_id}.json")
    else:
        result_file = str(request_path.parent.parent / "results" / f"chunk-{chunk_index:04d}.json")

    job_data = {
        "kind": "xkb-infer",
        "request_file": str(request_path),
        "result_file": result_file,
        "source_date": source_date,
        "chunk_index": chunk_index,
        "model": req.get("meta", {}).get("model") or "",
    }

    if dry_run:
        print(f"[enqueuer] DRY_RUN: {job_name}", file=sys.stderr)
        return f"DRY_RUN {idem}"

    conn = psycopg2.connect(GBRAIN_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status FROM minion_jobs WHERE idempotency_key=%s ORDER BY created_at DESC LIMIT 1",
                (idem,),
            )
            existing = cur.fetchone()
            if existing:
                conn.commit()
                return f"SKIP #{existing[0]} {idem} ({existing[1]})"

            cur.execute(
                """
                INSERT INTO minion_jobs
                    (name, queue, data, max_attempts, timeout_ms, backoff_delay, idempotency_key)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    job_name,
                    INFER_QUEUE,
                    json.dumps(job_data),
                    3,          # max_attempts
                    300000,     # 5 min timeout_ms
                    30000,      # 30s backoff
                    idem,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return f"#{row[0]} {idem}" if row else f"SKIP {idem}"
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Enqueue XKB request artifacts to Minions queue")
    parser.add_argument("--request", required=True, help="Path to request artifact JSON")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    args = parser.parse_args()

    result = enqueue_request(args.request, args.dry_run)
    print(result)


if __name__ == "__main__":
    main()