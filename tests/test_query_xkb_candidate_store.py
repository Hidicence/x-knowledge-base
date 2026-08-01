#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "query_xkb_candidate_store.py"
spec = importlib.util.spec_from_file_location("query", SCRIPT)
assert spec and spec.loader
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)


def event(key: str, artifact_id: str, episode: str | None, eligible: bool) -> dict:
    artifact = {
        "artifact_id": artifact_id,
        "candidate_key": key,
        "episode": {"episode_id": episode, "id_kind": "episode_id"} if episode else None,
        "provenance": {"evidence_type": "conversation_or_note" if eligible else "derived_dreaming"},
        "evidence": [{"induction_eligible": eligible}],
        "decision": {"label": "HOLD", "automatic_promotion": False},
    }
    return {
        "artifact_id": artifact_id,
        "artifact_sha256": artifact_id * 64 if len(artifact_id) == 1 else "a" * 64,
        "candidate_key": key,
        "artifact": artifact,
    }


class QueryStoreTests(unittest.TestCase):
    def test_distinct_episodes_are_aggregated_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "store.jsonl"
            rows = [event("pref", "a", "ep-1", True), event("pref", "b", "ep-2", True)]
            p.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
            result = query.review_store(p)
            self.assertEqual(result["candidates"][0]["distinct_episode_count"], 2)
            self.assertEqual(result["candidates"][0]["review_gate"], "REVIEW_READY")
            self.assertFalse(result["automatic_promotion"])

    def test_duplicate_artifact_is_not_double_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "store.jsonl"
            row = event("pref", "a", "ep-1", True)
            p.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
            result = query.review_store(p)
            self.assertEqual(result["candidates"][0]["artifact_count"], 1)

    def test_mutation_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "store.jsonl"
            a = event("pref", "a", "ep-1", True)
            b = dict(a); b["artifact_sha256"] = "b" * 64
            p.write_text(json.dumps(a) + "\n" + json.dumps(b) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                query.review_store(p)


if __name__ == "__main__":
    unittest.main()
