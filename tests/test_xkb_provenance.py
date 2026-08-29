from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from xkb_provenance import MARKER, annotate, is_self_derived  # noqa: E402


class ProvenanceContractTests(unittest.TestCase):
    def test_what_the_writer_emits_is_what_the_reader_detects(self) -> None:
        """The whole point: one definition shared by both ends.

        These drifted apart once already, and 913 self-derived claims spent a
        day competing at full weight against external evidence because of it.
        """
        self.assertTrue(is_self_derived(annotate("2026-08-02-evening-candidates.md#65")))

    def test_the_older_distillation_format_is_still_recognised(self) -> None:
        self.assertTrue(is_self_derived("*(self-derived · memory/2026-08-03.md)*"))

    def test_a_bare_memory_reference_counts(self) -> None:
        self.assertTrue(is_self_derived("整理自 (memory/2026-08-03.md) 的結論"))

    def test_external_sources_are_not_penalised(self) -> None:
        self.assertFalse(is_self_derived("*(source: https://x.com/i/status/123)*"))
        self.assertFalse(is_self_derived(""))

    def test_recall_applies_the_penalty_to_the_written_form(self) -> None:
        recall = importlib.import_module("recall_for_conversation")
        line = f"某個結論 <!-- xkb-candidate:abc --> {annotate('x.md#1')}"
        self.assertEqual(
            recall._provenance_penalty({"summary": line}),
            recall.SELF_DERIVED_PENALTY,
        )
        self.assertEqual(
            recall._provenance_penalty({"summary": "*(source: https://x.com/1)*"}),
            0.0,
        )


class BackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backfill = importlib.import_module("backfill_self_derived_marker")

    def test_repairs_only_governance_lines(self) -> None:
        text = (
            "- 外部卡片摘要 *(source: https://x.com/i/status/1)*\n"
            "某個結論 <!-- xkb-candidate:deadbeef --> *(source: a.md#1)*\n"
        )
        repaired, changed = self.backfill.repair(text)
        self.assertEqual(changed, 1)
        self.assertIn("https://x.com/i/status/1)*", repaired)
        self.assertIn(f"*({MARKER} · source: a.md#1)*", repaired)

    def test_running_twice_changes_nothing(self) -> None:
        text = "某個結論 <!-- xkb-candidate:deadbeef --> *(source: a.md#1)*\n"
        once, first = self.backfill.repair(text)
        twice, second = self.backfill.repair(once)
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(once, twice)

    def test_preserves_lines_it_does_not_touch(self) -> None:
        text = "# 標題\n\n一般內容\n\n- 條列\n"
        repaired, changed = self.backfill.repair(text)
        self.assertEqual((repaired, changed), (text, 0))


if __name__ == "__main__":
    unittest.main()
