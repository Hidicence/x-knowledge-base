#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ingest_xkb_candidate_store.py"
spec = importlib.util.spec_from_file_location("store", SCRIPT)
assert spec and spec.loader
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


def make_artifact(key: str = "candidate_one") -> dict:
    return {
        "schema": "xkb-candidate-artifact.v1",
        "artifact_id": f"candidate:{key}",
        "candidate_key": key,
        "status": "hold",
        "provenance": {
            "source_kind": "daily_memory", "evidence_type": "conversation_or_note",
            "source_path": "memory/2026-08-01.md", "source_sha256": "a" * 64,
        },
        "evidence": [{
            "evidence_id": "e1", "excerpt": "evidence", "evidence_sha256":
            hashlib.sha256(b"evidence").hexdigest(), "induction_eligible": False, "role": "context",
        }],
        "decision": {"label": "HOLD", "reason": "fixture", "automatic_promotion": False},
    }


class CandidateStoreTests(unittest.TestCase):
    def test_first_ingest_then_idempotent_reingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); batch = root / "batch"; batch.mkdir()
            artifact = make_artifact()
            (batch / "a.json").write_text(json.dumps(artifact), encoding="utf-8")
            (batch / "manifest.json").write_text(json.dumps({
                "artifact_count": 1, "read_only": True, "automatic_promotion": False,
            }), encoding="utf-8")
            store_path = root / "store.jsonl"
            first = store.ingest_batch(batch, store_path)
            second = store.ingest_batch(batch, store_path)
            self.assertEqual(first["added"], 1)
            self.assertEqual(second["added"], 0)
            self.assertEqual(second["skipped_existing"], 1)
            self.assertEqual(len(store_path.read_text().splitlines()), 1)

    def test_mutation_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); batch = root / "batch"; batch.mkdir()
            artifact = make_artifact()
            p = batch / "a.json"; p.write_text(json.dumps(artifact), encoding="utf-8")
            (batch / "manifest.json").write_text(json.dumps({
                "artifact_count": 1, "read_only": True, "automatic_promotion": False,
            }), encoding="utf-8")
            store_path = root / "store.jsonl"
            store.ingest_batch(batch, store_path)
            artifact["candidate_key"] = "mutated"
            p.write_text(json.dumps(artifact), encoding="utf-8")
            with self.assertRaises(ValueError):
                store.ingest_batch(batch, store_path)

    def test_store_event_has_no_promotion_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); batch = root / "batch"; batch.mkdir()
            (batch / "a.json").write_text(json.dumps(make_artifact()), encoding="utf-8")
            (batch / "manifest.json").write_text(json.dumps({
                "artifact_count": 1, "read_only": True, "automatic_promotion": False,
            }), encoding="utf-8")
            path = root / "store.jsonl"; store.ingest_batch(batch, path)
            event = json.loads(path.read_text())
            self.assertEqual(event["event"], "candidate_observed")
            self.assertFalse(event["automatic_promotion"])
            self.assertNotIn("promoted", event)


if __name__ == "__main__":
    unittest.main()
