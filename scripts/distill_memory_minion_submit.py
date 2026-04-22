#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
MEMORY_DIR = WORKSPACE / "memory"
GBRAIN_DB_URL = os.getenv("GBRAIN_DATABASE_URL", "postgresql://gbrain:REDACTED_ROTATE_THIS_PASSWORD@127.0.0.1:5432/gbrain")
QUEUE = "xkb-memory-distill"
DEFAULT_TIMEOUT_MS = 900000
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_DELAY_MS = 60000


def build_dates(days: int) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [str(today - timedelta(days=i)) for i in range(days)]



def parse_dates(days: int, explicit_date: str | None) -> list[str]:
    if explicit_date:
        return [explicit_date]
    return build_dates(days)



def submit_job(conn, payload: dict, timeout_ms: int, max_attempts: int, backoff_delay: int, dry_run: bool) -> str:
    source_date = payload["source_date"]
    label = payload.get("label") or "default"
    mode = "auto-apply-high" if payload.get("auto_apply_high") else "stage"
    idem = f"memory-distill:{source_date}:{label}:{mode}"

    if dry_run:
        return f"DRY_RUN {idem}"

    with conn.cursor() as cur:
        cur.execute("SELECT id, status FROM minion_jobs WHERE idempotency_key=%s ORDER BY created_at DESC LIMIT 1", (idem,))
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
                f"memory-distill:{source_date}",
                QUEUE,
                json.dumps(payload),
                max_attempts,
                timeout_ms,
                backoff_delay,
                idem,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return f"#{row[0]} {idem}" if row else f"SKIP {idem}"



def main() -> None:
    parser = argparse.ArgumentParser(description="Submit daily memory distill jobs to Minions")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--date", help="Explicit memory date YYYY-MM-DD")
    parser.add_argument("--label", default="")
    parser.add_argument("--auto-apply-high", action="store_true")
    parser.add_argument("--approve-all", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--backoff-delay", type=int, default=DEFAULT_BACKOFF_DELAY_MS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dates = [d for d in parse_dates(args.days, args.date) if (MEMORY_DIR / f"{d}.md").exists()]
    if not dates:
        print("No memory files found.")
        return

    conn = None if args.dry_run else psycopg2.connect(GBRAIN_DB_URL)
    if conn:
        conn.autocommit = False

    try:
        for date_str in dates:
            payload = {
                "kind": "memory_distill",
                "source_date": date_str,
                "memory_path": str(MEMORY_DIR / f"{date_str}.md"),
                "label": args.label,
                "auto_apply_high": args.auto_apply_high,
                "approve_all": args.approve_all,
            }
            status = submit_job(conn, payload, args.timeout_ms, args.max_attempts, args.backoff_delay, args.dry_run)
            print(status)
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main()
