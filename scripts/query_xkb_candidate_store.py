#!/usr/bin/env python3
"""Read-only query and review layer for the Stage 2 candidate store."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def load_events(store: Path) -> list[dict]:
    events = []
    seen: dict[str, str] = {}
    for line_no, line in enumerate(store.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        event = json.loads(line)
        artifact_id = event["artifact_id"]
        artifact_sha = event["artifact_sha256"]
        if artifact_id in seen and seen[artifact_id] != artifact_sha:
            raise ValueError(f"immutable conflict at line {line_no}: {artifact_id}")
        if artifact_id not in seen:
            seen[artifact_id] = artifact_sha
            events.append(event)
    return events


def review_store(store: Path, candidate_key: str | None = None) -> dict:
    events = load_events(store)
    if candidate_key:
        events = [e for e in events if e["candidate_key"] == candidate_key]
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        groups[event["candidate_key"]].append(event)
    candidates = []
    for key, bucket in sorted(groups.items()):
        artifacts = [e["artifact"] for e in bucket]
        evidence = [ev for a in artifacts for ev in a.get("evidence", [])]
        eligible = [ev for ev in evidence if ev.get("induction_eligible") is True]
        episodes = {a["episode"]["episode_id"] for a in artifacts if a.get("episode")}
        provenance = sorted({a["provenance"]["evidence_type"] for a in artifacts})
        labels = sorted({a["decision"]["label"] for a in artifacts})
        candidates.append({
            "candidate_key": key,
            "artifact_count": len(artifacts),
            "distinct_episode_count": len(episodes),
            "eligible_evidence_count": len(eligible),
            "provenance_types": provenance,
            "decisions": labels,
            "review_gate": "REVIEW_READY" if len(episodes) >= 2 and len(eligible) >= 2 else "HOLD",
            "automatic_promotion": False,
            "artifacts": [a["artifact_id"] for a in artifacts],
        })
    return {
        "schema": "xkb-candidate-review.v1",
        "read_only": True,
        "automatic_promotion": False,
        "store": str(store),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("store", type=Path)
    parser.add_argument("--candidate-key")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = review_store(args.store.resolve(), args.candidate_key)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
