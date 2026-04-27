#!/usr/bin/env python3
"""XKB Minion Worker"""
from __future__ import annotations
import argparse, json, os, re, signal, sys, time, uuid
from pathlib import Path
import psycopg2, psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent))
from _card_prompt import build_prompt, find_related_context, llm_call as _llm_call, gbrain_put as _gbrain_put

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(WORKSPACE / "memory" / "cards")))
GBRAIN_DB_URL = os.environ["GBRAIN_DATABASE_URL"]  # required: set in env, no fallback
XKB_QUEUE = "xkb"
LOCK_DURATION_S = 300
POLL_INTERVAL_S = 15
_shutdown = False

def _handle_signal(sig, frame):
    global _shutdown
    print(f"\n Shutting down after current job...")
    _shutdown = True

def _get_api_key():
    for k in ("LLM_API_KEY", "MINIMAX_API_KEY"):
        v = os.environ.get(k, "")
        if v: return v
    cfg = Path(os.environ.get("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
    if cfg.exists():
        try:
            d = json.loads(cfg.read_text())
            e = d.get("env", {})
            return e.get("LLM_API_KEY") or e.get("MINIMAX_API_KEY") or ""
        except: pass
    return ""

def _call_llm(api_key, content, card_id, source_url, category):
    prompt = build_prompt(
        content=f"Process this bookmark. If low-value output only: SKIPPED\n\n--- Raw ---\n{content[:4000]}\n---",
        card_id=card_id, source_type="x-bookmark", source_url=source_url, category=category,
        related_context=find_related_context(content, []) or "no related context",
    )
    return _llm_call(prompt, api_key, max_tokens=2500)

def claim_job(conn):
    lock_token = str(uuid.uuid4())
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"""
            UPDATE minion_jobs SET status=\'active\', lock_token=%s,
                lock_until=NOW()+INTERVAL \'{LOCK_DURATION_S} seconds\',
                started_at=COALESCE(started_at,NOW()), attempts_started=attempts_started+1, updated_at=NOW()
            WHERE id=(SELECT id FROM minion_jobs WHERE queue=%s AND status=\'waiting\'
                AND (delay_until IS NULL OR delay_until<=NOW())
                ORDER BY priority DESC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING *""", (lock_token, XKB_QUEUE))
        conn.commit()
        row = cur.fetchone()
        return dict(row) if row else None

def complete_job(conn, job_id, result):
    with conn.cursor() as cur:
        cur.execute("UPDATE minion_jobs SET status=\'completed\', result=%s, finished_at=NOW(), updated_at=NOW(), attempts_made=attempts_made+1, lock_token=NULL, lock_until=NULL WHERE id=%s",
            (json.dumps(result), job_id))
        conn.commit()

def fail_job(conn, job_id, error, max_attempts, attempts_made, backoff_delay):
    new_attempts = attempts_made + 1
    is_dead = new_attempts >= max_attempts
    new_status = "dead" if is_dead else "waiting"
    delay_ms = backoff_delay * (2 ** attempts_made) if not is_dead else 0
    delay_expr = f"NOW()+INTERVAL \'{delay_ms} milliseconds\'" if delay_ms else "NULL"
    with conn.cursor() as cur:
        cur.execute(f"UPDATE minion_jobs SET status=%s, error_text=%s, attempts_made=%s, finished_at=CASE WHEN %s THEN NOW() ELSE NULL END, delay_until={delay_expr}, updated_at=NOW(), lock_token=NULL, lock_until=NULL WHERE id=%s",
            (new_status, error[:1000], new_attempts, is_dead, job_id))
        conn.commit()

def process_job(job, api_key):
    data = job["data"]
    card_id = data["card_id"]
    filepath = Path(data["filepath"])
    if not filepath.exists(): return {"status": "skipped", "reason": "file_not_found"}
    content = filepath.read_text(encoding="utf-8", errors="ignore")
    text = _call_llm(api_key, content, card_id, data.get("source_url",""), data.get("category",""))
    if not text: raise RuntimeError("LLM empty response")
    if re.match(r"^SKIPPED", text.strip(), re.IGNORECASE): return {"status":"skipped","reason":"low_value"}
    card_path = CARDS_DIR / f"{card_id}.md"
    card_path.write_text(text, encoding="utf-8")
    _gbrain_put(card_path, card_id)
    return {"status":"done","card_id":card_id}

def run_worker(poll_interval=POLL_INTERVAL_S):
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    api_key = _get_api_key()
    if not api_key:
        print("LLM_API_KEY not found"); sys.exit(1)
    print(f"XKB Minion Worker started (queue={XKB_QUEUE}, poll={poll_interval}s)")
    conn = psycopg2.connect(GBRAIN_DB_URL)
    conn.autocommit = False
    idle = 0
    while not _shutdown:
        try: job = claim_job(conn)
        except Exception as e:
            print(f"DB error: {e}, reconnecting...")
            try: conn.close()
            except: pass
            time.sleep(5)
            conn = psycopg2.connect(GBRAIN_DB_URL)
            conn.autocommit = False
            continue
        if not job:
            if idle == 0: print(f"  idle, polling every {poll_interval}s...")
            idle += 1
            time.sleep(poll_interval)
            continue
        idle = 0
        job_id = job["id"]
        card_id = job.get("data",{}).get("card_id","?")
        print(f"  Job #{job_id} [{card_id}]", end="  ", flush=True)
        try:
            result = process_job(job, api_key)
            complete_job(conn, job_id, result)
            _s = result.get("status", ""); print(f"done ({_s})")
        except Exception as exc:
            print(f"FAIL: {str(exc)[:80]}")
            try: fail_job(conn, job_id, str(exc), job.get("max_attempts",2), job.get("attempts_made",0), job.get("backoff_delay",5000))
            except Exception as e2: print(f"  failed to update status: {e2}")
    conn.close()
    print("Worker stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL_S)
    args = parser.parse_args()
    run_worker(args.poll_interval)
