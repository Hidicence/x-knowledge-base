#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_xkb_candidate_artifacts.py"
spec = importlib.util.spec_from_file_location("validator", SCRIPT)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def artifact() -> dict:
    excerpt = "Pan prefers evidence-first workflows"
    return {
        "schema": "xkb-candidate-artifact.v1",
        "artifact_id": "candidate:pan_prefers_evidence_first",
        "candidate_key": "pan_prefers_evidence_first",
        "candidate_label": "Pan prefers evidence-first workflows",
        "status": "review_ready",
        "provenance": {
            "source_kind": "daily_memory",
            "evidence_type": "conversation_or_note",
            "source_path": "memory/2026-08-01.md",
            "source_sha256": "a" * 64,
        },
        "episode": {"episode_id": "ep-1", "id_kind": "episode_id"},
        "evidence": [{
            "evidence_id": "e1",
            "excerpt": excerpt,
            "evidence_sha256": sha256(excerpt.encode()).hexdigest(),
            "induction_eligible": True,
            "role": "support",
        }],
        "decision": {
            "label": "REVIEW_READY",
            "reason": "synthetic two-episode fixture",
            "automatic_promotion": False,
        },
    }


class ValidatorTests(unittest.TestCase):
    def test_valid_synthetic_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "candidate.json"
            p.write_text(json.dumps(artifact()), encoding="utf-8")
            self.assertEqual(validator.validate_artifact(p), [])

    def test_hash_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            obj = artifact()
            obj["evidence"][0]["excerpt"] = "tampered"
            p = Path(tmp) / "candidate.json"
            p.write_text(json.dumps(obj), encoding="utf-8")
            self.assertTrue(any("sha256 mismatch" in e for e in validator.validate_artifact(p)))

    def test_automatic_promotion_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            obj = artifact()
            obj["decision"]["automatic_promotion"] = True
            p = Path(tmp) / "candidate.json"
            p.write_text(json.dumps(obj), encoding="utf-8")
            self.assertTrue(any("automatic_promotion" in e for e in validator.validate_artifact(p)))

    def test_synthetic_batch_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "a.json").write_text(json.dumps(artifact()), encoding="utf-8")
            (batch / "manifest.json").write_text(json.dumps({
                "artifact_count": 1,
                "read_only": True,
                "automatic_promotion": False,
            }), encoding="utf-8")
            result = validator.validate_batch(batch)
            self.assertTrue(result["valid"], result["errors"])


if __name__ == "__main__":
    unittest.main()
