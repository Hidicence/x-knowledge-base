#!/usr/bin/env python3
"""Convert a read-only replay report into Stage 2 candidate artifacts.

This is an append-only/reporting tool. It never promotes candidates and never
writes MEMORY.md, wiki topics, cards, or production indexes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "xkb-candidate-artifact.v1"
DECISION_TO_STATUS = {
    "REVIEW_READY": "review_ready",
    "HOLD": "hold",
    "REJECT_SYSTEM_ECHO": "rejected",
    "EXPIRED": "expired",
}


def slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9._:-]+", "_", value.lower()).strip("_")
    return value[:200] or "unknown"


def source_kind(path: str) -> str:
    if "/_staging/" in path:
        return "wiki_staging"
    if "/dreaming/" in path:
        return "dreaming"
    if path.startswith("memory/"):
        return "daily_memory"
    return "external_source"


def evidence_role(item: dict) -> str:
    if item.get("evidence_type") == "system":
        return "system_echo"
    if item.get("evidence_type") in {"pipeline_instruction", "assistant_output"}:
        return "context"
    return "support"


def convert_candidate(item: dict, generated_at: str) -> dict:
    decision = item["decision"]
    evidence = []
    episode_ids = []
    for index, raw in enumerate(item.get("evidence", []), start=1):
        episode_id = raw.get("episode_id")
        if episode_id:
            episode_ids.append(episode_id)
        excerpt = raw.get("excerpt", "")
        evidence.append({
            "evidence_id": f"{item['candidate_key']}:e{index}",
            "excerpt": excerpt,
            "evidence_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "induction_eligible": bool(raw.get("induction_eligible", False)),
            "role": evidence_role(raw),
        })
    first = item.get("evidence", [{}])[0]
    episode = None
    if episode_ids:
        episode = {"episode_id": episode_ids[0], "id_kind": "episode_id"}
    key_hash = hashlib.sha256(item["candidate_key"].encode("utf-8")).hexdigest()[:12]
    artifact = {
        "schema": SCHEMA,
        "artifact_id": f"candidate:{slug(item['candidate_key'])}:{key_hash}",
        "candidate_key": item["candidate_key"],
        "candidate_label": item.get("candidate_label", ""),
        "status": DECISION_TO_STATUS.get(decision, "hold"),
        "provenance": {
            "source_kind": source_kind(first.get("path", "")),
            "evidence_type": first.get("evidence_type", "conversation_or_note"),
            "source_path": first.get("path", ""),
            "source_sha256": first.get("sha256", "0" * 64),
            "captured_at": generated_at,
        },
        "episode": episode,
        "evidence": evidence or [{
            "evidence_id": f"{item['candidate_key']}:none",
            "excerpt": "No evidence excerpt captured",
            "evidence_sha256": hashlib.sha256(b"").hexdigest(),
            "induction_eligible": False,
            "role": "context",
        }],
        "aggregate": {
            "eligible_episode_count": item.get("eligible_episode_count", 0),
            "eligible_evidence_count": item.get("eligible_evidence_count", 0),
            "system_evidence_count": item.get("system_evidence", 0),
            "confidence": item.get("confidence", 0),
        },
        "decision": {
            "label": decision if decision in DECISION_TO_STATUS else "HOLD",
            "reason": item.get("reason", "No decision reason recorded"),
            "automatic_promotion": False,
        },
    }
    return artifact


def convert_report(report_path: Path, out_dir: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc).isoformat()
    batch = out_dir / f"batch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    batch.mkdir(parents=True, exist_ok=False)
    written = []
    for item in report.get("candidates", []):
        artifact = convert_candidate(item, generated_at)
        # Candidate labels can normalize to the same filesystem slug. Keep a
        # short hash suffix so every artifact remains lossless and rerunnable.
        key_hash = hashlib.sha256(item["candidate_key"].encode("utf-8")).hexdigest()[:12]
        path = batch / f"{slug(item['candidate_key'])}-{key_hash}.json"
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(str(path))
    manifest = {
        "schema": "xkb-candidate-artifact-batch.v1",
        "generated_at": generated_at,
        "read_only": True,
        "automatic_promotion": False,
        "input_report": str(report_path),
        "artifact_count": len(written),
        "artifacts": [str(Path(p).relative_to(batch)) for p in written],
    }
    (batch / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"batch": str(batch), "artifact_count": len(written), "manifest": str(batch / "manifest.json")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(convert_report(args.report.resolve(), args.out_dir.resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
