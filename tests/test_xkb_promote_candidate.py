from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import xkb_promote_candidate as promote  # noqa: E402
from xkb_memory_service import Store  # noqa: E402


def candidate(**overrides):
    base = {
        "candidate_id": "candidate:abc",
        "candidate_value": "recall must state which retrieval actually ran",
        "episode_ids": ["ep-1", "ep-2"],
        "source_trace_ids": ["trace:1", "trace:2"],
        "status": "pending",
        "analysis": {},
    }
    base.update(overrides)
    return base


class GateTests(unittest.TestCase):
    def test_distinct_episodes_are_required(self) -> None:
        """Saying something twice in one session proves nothing."""
        ok, why = promote.gate(candidate(episode_ids=["ep-1", "ep-1"]))
        self.assertFalse(ok)
        self.assertIn("episode", why)

    def test_rejected_candidates_can_never_be_promoted(self) -> None:
        ok, _ = promote.gate(candidate(status="rejected"))
        self.assertFalse(ok)

    def test_enough_evidence_across_episodes_passes(self) -> None:
        ok, why = promote.gate(candidate())
        self.assertTrue(ok)
        self.assertIn("2", why)


class CardTests(unittest.TestCase):
    def test_card_is_marked_self_derived_so_recall_downweights_it(self) -> None:
        """Knowledge grown from your own conversation must not outrank sourced evidence."""
        card = promote.render_card(candidate())
        self.assertIn("provenance: self-derived", card)
        self.assertIn("self-derived", card.split("---")[2])  # also in the body, where the regex looks
        self.assertIn("Inference", card)

    def test_card_does_not_fabricate_sections_it_cannot_fill(self) -> None:
        card = promote.render_card(candidate())
        for absent in ("## 3. 關鍵論點", "## 4. False Friends", "## 5. 驚訝點", "## 7. 雙語摘要"):
            self.assertNotIn(absent, card)

    def test_card_records_its_provenance(self) -> None:
        card = promote.render_card(candidate())
        self.assertIn("candidate:abc", card)
        self.assertIn("ep-1", card)
        self.assertIn("trace:1", card)


class PromotionFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "memory.sqlite")
        self.cards = Path(self.tmp.name) / "cards"
        patcher = mock.patch.object(promote.xkb_paths, "CARDS_DIR", self.cards)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _insert(self, **overrides) -> dict:
        c = candidate(**overrides)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO candidates(candidate_id,candidate_key,candidate_value,"
                "source_trace_ids_json,episode_ids_json,confidence,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (c["candidate_id"], "key", c["candidate_value"],
                 json.dumps(c["source_trace_ids"]), json.dumps(c["episode_ids"]),
                 0.9, c["status"], "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
            db.commit()
        return c

    def _status(self, candidate_id: str) -> str:
        with self.store.connect() as db:
            return db.execute("SELECT status FROM candidates WHERE candidate_id=?",
                              (candidate_id,)).fetchone()["status"]

    def test_nothing_is_promoted_without_explicit_approval(self) -> None:
        """The whole point of the gate: no path from conversation to knowledge runs itself."""
        self._insert()
        self.assertEqual(promote.cmd_apply(self.store, dry_run=False), 0)
        self.assertEqual(list(self.cards.glob("*.md")) if self.cards.exists() else [], [])
        self.assertEqual(self._status("candidate:abc"), "pending")

    def test_approve_then_apply_writes_one_card(self) -> None:
        self._insert()
        self.assertEqual(promote.cmd_approve(self.store, "candidate:abc", force=False), 0)
        self.assertEqual(self._status("candidate:abc"), "approved")
        self.assertEqual(promote.cmd_apply(self.store, dry_run=False), 0)
        written = list(self.cards.glob("*.md"))
        self.assertEqual(len(written), 1)
        self.assertIn("provenance: self-derived", written[0].read_text(encoding="utf-8"))
        self.assertEqual(self._status("candidate:abc"), "promoted")

    def test_promoted_candidates_are_not_written_twice(self) -> None:
        self._insert()
        promote.cmd_approve(self.store, "candidate:abc", force=False)
        promote.cmd_apply(self.store, dry_run=False)
        promote.cmd_apply(self.store, dry_run=False)
        self.assertEqual(len(list(self.cards.glob("*.md"))), 1)

    def test_weak_candidate_is_refused_unless_forced(self) -> None:
        self._insert(candidate_id="candidate:weak", episode_ids=["ep-1"])
        self.assertEqual(promote.cmd_approve(self.store, "candidate:weak", force=False), 2)
        self.assertEqual(self._status("candidate:weak"), "pending")
        self.assertEqual(promote.cmd_approve(self.store, "candidate:weak", force=True), 0)
        self.assertEqual(self._status("candidate:weak"), "approved")

    def test_forcing_is_recorded(self) -> None:
        self._insert(candidate_id="candidate:weak", episode_ids=["ep-1"])
        promote.cmd_approve(self.store, "candidate:weak", force=True)
        with self.store.connect() as db:
            reasons = json.loads(db.execute(
                "SELECT reject_reasons_json FROM candidates WHERE candidate_id=?",
                ("candidate:weak",)).fetchone()[0])
        self.assertTrue(any("forced" in r for r in reasons))

    def test_dry_run_writes_nothing(self) -> None:
        self._insert()
        promote.cmd_approve(self.store, "candidate:abc", force=False)
        promote.cmd_apply(self.store, dry_run=True)
        self.assertEqual(list(self.cards.glob("*.md")) if self.cards.exists() else [], [])
        self.assertEqual(self._status("candidate:abc"), "approved")


if __name__ == "__main__":
    unittest.main()
