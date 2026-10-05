"""Conversational context must reach retrieval without rewriting captured user turns."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import xkb_agent_hook as hook
import xkb_memory_service as service
import xkb_recall as recall
import xkb_review as review
import distill_memory_to_wiki as distill
import xkb_jev as jev


class ConversationalNeeds(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_hook_sends_prior_dialogue_for_a_statement_without_changing_capture(self):
        transcript = self.root / "conversation.jsonl"
        prior = [{"role": "user", "content": "I am building a Seedance character sequence."},
                 {"role": "assistant", "content": "Keep the character reference stable across shots."}]
        current = "The face still changes on the next shot."
        transcript.write_text("\n".join(json.dumps({"message": m}) for m in
                              prior + [{"role": "user", "content": current}]), encoding="utf-8")
        with mock.patch.object(hook, "STATE_DIR", self.root / "state"), \
             mock.patch.object(hook, "call", side_effect=[{"session_id": "s"}, {"retrieval": {"records": []}}]) as call:
            hook.on_prompt({"prompt": current, "session_id": "s", "transcript_path": str(transcript)}, {"source": "test"})
            captured = json.loads(hook.state_path("s").read_text(encoding="utf-8"))
        body = call.call_args.args[1]
        self.assertEqual(body["query"], current)
        self.assertEqual(body["conversation"], prior)
        self.assertEqual(captured["query"], current)

    def test_context_is_bounded_and_tools_and_harness_are_not_conversation(self):
        recent = recall.conversation_messages([{"role": "user", "content": "x" * 2000}] * 8)
        self.assertEqual(len(recent), 4)
        self.assertTrue(all(len(m["content"]) == 600 for m in recent))
        with self.assertRaises(ValueError):
            recall.conversation_messages([{"role": "tool", "content": "tool output"}])

    def test_core_uses_context_for_statements_but_suppresses_acknowledgement(self):
        store = service.Store(self.root / "knowledge.sqlite")
        history = [{"role": "user", "content": "Seedance character reference"}]
        with mock.patch.object(store.catalog, "search", return_value={"records": []}) as search, \
             mock.patch.object(store, "recall", return_value={"memories": []}), \
             mock.patch.dict("os.environ", {"XKB_JEV_DECIDE": "0"}):
            packet = store.knowledge_recall("It still changes between shots.", conversation=history)
            self.assertIn("Seedance", search.call_args.args[0])
            self.assertEqual(packet["query"], "It still changes between shots.")
            self.assertEqual(packet["conversation_messages"], 1)
            search.reset_mock()
            packet = store.knowledge_recall("ok", conversation=history)
            self.assertEqual(packet["retrieval_mode"], "skipped")
            search.assert_not_called()

    def test_session_context_never_crosses_session_boundary(self):
        store = service.Store(self.root / "knowledge.sqlite")
        first = store.open_session({"source": "test", "session_key": "one"})["session_id"]
        other = store.open_session({"source": "test", "session_key": "two"})["session_id"]
        with mock.patch.object(store, "knowledge_recall", return_value={"records": []}) as run:
            store.start_turn({"session_id": first, "turn_id": "t1", "query": "Seedance character"})
            store.complete_turn("t1", {"session_id": first, "query": "Seedance character", "answer": "Use character references."})
            store.start_turn({"session_id": first, "turn_id": "t2", "query": "It still changes."})
            self.assertEqual(run.call_args.kwargs["conversation"][0]["content"], "Seedance character")
            store.start_turn({"session_id": other, "turn_id": "t3", "query": "It still changes."})
            self.assertEqual(run.call_args.kwargs["conversation"], [])

    def test_current_need_and_followup_context_both_reach_judge(self):
        store = service.Store(self.root / "knowledge.sqlite")
        def search(query, *args, **kwargs):
            key = "old-video" if "Seedance" in query else "carbon"
            return {"records": [{"id": key, "record_type": "knowledge_card", "summary": key,
                                 "namespace": "private", "score": 0.8}]}
        def judge(query, records):
            self.assertEqual({r['id'] for r in records}, {'carbon', 'old-video'})
            return [r for r in records if r['id'] == 'carbon'], {"status": "judged"}
        with mock.patch.object(store.catalog, "search", side_effect=search), \
             mock.patch.object(store, "recall", return_value={"memories": []}), \
             mock.patch.object(service, "judge_relevance", side_effect=judge), \
             mock.patch.dict("os.environ", {"XKB_JEV_DECIDE": "1"}):
            result = store.knowledge_recall("carbon emissions", 1,
                       conversation=[{"role": "user", "content": "Seedance camera shots"}])
        self.assertEqual([r['id'] for r in result['records']], ['carbon'])

    def test_judge_retains_all_bounded_context_within_body_limit(self):
        history = [{"role": "user", "content": "offline deployment only" + "x" * 570},
                   {"role": "assistant", "content": "y" * 600}] * 2
        query = recall.contextual_query("z" * 1200, history, for_judge=True)
        def judge(state, questions, **kwargs):
            self.assertEqual(state.count("offline deployment only"), 2)
            self.assertLessEqual(len(jev._body(state, questions)), jev.MAX_BODY_BYTES)
            return {key: {"noul": 1} for key in questions}
        with mock.patch.object(jev, "judge", side_effect=judge):
            self.assertTrue(jev.relevance(query, [(str(n), "candidate" * 300) for n in range(25)]))

    def test_context_merge_preserves_ranking_legs_and_anonymous_evidence(self):
        store = service.Store(self.root / "knowledge.sqlite")
        def search(query, *args, **kwargs):
            if "earlier" in query:
                return {"records": [{"id": "a", "score": 1, "score_scale": "card_keyword"}]}
            return {"records": [{"id": "a", "score": .8, "score_scale": "card_semantic"},
                                 {"id": "b", "score": .9, "score_scale": "card_semantic"},
                                 {"summary": "anonymous one", "score": .1},
                                 {"summary": "anonymous two", "score": .1}]}
        with mock.patch.object(store.catalog, "search", side_effect=search), \
             mock.patch.object(store, "recall", return_value={"memories": []}), \
             mock.patch.dict("os.environ", {"XKB_JEV_DECIDE": "0"}):
            packet = store.knowledge_recall("current need", 5,
                       conversation=[{"role": "user", "content": "earlier context"}])
        self.assertEqual(packet['records'][0]['id'], 'a')
        self.assertEqual(set(packet['records'][0]['matched_by']), {'card_semantic', 'card_keyword'})
        self.assertEqual(len(packet['records']), 4)


class TopicResolution(unittest.TestCase):
    def test_resolution_keeps_source_and_identity_and_rejects_stale_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            staging.mkdir()
            governance = root / "governance"
            governance.mkdir()
            source = staging / "sample.md"
            original = "## Candidate 1\n- **Topic:** old-topic\n- **Status:** [ ] approve\n\nReusable observation with evidence.\n"
            source.write_text(original, encoding="utf-8")
            with mock.patch.object(review, "STAGING_DIR", staging), mock.patch.object(review, "GOVERNANCE_DIR", governance):
                before = review.load_candidates(False)[0]
                (governance / "topic-resolutions.json").write_text(json.dumps({before.candidate_id: {
                    "fingerprint": before.fingerprint, "topic": "current-topic", "reason": "reviewed mapping"}}), encoding="utf-8")
                after = review.load_candidates(False)[0]
                self.assertEqual(after.topic_key, "current-topic")
                self.assertEqual(after.original_topic, "old-topic")
                self.assertEqual(after.candidate_id, before.candidate_id)
                self.assertEqual(source.read_text(encoding="utf-8"), original)
                topics = root / "topics"
                topics.mkdir()
                (topics / "current-topic.md").write_text("# Current\n", encoding="utf-8")
                with mock.patch.object(distill, "STAGING_DIR", staging), \
                     mock.patch.object(distill, "TOPICS_DIR", topics):
                    applied, skipped, slugs = distill.apply_staging_file(source, approve_all=True)
                self.assertEqual((applied, skipped, slugs), (1, 0, ["current-topic"]))
                self.assertFalse((topics / "old-topic.md").exists())
                source.write_text(original + "Changed evidence.\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "stale topic resolution"):
                    review.load_candidates(False)

    def test_archived_and_redirect_pages_cannot_accumulate_new_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            topics = Path(tmp)
            for name, body in {"active": "# Active\n", "archive": "---\nstatus: archived\n---\n# Archive\n",
                               "redirect": "# Redirect: Canonical\n[[active]]\n"}.items():
                (topics / f"{name}.md").write_text(body, encoding="utf-8")
            with mock.patch.object(distill, "TOPICS_DIR", topics):
                self.assertEqual(distill.load_topic_slugs(), ["active"])
                before = (topics / "redirect.md").read_bytes()
                self.assertFalse(distill.upsert_wiki_section("redirect", "Notes", "New note", "2026-10-05"))
                self.assertEqual((topics / "redirect.md").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
