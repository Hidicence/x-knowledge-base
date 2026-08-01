#!/usr/bin/env python3
"""Focused regression tests for the read-only candidate pool replay analyzer."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xkb_candidate_pool_analyzer.py"
spec = importlib.util.spec_from_file_location("xkb_candidate_pool_analyzer", SCRIPT)
assert spec and spec.loader
analyzer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyzer)


class AnalyzerClassifierTests(unittest.TestCase):
    def test_system_and_pipeline_provenance_order(self) -> None:
        self.assertEqual(
            analyzer.evidence_type("User: [cron:abc] report", Path("memory/2026-08-01.md")),
            "system",
        )
        self.assertEqual(
            analyzer.evidence_type(
                "User: Write a dream diary entry from these fragments",
                Path("memory/dreaming/light/2026-08-01.md"),
            ),
            "pipeline_instruction",
        )
        self.assertEqual(
            analyzer.evidence_type(
                "Assistant: NO_REPLY", Path("memory/dreaming/light/2026-08-01.md")
            ),
            "system",
        )

    def test_episode_id_is_explicit_only(self) -> None:
        self.assertEqual(analyzer.extract_episode_id("episode_id: ep-123"), "ep-123")
        self.assertEqual(analyzer.extract_episode_id('session_id="session-9"'), "session-9")
        self.assertIsNone(analyzer.extract_episode_id("filename looks like an episode"))


class MemmyGateTests(unittest.TestCase):
    def test_distinct_explicit_episodes_become_review_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory").mkdir()
            (root / "memory" / "2026-07-01.md").write_text(
                "- Candidate: Pan prefers evidence-first workflows\n"
                "  episode_id: ep-1\n",
                encoding="utf-8",
            )
            (root / "memory" / "2026-07-02.md").write_text(
                "- Candidate: Pan prefers evidence-first workflows\n"
                "  episode_id: ep-2\n",
                encoding="utf-8",
            )
            report = analyzer.analyze(root)
            self.assertEqual(report["summary"]["REVIEW_READY"], 1)
            candidate = report["candidates"][0]
            self.assertEqual(candidate["eligible_episode_count"], 2)
            self.assertEqual(candidate["eligible_evidence_count"], 2)
            self.assertEqual(candidate["decision"], "REVIEW_READY")

    def test_repeated_dreaming_files_do_not_fake_episode_diversity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory" / "dreaming" / "light").mkdir(parents=True)
            for day in ("01", "02", "03"):
                (root / "memory" / "dreaming" / "light" / f"2026-07-{day}.md").write_text(
                    "- Candidate: Pan prefers evidence-first workflows\n",
                    encoding="utf-8",
                )
            report = analyzer.analyze(root)
            self.assertEqual(report["summary"]["REVIEW_READY"], 0)
            self.assertEqual(report["summary"]["REJECT_SYSTEM_ECHO"], 1)
            self.assertEqual(report["candidates"][0]["eligible_episode_count"], 0)


if __name__ == "__main__":
    unittest.main()
