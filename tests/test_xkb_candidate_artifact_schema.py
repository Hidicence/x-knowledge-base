#!/usr/bin/env python3
"""Structural regression tests for the Stage 2 candidate artifact contract."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "xkb-candidate-artifact.v1.schema.json"


class CandidateArtifactSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_schema_contract_is_read_only(self) -> None:
        self.assertEqual(self.schema["$id"].split("/")[-1], SCHEMA.name)
        decision = self.schema["properties"]["decision"]
        self.assertEqual(decision["properties"]["automatic_promotion"]["const"], False)
        self.assertIn("review_ready", self.schema["properties"]["status"]["enum"])
        self.assertNotIn("promoted", self.schema["properties"]["status"]["enum"])

    def test_required_provenance_and_episode_contract(self) -> None:
        provenance = self.schema["properties"]["provenance"]
        self.assertEqual(
            provenance["required"],
            ["source_kind", "evidence_type", "source_path", "source_sha256"],
        )
        episode = self.schema["properties"]["episode"]
        self.assertEqual(episode["type"], ["object", "null"])
        self.assertEqual(episode["properties"]["id_kind"]["enum"], ["episode_id", "session_id"])

    def test_evidence_requires_explicit_eligibility(self) -> None:
        evidence = self.schema["properties"]["evidence"]["items"]
        self.assertIn("induction_eligible", evidence["required"])
        self.assertEqual(evidence["properties"]["induction_eligible"]["type"], "boolean")
        self.assertEqual(self.schema["additionalProperties"], False)


if __name__ == "__main__":
    unittest.main()
