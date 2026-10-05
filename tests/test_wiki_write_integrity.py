"""Exercise wiki writes and their recovery through the existing public commands."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import xkb_review as governance
import distill_memory_to_wiki as distill
import sync_cards_to_wiki as sync
import xkb_synthesize_topic as synthesis
import normalize_index_quality as quality
import canonicalize_duplicates as canonical


FIXTURE = """## Candidate 7
- **Topic:** topic-a
- **Confidence:** high
- **Source date:** 2026-10-01
- **Status:** [ ] approve  [ ] skip

Reusable claim with https://example.test/evidence.
"""


class WikiWriteIntegrity(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.topics = self.root / "topics"
        self.topics.mkdir()
        self.staging = self.root / "staging"
        self.staging.mkdir()
        self.source = self.staging / "batch-candidates.md"
        self.source.write_text(FIXTURE, encoding="utf-8")
        self.topic = self.topics / "topic-a.md"
        self.topic.write_text("# Topic A\n", encoding="utf-8")
        self.governed = self.root / "governance"
        for module, name, value in (
            (governance, "STAGING_DIR", self.staging),
            (governance, "TOPICS_DIR", self.topics),
            (governance, "GOVERNANCE_DIR", self.governed),
            (distill, "STAGING_DIR", self.staging),
            (distill, "TOPICS_DIR", self.topics),
            (sync, "TOPICS_DIR", self.topics),
            (sync, "REVIEW_DECISIONS_PATH", self.root / "decisions.json"),
            (synthesis.xkb_paths, "WIKI_TOPICS_DIR", self.topics),
            (synthesis, "REVIEW_DIR", self.root / "drafts"),
        ):
            patch = mock.patch.object(module, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def run_batch(self):
        return governance.governance_batch(limit=10, dry_run=False, ttl_days=9999)

    def files(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_preview_and_health_do_not_create_general(self):
        self.source.write_text(FIXTURE.replace("topic-a", "[NEW: proposed]"), encoding="utf-8")
        before = self.files()
        preview = governance.governance_batch(dry_run=True, ttl_days=9999)
        health = governance.governance_health_counts(ttl_days=9999)
        self.assertEqual(preview["stats"]["promoted"], 1)
        self.assertEqual(health["safe_promotion"], 1)
        self.assertEqual(self.files(), before)
        result = self.run_batch()
        self.assertTrue((self.topics / "general.md").exists())
        governance.rollback_batch(result["batch_id"])
        self.assertFalse((self.topics / "general.md").exists())

    def test_explicit_dry_run_overrides_write_flag(self):
        before = self.files()
        with mock.patch.object(sys, "argv", ["review", "--governance", "--write-governance", "--dry-run"]):
            self.assertEqual(governance.main(), 0)
        self.assertEqual(self.files(), before)

    def test_health_does_not_report_missing_topics_as_ready_to_promote(self):
        self.topic.unlink()
        health = governance.governance_health_counts(ttl_days=9999)
        preview = governance.governance_batch(dry_run=True, ttl_days=9999)
        self.assertEqual(health["safe_promotion"], preview["stats"]["promoted"])
        self.assertEqual(health["safe_promotion"], 0)

    def test_health_classifies_against_the_same_pool_as_governance(self):
        original = governance.load_candidates()[0]
        self.source.write_text(FIXTURE + "\n" + FIXTURE.replace("Candidate 7", "Candidate 8"), encoding="utf-8")
        governance.write_registry([original], self.governed / "candidate-registry.jsonl", {original.candidate_id})
        health = governance.governance_health_counts(ttl_days=9999)
        preview = governance.governance_batch(dry_run=True, ttl_days=9999)
        self.assertEqual(health["pending"], 1)
        self.assertEqual(health["safe_promotion"], preview["stats"]["promoted"])
        self.assertEqual(health["safe_promotion"], 0)

    def test_interrupted_topic_write_resumes_registry_without_duplicates(self):
        original = self.topic.read_bytes()
        real_write = governance._atomic_write

        def fail_registry(path, data):
            if path == self.governed / "candidate-registry.jsonl":
                raise OSError("simulated interruption after topic write")
            return real_write(path, data)

        with mock.patch.object(governance, "_atomic_write", side_effect=fail_registry):
            with self.assertRaises(OSError):
                self.run_batch()
        self.assertIn("xkb-candidate:", self.topic.read_text(encoding="utf-8"))
        result = self.run_batch()
        rows = (self.governed / "candidate-registry.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(r)["lifecycle"] for r in rows], ["promoted"])
        self.assertEqual(self.topic.read_text(encoding="utf-8").count("xkb-candidate:"), 1)
        governance.rollback_batch(result["batch_id"])
        self.assertEqual(self.topic.read_bytes(), original)

    def test_recovery_refuses_intervening_edit_without_partial_writes(self):
        with mock.patch.object(governance, "_finish_batch", side_effect=OSError("interrupt")):
            with self.assertRaises(OSError):
                self.run_batch()
        self.topic.write_text("# Human changed this\n", encoding="utf-8")
        before = self.files()
        with self.assertRaisesRegex(RuntimeError, "recovery conflict"):
            self.run_batch()
        self.assertEqual(self.files(), before)

    def test_rollback_preflights_all_artifacts_and_can_be_reapplied(self):
        original = self.topic.read_bytes()
        result = self.run_batch()
        generated = self.topic.read_bytes()
        self.topic.write_bytes(generated + b"\nHuman addition\n")
        before = self.files()
        with self.assertRaisesRegex(RuntimeError, "rollback conflict"):
            governance.rollback_batch(result["batch_id"])
        self.assertEqual(self.files(), before)
        self.topic.write_bytes(generated)
        governance.rollback_batch(result["batch_id"])
        self.assertEqual(self.topic.read_bytes(), original)
        self.assertEqual(governance.rollback_batch(result["batch_id"])["restored"], 0)
        reapplied = self.run_batch()
        self.assertEqual(reapplied["stats"]["promoted"], 1)
        self.assertNotEqual(reapplied["batch_id"], result["batch_id"])

    def test_manual_apply_does_not_claim_missing_topic_and_shares_identity(self):
        self.topic.unlink()
        before = self.source.read_bytes()
        self.assertEqual(distill.apply_staging_file(self.source, approve_all=True), (0, 1, []))
        self.assertEqual(self.source.read_bytes(), before)
        self.topic.write_text("# Topic A\n", encoding="utf-8")
        self.assertEqual(distill.apply_staging_file(self.source, approve_all=True), (1, 0, ["topic-a"]))
        generated = self.topic.read_bytes()
        self.assertEqual(distill.apply_staging_file(self.source, approve_all=True), (0, 1, []))
        result = self.run_batch()
        self.assertEqual(result["stats"]["promoted"], 1)
        self.assertEqual(self.topic.read_bytes(), generated)

    def test_interrupted_rollback_resumes_and_records_one_event(self):
        original = self.topic.read_bytes()
        result = self.run_batch()
        real_write = governance._atomic_write

        def fail_audit(path, data):
            if path == self.governed / "audit.jsonl":
                raise OSError("simulated rollback interruption")
            return real_write(path, data)

        with mock.patch.object(governance, "_atomic_write", side_effect=fail_audit):
            with self.assertRaises(OSError):
                governance.rollback_batch(result["batch_id"])
        with self.assertRaisesRegex(RuntimeError, "finish interrupted rollback"):
            self.run_batch()
        governance.rollback_batch(result["batch_id"])
        self.assertEqual(self.topic.read_bytes(), original)
        events = [json.loads(line) for line in (self.governed / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event"] for e in events], ["rollback"])

    def test_corrupt_review_decisions_fail_closed_and_remain_untouched(self):
        for raw in ('{"topics":', '{"topics": []}', '[]'):
            sync.REVIEW_DECISIONS_PATH.write_text(raw, encoding="utf-8")
            with self.assertRaises(ValueError):
                sync.save_absorb_decisions([{"url": "https://example.test", "decision": "approve"}])
            self.assertEqual(sync.REVIEW_DECISIONS_PATH.read_text(encoding="utf-8"), raw)

    def test_absorb_failure_is_unknown_and_next_run_retries(self):
        card = sync.make_card({"title": "Card", "source_url": "https://example.test", "category": "test"})
        with mock.patch.object(sync, "load_topic_map", return_value={}), \
             mock.patch.object(sync, "load_search_items", return_value=[]), \
             mock.patch.object(sync, "collect_topic_existing_urls", return_value={}), \
             mock.patch.object(sync, "iter_mapped_cards", return_value={"topic-a": [card]}), \
             mock.patch.object(sync, "_call_configured_llm", side_effect=[
                 '{"include":"false","dimension":"new_case","reason":"bad type"}',
                 '{"include":false,"dimension":"none","reason":"no value"}',
             ]) as llm, \
             mock.patch.object(sys, "argv", ["sync", "--apply"]):
            self.assertEqual(sync.main(), 2)
            decision = json.loads(sync.REVIEW_DECISIONS_PATH.read_text(encoding="utf-8"))
            self.assertEqual(decision["decisions"][card.url]["decision"], "unavailable")
            self.assertEqual(sync.main(), 0)
            decision = json.loads(sync.REVIEW_DECISIONS_PATH.read_text(encoding="utf-8"))
            self.assertEqual(decision["decisions"][card.url]["decision"], "skip")
            self.assertEqual(llm.call_count, 2)
        self.assertEqual(self.topic.read_text(encoding="utf-8"), "# Topic A\n")

    def test_synthesis_cycles_preserve_manual_sources_and_multiline_archive(self):
        long_note = ("- " + "A reusable observation with evidence. " * 3).rstrip()
        archive = "- An archived observation\n  Supporting detail must stay with its note."
        human = "Human source explanation remains verbatim."
        content = ("# Topic A\n" + synthesis.SOURCES_HEADING + "\n" + human + "\n"
                   + long_note + "\n" + synthesis.CONCLUSIONS_HEADING + "\n- First conclusion\n"
                   + synthesis.CONCLUSIONS_HEADING + "\n- Second conclusion\n"
                   + synthesis.DIGESTED_HEADING + "\n" + archive + "\n")
        self.topic.write_text(content, encoding="utf-8")
        with mock.patch.object(synthesis, "synthesise", return_value=("- New conclusion", [])) as llm:
            for cycle in range(2):
                if cycle:
                    with self.topic.open("a", encoding="utf-8") as stream:
                        stream.write("\n## Fresh notes\n" + long_note + " second\n")
                self.assertEqual(synthesis.cmd_topic("topic-a", apply=False), 0)
                self.assertEqual(synthesis.cmd_topic("topic-a", apply=True), 0)
                output = self.topic.read_text(encoding="utf-8")
                for preserved in (human, archive, long_note, "- First conclusion", "- Second conclusion"):
                    self.assertIn(preserved, output)
            self.assertEqual(llm.call_count, 2)

    def test_synthesis_draft_detects_prose_only_edits(self):
        self.topic.write_text("# Topic A\n- " + "A long observation. " * 8, encoding="utf-8")
        with mock.patch.object(synthesis, "synthesise", return_value=("- Conclusion", [])) as llm:
            self.assertEqual(synthesis.cmd_topic("topic-a", apply=False), 0)
            with self.topic.open("a", encoding="utf-8") as stream:
                stream.write("\nHuman edit after review.\n")
            before = self.topic.read_bytes()
            self.assertEqual(synthesis.cmd_topic("topic-a", apply=True), 3)
            self.assertEqual(self.topic.read_bytes(), before)
            self.assertEqual(llm.call_count, 1)

    def test_quality_url_host_and_canonical_card_preference(self):
        for url in ("https://github.com/example/x.com", "https://examplex.com/docs",
                    "https://x.com/person/status/1234567890123456789/photo/1"):
            self.assertEqual(quality.exclusion_reasons({"source_type": "github_star", "source_url": url}), [])
            self.assertTrue(canonical.is_valid_source_url(url))
        self.assertFalse(quality.is_valid_source_url("https://x.com/person"))
        self.assertGreater(canonical.entry_score({"relative_path": "cards/a.md"}),
                           canonical.entry_score({"relative_path": "bookmarks/a.md"}))


if __name__ == "__main__":
    unittest.main()
