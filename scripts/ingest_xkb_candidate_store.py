#!/usr/bin/env python3
"""Ingest validated candidate artifacts into an append-only JSONL store.

The store is an isolated Stage 2 research artifact. It has no promotion path and
never updates MEMORY.md, wiki topics, cards, or production indexes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_VALIDATOR_PATH = _SCRIPT_DIR / "validate_xkb_candidate_artifacts.py"
_VALIDATOR_SPEC = importlib.util.spec_from_file_location("xkb_artifact_validator", _VALIDATOR_PATH)
assert _VALIDATOR_SPEC and _VALIDATOR_SPEC.loader
validator = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(validator)

STORE_SCHEMA = "xkb-candidate-store-event.v1"


def canonical_bytes(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def load_seen(store_path: Path) -> dict[str, str]:
    seen: dict[str, str] = {}
    if not store_path.exists():
        return seen
    for line_no, line in enumerate(store_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            seen[event["artifact_id"]] = event["artifact_sha256"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(f"invalid store event at line {line_no}: {exc}") from exc
    return seen


def ingest_batch(batch: Path, store_path: Path) -> dict:
    check = validator.validate_batch(batch)
    if not check["valid"]:
        raise ValueError("batch validation failed: " + "; ".join(check["errors"][:5]))
    store_path.parent.mkdir(parents=True, exist_ok=True)
    seen = load_seen(store_path)
    added = 0
    skipped = 0
    events: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for artifact_path in sorted(p for p in batch.glob("*.json") if p.name != "manifest.json"):
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact_id = artifact["artifact_id"]
        artifact_sha = hashlib.sha256(canonical_bytes(artifact)).hexdigest()
        if artifact_id in seen:
            if seen[artifact_id] != artifact_sha:
                raise ValueError(f"immutable conflict for {artifact_id}")
            skipped += 1
            continue
        event = {
            "schema": STORE_SCHEMA,
            "event": "candidate_observed",
            "observed_at": now,
            "artifact_id": artifact_id,
            "artifact_sha256": artifact_sha,
            "status": artifact["status"],
            "candidate_key": artifact["candidate_key"],
            "automatic_promotion": False,
            "artifact": artifact,
        }
        events.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
        seen[artifact_id] = artifact_sha
        added += 1
    if events:
        with store_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(events) + "\n")
    return {"store": str(store_path), "added": added, "skipped_existing": skipped, "total_seen": len(seen), "read_only_production": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(ingest_batch(args.batch.resolve(), args.store.resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
