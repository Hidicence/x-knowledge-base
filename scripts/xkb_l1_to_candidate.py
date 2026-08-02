#!/usr/bin/env python3
"""Rule-based, replay-safe L1 trace -> candidate distillation worker.

This worker deliberately performs no LLM call and no promotion.  It consumes
queued/pending ``distill`` jobs created by xkb_memory_service, annotates the
pending candidate with an auditable decision, and preserves trace/episode
provenance.  System-generated traces are rejected; all other traces remain
pending for later governance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from xkb_memory_service import Store, now, text

WORKER = "xkb_l1_to_candidate"
STAGE = "distill"
ANALYSIS_SCHEMA = "xkb-l1-candidate-analysis.v1"
NOISE_MARKERS = (
    "self_review_sent", "hn_digest_sent", "cron jobs executed", "cron_jobs_executed",
    "system echo", "heartbeat_ok", "heartbeat", "delivery", "cron:",
    "assistant: no_reply", "assistant turn failed before producing content",
    "image2_skill_autogrow_failed", "async command did not run",
)


def _blob(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()


def analyze(trace: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic analysis metadata; never mutate input records."""
    source_trace_ids = candidate.get("source_trace_ids", [])
    episode_ids = candidate.get("episode_ids", [])
    payload = trace.get("payload", trace)
    blob = _blob({"trace": trace, "candidate": candidate})
    matched = sorted({marker for marker in NOISE_MARKERS if marker in blob})
    if matched:
        decision, reason = "REJECT_SYSTEM_ECHO", "system-generated marker(s) detected"
    else:
        decision, reason = "HOLD", "rule-based analysis complete; human/promotion gate still required"
    fingerprint = hashlib.sha256(json.dumps({
        "trace": trace.get("trace_id"), "candidate": candidate.get("candidate_id"),
        "candidate_value": candidate.get("candidate_value", ""),
        "matched_markers": matched,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {
        "schema": ANALYSIS_SCHEMA,
        "worker": WORKER,
        "analyzer_version": "1",
        "analyzed_at": now(),
        "analysis_fingerprint": fingerprint,
        "decision": decision,
        "reason": reason,
        "noise_markers": matched,
        "source_trace_ids": source_trace_ids,
        "episode_ids": episode_ids,
        "provenance": {
            "trace_id": trace.get("trace_id"),
            "memory_layer": trace.get("memory_layer", "L1"),
            "observed_status": trace.get("status", "observed"),
            "candidate_id": candidate.get("candidate_id"),
            "candidate_key": candidate.get("candidate_key"),
        },
        "llm_used": False,
        "promotion_performed": False,
        "payload_sha256": hashlib.sha256(_blob(payload).encode()).hexdigest(),
    }


def _json(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def process_job(store: Store, job_id: str) -> dict[str, Any]:
    """Process one job atomically enough for local replay and concurrent runs."""
    with store.lock, store.connect() as db:
        job = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError(f"job not found: {job_id}")
        if job["stage"] != STAGE or job["worker"] != WORKER:
            raise ValueError("job is not an L1-to-candidate distillation job")
        if job["status"] not in {"queued", "pending", "running", "succeeded"}:
            raise ValueError(f"job is not processable: {job['status']}")
        trace_id = text(job["input_ref"])
        candidate_id = text(job["output_ref"])
        turn = db.execute("SELECT * FROM turns WHERE trace_id=?", (trace_id,)).fetchone()
        candidate = db.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if not turn or not candidate:
            raise ValueError("job input/output provenance could not be resolved")
        candidate_data = {
            "candidate_id": candidate["candidate_id"], "candidate_key": candidate["candidate_key"],
            "candidate_value": candidate["candidate_value"],
            "source_trace_ids": _json(candidate["source_trace_ids_json"], []),
            "episode_ids": _json(candidate["episode_ids_json"], []),
        }
        trace = {"trace_id": trace_id, "payload": _json(turn["payload_json"], {}), "memory_layer": "L1", "status": "observed"}
        analysis = analyze(trace, candidate_data)
        # A rerun writes the same logical result. Keep the original timestamp so
        # repeated execution does not create observable churn.
        previous = _json(candidate["analysis_json"] if "analysis_json" in candidate.keys() else None, {})
        if previous.get("analysis_fingerprint") == analysis["analysis_fingerprint"]:
            analysis = previous
        new_status = "rejected" if analysis["decision"] == "REJECT_SYSTEM_ECHO" else "pending"
        db.execute("UPDATE candidates SET status=?,reject_reasons_json=?,analysis_json=?,updated_at=? WHERE candidate_id=? AND status IN ('pending','rejected')",
                   (new_status, json.dumps([analysis["reason"]] if new_status == "rejected" else [], ensure_ascii=False), json.dumps(analysis, ensure_ascii=False), now(), candidate_id))
        metadata = _json(job["metadata_json"], {})
        metadata.update({"analysis": analysis, "source_trace_ids": candidate_data["source_trace_ids"], "candidate_ids": [candidate_id]})
        db.execute("UPDATE jobs SET status='succeeded',finished_at=COALESCE(finished_at,?),output_ref=?,error=NULL,metadata_json=?,updated_at=? WHERE job_id=?",
                   (now(), candidate_id, json.dumps(metadata, ensure_ascii=False), now(), job_id))
    return {"job_id": job_id, "status": "succeeded", "candidate_id": candidate_id, "decision": analysis["decision"], "analysis": analysis}


def fail_job(store: Store, job_id: str, exc: Exception) -> dict[str, Any]:
    with store.lock, store.connect() as db:
        db.execute("UPDATE jobs SET status='failed',finished_at=?,error=?,retryable=1,updated_at=? WHERE job_id=? AND status IN ('queued','pending','running')", (now(), f"{type(exc).__name__}: {exc}", now(), job_id))
    return {"job_id": job_id, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--job-id", action="append", help="Process specific job (repeatable)")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    store = Store(args.db or Path.home() / ".xkb-runtime" / "knowledge.sqlite")
    if args.job_id:
        ids = args.job_id
    else:
        with store.connect() as db:
            rows = db.execute("SELECT job_id FROM jobs WHERE stage=? AND worker=? AND status IN ('queued','pending') ORDER BY created_at LIMIT ?", (STAGE, WORKER, max(1, min(args.limit, 200)))).fetchall()
        ids = [row["job_id"] for row in rows]
    results = []
    for job_id in ids:
        try:
            results.append(process_job(store, job_id))
        except Exception as exc:
            results.append(fail_job(store, job_id, exc))
    print(json.dumps({"worker": WORKER, "processed": len(results), "results": results}, ensure_ascii=False))
    return 0 if all(item["status"] == "succeeded" for item in results) else (1 if results else 0)


if __name__ == "__main__":
    raise SystemExit(main())
