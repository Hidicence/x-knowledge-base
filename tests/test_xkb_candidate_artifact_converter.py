#!/usr/bin/env python3
"""Regression tests for the read-only replay-to-artifact converter."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xkb_candidate_artifact_converter.py"
spec = importlib.util.spec_from_file_location("converter", SCRIPT)
assert spec and spec.loader
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


class CandidateArtifactConverterTests(unittest.TestCase):
    def test_conversion_is_never_promotable(self) -> None:
        item = {
            "candidate_key": "pan_prefers_evidence_first",
            "candidate_label": "Pan prefers evidence-first workflows",
            "decision": "REVIEW_READY",
            "reason": "two explicit episodes",
            "eligible_episode_count": 2,
            "eligible_evidence_count": 2,
            "system_evidence": 0,
            "confidence": 0.9,
            "evidence": [
                {
                    "path": "memory/2026-08-01.md",
                    "sha256": "a" * 64,
                    "evidence_type": "conversation_or_note",
                    "episode_id": "ep-1",
                    "induction_eligible": True,
                    "excerpt": "Pan prefers evidence-first workflows",
                }
            ],
        }
        artifact = converter.convert_candidate(item, "2026-08-01T02:00:00+00:00")
        self.assertEqual(artifact["schema"], "xkb-candidate-artifact.v1")
        self.assertEqual(artifact["status"], "review_ready")
        self.assertFalse(artifact["decision"]["automatic_promotion"])
        self.assertEqual(artifact["episode"]["episode_id"], "ep-1")
        self.assertTrue(artifact["evidence"][0]["induction_eligible"])

    def test_batch_isolated_from_input_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report.json"
            report.write_text(json.dumps({"candidates": []}), encoding="utf-8")
            out = root / "artifacts"
            result = converter.convert_report(report, out)
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertTrue(manifest["read_only"])
            self.assertFalse(manifest["automatic_promotion"])
            self.assertEqual(manifest["artifact_count"], 0)
            self.assertEqual(report.read_text(encoding="utf-8"), json.dumps({"candidates": []}))


if __name__ == "__main__":
    unittest.main()
