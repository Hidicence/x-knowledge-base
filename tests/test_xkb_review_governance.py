import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import xkb_review

FIXTURE = """## Candidate 1
- **Topic:** topic-a
- **Confidence:** high
- **Source date:** 2026-08-26
- **Status:** [ ] approve  [ ] skip

Reusable claim with https://example.test/evidence.
"""


class GovernanceTests(unittest.TestCase):
    def with_staging(self, text=FIXTURE):
        temp = tempfile.TemporaryDirectory()
        staging = Path(temp.name) / "staging"
        staging.mkdir()
        (staging / "batch-candidates.md").write_text(text, encoding="utf-8")
        old = xkb_review.STAGING_DIR
        xkb_review.STAGING_DIR = staging
        return temp, staging, old

    def test_normalize_and_stable_identity_are_deterministic(self):
        self.assertEqual(xkb_review.normalize("  Hello   WORLD\n"), "hello world")
        self.assertEqual(xkb_review.stable_candidate_id("a.md", 3), xkb_review.stable_candidate_id("a.md", 3))
        self.assertEqual(xkb_review.stable_fingerprint("  Hello WORLD"), xkb_review.stable_fingerprint("hello world"))

    def test_provenance_and_relative_source_path(self):
        temp, staging, old = self.with_staging()
        try:
            candidate = xkb_review.load_candidates()[0]
            self.assertEqual(candidate.source_file, "batch-candidates.md")
            self.assertTrue(candidate.evidence_present)
            self.assertTrue(candidate.provenance_complete)
        finally:
            xkb_review.STAGING_DIR = old
            temp.cleanup()

    def _proposals(self, times: int) -> str:
        """同一個新主題被提議 times 次的 staging 內容。"""
        return "\n".join(
            FIXTURE.replace("Candidate 1", f"Candidate {i + 1}")
                   .replace("topic-a", "[NEW: proposed-topic]")
                   .replace("Reusable claim", f"Reusable claim number {i + 1}")
            for i in range(times)
        )

    def test_a_one_off_new_topic_goes_to_general_instead_of_waiting(self):
        """提一次的新主題不該無限期停在提案區。

        原本它會，而且唯一的出路是「主題已經存在」——對 [NEW: x] 而言那個
        條件永遠不成立。155 條就是這樣卡住的。現在它進 general，並且帶著
        當初提議的名字，日後撈得回來。
        """
        temp, staging, old = self.with_staging(self._proposals(1))
        try:
            result = xkb_review.governance_batch(limit=10, dry_run=True, ttl_days=9999)
            self.assertEqual(result["stats"]["routed_to_general"], 1)
            self.assertEqual(result["stats"]["proposal_queue"], 0)
            self.assertEqual(result["topic_suggestions"], [])
        finally:
            xkb_review.STAGING_DIR = old
            temp.cleanup()

    def test_a_repeated_new_topic_becomes_a_proposal(self):
        """反覆出現才值得開一頁,而開不開仍然是領域判斷。"""
        temp, staging, old = self.with_staging(self._proposals(xkb_review.PROMOTE_AFTER))
        try:
            result = xkb_review.governance_batch(limit=20, dry_run=True, ttl_days=9999)
            self.assertEqual(result["stats"]["routed_to_general"], 0)
            proposals = [t for t in result["topic_suggestions"] if t["action"] == "proposal"]
            self.assertEqual(len(proposals), 1)
            self.assertEqual(proposals[0]["proposed_count"], xkb_review.PROMOTE_AFTER)
        finally:
            xkb_review.STAGING_DIR = old
            temp.cleanup()

    def test_low_confidence_never_promotes(self):
        """信心不足擋放行,這跟主題存不存在是兩件獨立的事。"""
        text = FIXTURE.replace("high", "low").replace("https://example.test/evidence", "")
        temp, staging, old = self.with_staging(text)
        try:
            result = xkb_review.governance_batch(limit=10, dry_run=True, ttl_days=9999)
            self.assertEqual(result["stats"]["promoted"], 0)
            self.assertEqual(result["stats"]["review_queue"], 1)
        finally:
            xkb_review.STAGING_DIR = old
            temp.cleanup()

    def test_registry_rerun_is_idempotent(self):
        temp, staging, old = self.with_staging()
        try:
            registry = staging / "registry.jsonl"
            self.assertEqual(xkb_review.write_registry(xkb_review.load_candidates(), registry)["added"], 1)
            self.assertEqual(xkb_review.write_registry(xkb_review.load_candidates(), registry)["added"], 0)
            self.assertEqual(len(registry.read_text(encoding="utf-8").splitlines()), 1)
        finally:
            xkb_review.STAGING_DIR = old
            temp.cleanup()

    def test_bounded_batch_advances_past_registered_candidates(self):
        temp, staging, old = self.with_staging()
        old_governance = xkb_review.GOVERNANCE_DIR
        try:
            source = staging / "batch-candidates.md"
            source.write_text(FIXTURE + FIXTURE.replace("Candidate 1", "Candidate 2").replace("topic-a", "topic-b").replace("Reusable claim with", "A different reusable claim with"), encoding="utf-8")
            governance = staging / "governance"
            governance.mkdir()
            first = xkb_review.load_candidates()[0]
            (governance / "candidate-registry.jsonl").write_text(
                '{"candidate_id": "%s", "lifecycle": "retained"}\n' % first.candidate_id,
                encoding="utf-8",
            )
            xkb_review.GOVERNANCE_DIR = governance
            result = xkb_review.governance_batch(limit=1, dry_run=True, ttl_days=9999)
            self.assertEqual(result["stats"]["discovered"], 1)
            self.assertEqual(result["queues"]["review_queue"][0]["candidate_id"],
                             xkb_review.load_candidates()[1].candidate_id)
        finally:
            xkb_review.STAGING_DIR = old
            xkb_review.GOVERNANCE_DIR = old_governance
            temp.cleanup()

    def test_missing_evidence_cannot_promote(self):
        with tempfile.TemporaryDirectory() as d:
            tmp_path = Path(d)
            staging = tmp_path / "staging"
            staging.mkdir()
            (staging / "batch-candidates.md").write_text("## Candidate 1\n- **Topic:** topic-a\n- **Confidence:** low\n- **Source date:** 2020-01-01\n- **Status:** [ ] approve  [ ] skip\n\nOld claim.\n", encoding="utf-8")
            old = xkb_review.STAGING_DIR
            xkb_review.STAGING_DIR = staging
            try:
                result = xkb_review.governance_batch(limit=10, dry_run=True, ttl_days=30)
                self.assertEqual(result["stats"]["quarantine"], 1)
            finally:
                xkb_review.STAGING_DIR = old

    def test_non_prefix_near_duplicate_is_explainably_related(self):
        text = FIXTURE + FIXTURE.replace("Candidate 1", "Candidate 2").replace("Reusable claim with", "A reusable claim with")
        temp, staging, old = self.with_staging(text)
        try:
            candidates = xkb_review.load_candidates()
            self.assertTrue(candidates[1].relation in ("exact_duplicate", "near_duplicate"))
        finally:
            xkb_review.STAGING_DIR = old
            temp.cleanup()


    def test_apply_promotes_only_gated_candidates_and_is_idempotent(self):
        temp, staging, old = self.with_staging()
        old_governance = xkb_review.GOVERNANCE_DIR
        old_topics = xkb_review.TOPICS_DIR
        topic_dir = staging / "topics"
        topic_dir.mkdir()
        (topic_dir / "topic-a.md").write_text("# Topic A\n\n## Claims\n", encoding="utf-8")
        xkb_review.GOVERNANCE_DIR = staging / "governance"
        xkb_review.TOPICS_DIR = topic_dir
        try:
            first = xkb_review.governance_batch(limit=10, dry_run=False, ttl_days=9999)
            second = xkb_review.governance_batch(limit=10, dry_run=False, ttl_days=9999)
            self.assertEqual(first["stats"]["promoted"], 1)
            self.assertEqual(second["stats"]["promoted"], 0)
            self.assertTrue(first["batch_id"])
            topic = (topic_dir / "topic-a.md").read_text(encoding="utf-8")
            self.assertEqual(topic.count("xkb-candidate:"), 1)
            self.assertGreaterEqual(len((xkb_review.GOVERNANCE_DIR / "audit.jsonl").read_text(encoding="utf-8").splitlines()), 1)
        finally:
            xkb_review.STAGING_DIR = old
            xkb_review.GOVERNANCE_DIR = old_governance
            xkb_review.TOPICS_DIR = old_topics
            temp.cleanup()

    def test_missing_topic_is_retained_then_promoted_after_topic_creation(self):
        temp, staging, old = self.with_staging()
        old_governance = xkb_review.GOVERNANCE_DIR
        old_topics = xkb_review.TOPICS_DIR
        topic_dir = staging / "topics"
        topic_dir.mkdir()
        xkb_review.GOVERNANCE_DIR = staging / "governance"
        xkb_review.TOPICS_DIR = topic_dir
        try:
            deferred = xkb_review.governance_batch(limit=1, dry_run=False, ttl_days=9999)
            candidate_id = xkb_review.load_candidates()[0].candidate_id
            self.assertEqual(deferred["stats"]["promoted"], 0)
            self.assertEqual(deferred["topic_changes"], [])
            rows = [json.loads(line) for line in
                    (xkb_review.GOVERNANCE_DIR / "candidate-registry.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["candidate_id"], candidate_id)
            self.assertEqual(rows[0]["lifecycle"], "retained")

            (topic_dir / "topic-a.md").write_text("# Topic A\n", encoding="utf-8")
            promoted = xkb_review.governance_batch(limit=1, dry_run=False, ttl_days=9999)
            self.assertEqual(promoted["stats"]["promoted"], 1)
            self.assertEqual(len(promoted["topic_changes"]), 1)
            self.assertEqual(promoted["topic_changes"][0]["candidate_id"], candidate_id)
            topic = (topic_dir / "topic-a.md").read_text(encoding="utf-8")
            self.assertEqual(topic.count("xkb-candidate:"), 1)
            registry = (xkb_review.GOVERNANCE_DIR / "candidate-registry.jsonl").read_text(encoding="utf-8")
            self.assertEqual(registry.count('"lifecycle": "retained"'), 1)
            self.assertEqual(registry.count('"lifecycle": "promoted"'), 1)
            self.assertEqual(xkb_review.rollback_batch(promoted["batch_id"])["restored"], 3)
            self.assertEqual((topic_dir / "topic-a.md").read_text(encoding="utf-8"), "# Topic A\n")
        finally:
            xkb_review.STAGING_DIR = old
            xkb_review.GOVERNANCE_DIR = old_governance
            xkb_review.TOPICS_DIR = old_topics
            temp.cleanup()

    def test_rollback_restores_only_batch_outputs_and_preserves_staging(self):
        temp, staging, old = self.with_staging()
        old_governance = xkb_review.GOVERNANCE_DIR
        old_topics = xkb_review.TOPICS_DIR
        topic_dir = staging / "topics"
        topic_dir.mkdir()
        topic_path = topic_dir / "topic-a.md"
        original = "# Topic A\n\n## Claims\n"
        topic_path.write_text(original, encoding="utf-8")
        xkb_review.GOVERNANCE_DIR = staging / "governance"
        xkb_review.TOPICS_DIR = topic_dir
        source_before = (staging / "batch-candidates.md").read_bytes()
        try:
            result = xkb_review.governance_batch(limit=1, dry_run=False, ttl_days=9999)
            self.assertEqual(xkb_review.rollback_batch(result["batch_id"])["restored"], 3)
            self.assertEqual(topic_path.read_text(encoding="utf-8"), original)
            self.assertEqual((staging / "batch-candidates.md").read_bytes(), source_before)
            self.assertIn("rollback", (xkb_review.GOVERNANCE_DIR / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        finally:
            xkb_review.STAGING_DIR = old
            xkb_review.GOVERNANCE_DIR = old_governance
            xkb_review.TOPICS_DIR = old_topics
            temp.cleanup()


    def test_health_counts_include_actionable_queues_without_writes(self):
        text = FIXTURE + FIXTURE.replace("Candidate 1", "Candidate 2").replace("topic-a", "[NEW: proposed]").replace("high", "low")
        temp, staging, old = self.with_staging(text)
        try:
            counts = xkb_review.governance_health_counts(ttl_days=9999)
            self.assertEqual(counts["pending"], 2)
            self.assertEqual(counts["low"], 1)
            # 提一次的新主題會先被導向 general，所以它不是提案。這裡數的必須
            # 跟治理真的會做的一致——報 1 而治理看到 0，就是「77 待處理」
            # 與「實際 3 條」並存的那個病。
            self.assertEqual(counts["proposal"], 0)
            self.assertEqual(counts["safe_promotion"], 1)
            self.assertEqual(counts["quarantine"], 0)
        finally:
            xkb_review.STAGING_DIR = old
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
