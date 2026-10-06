"""2026-10-05 第一天真實使用照出的四件事，各自要有一個會失敗的測試。

- wiki 比對逐段跑 Python 迴圈，每輪多花約 3 秒
- 同一個 session 裡同一條建議被塞三次
- 召回把同一段對話裡 Claude 自己的回覆當成既有知識送回來
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import continuity_recall as cr
import xkb_delivery as delivery
import xkb_memory_service as service


def record(key, judge=0.9, body=None):
    return {"id": key, "judge": judge, "summary": body or f"{key} body text",
            "score": 0.9, "score_scale": "wiki_semantic", "namespace": "private"}


class MatrixScoringMatchesTheLoop(unittest.TestCase):
    def test_same_ranking_and_scores_as_per_vector_cosine(self):
        import random
        rng = random.Random(7)
        vectors = {f"wiki/t{i}.md#s": [rng.uniform(-1, 1) for _ in range(16)] for i in range(200)}
        query = [rng.uniform(-1, 1) for _ in range(16)]
        loop = sorted(((cr._cosine(query, v), k) for k, v in vectors.items()), reverse=True)
        fast = cr._score_all(vectors, query)
        self.assertEqual([k for _, k in fast[:20]], [k for _, k in loop[:20]])
        for (a, _), (b, _) in zip(fast, loop):
            self.assertAlmostEqual(a, b, places=5)

    def test_ragged_or_mismatched_vectors_fall_back_instead_of_failing(self):
        ragged = {"a": [1.0, 0.0], "b": [1.0, 0.0, 0.0]}
        loop = sorted(((cr._cosine([1.0, 0.0], v), k) for k, v in ragged.items()), reverse=True)
        self.assertEqual(cr._score_all(ragged, [1.0, 0.0]), loop)
        self.assertEqual(len(cr._score_all({"a": [1.0, 0.0]}, [1.0, 0.0, 0.0])), 1)

    def test_a_new_vector_dict_is_not_scored_with_a_stale_matrix(self):
        first = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
        cr._score_all(first, [1.0, 0.0])
        second = {"c": [0.0, 1.0]}
        self.assertEqual([k for _, k in cr._score_all(second, [0.0, 1.0])], ["c"])


class SessionDeduplication(unittest.TestCase):
    def test_evidence_already_suggested_in_this_session_is_not_delivered_again(self):
        given = delivery.identity_key(record("given"))
        result = delivery.select([record("given")], {"status": "judged"}, [], query="next shot",
                                 delivered=frozenset({given}))
        self.assertEqual(result["records"], [])
        self.assertEqual(result["withheld"][0]["reason"], "delivered_earlier_in_session")
        self.assertEqual(result["status"], "ready")

    def test_fresh_evidence_takes_the_freed_slot(self):
        given = delivery.identity_key(record("given"))
        records = [record("given"), record("a"), record("b"), record("c")]
        result = delivery.select(records, {"status": "judged"}, [], query="next shot",
                                 delivered=frozenset({given}))
        self.assertEqual([r["id"] for r in result["records"]], ["a", "b", "c"])


class ServiceBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = service.Store(Path(self.tmp.name) / "knowledge.sqlite")
        env = mock.patch.dict(os.environ, {"XKB_JEV_DECIDE": "1"})
        env.start()
        self.addCleanup(env.stop)

    def _trace(self, session_key, turn_id, query, answer):
        session = self.store.open_session({"source": "test", "session_key": session_key})["session_id"]
        with mock.patch.object(self.store, "knowledge_recall", return_value={"records": []}):
            self.store.start_turn({"session_id": session, "turn_id": turn_id, "query": query})
        self.store.complete_turn(turn_id, {"session_id": session, "query": query, "answer": answer})
        return session

    def test_this_sessions_own_traces_are_not_recalled_as_knowledge(self):
        mine = self._trace("mine", "t1", "下載 youtube 影片", "兩支都下載完成了")
        self._trace("other", "t2", "下載 youtube 影片 檢查", "用 ffprobe 檢查時長")
        with mock.patch.object(self.store.catalog, "search", return_value={"records": []}), \
             mock.patch.object(service, "judge_relevance", side_effect=lambda q, r: (r, {"status": "judged"})):
            packet = self.store.knowledge_recall("下載 youtube 影片", session_id=mine)
        answers = [r.get("answer") for r in packet["records"] if r.get("record_type") == "conversation_trace"]
        self.assertEqual(answers, ["用 ffprobe 檢查時長"])

    def test_turn_start_passes_what_this_session_was_already_given(self):
        session = self.store.open_session({"source": "test", "session_key": "s"})["session_id"]
        given = {"records": [], "delivery": {"records": [{"id": "tip", "evidence_key": "ev1:tip"}]}}
        with mock.patch.object(self.store, "knowledge_recall", return_value=given):
            self.store.start_turn({"session_id": session, "turn_id": "a", "query": "first"})
        other = self.store.open_session({"source": "test", "session_key": "o"})["session_id"]
        with mock.patch.object(self.store, "knowledge_recall", return_value={"records": []}) as run:
            self.store.start_turn({"session_id": session, "turn_id": "b", "query": "second"})
            self.assertEqual(run.call_args.kwargs["delivered"], frozenset({"ev1:tip"}))
            self.store.start_turn({"session_id": other, "turn_id": "c", "query": "second"})
            self.assertEqual(run.call_args.kwargs["delivered"], frozenset())

    def test_completion_records_how_much_of_each_suggestion_the_answer_reused(self):
        session = self.store.open_session({"source": "test", "session_key": "s"})["session_id"]
        given = {"namespace": "private", "records": [], "delivery": {"records": [
            {"evidence_key": "ev1:used", "summary": "單一鏡頭只安排一個核心動作"},
            {"evidence_key": "ev1:ignored", "summary": "碳盤查係數版本要記錄"}]}}
        with mock.patch.object(self.store, "knowledge_recall", return_value=given):
            self.store.start_turn({"session_id": session, "turn_id": "t", "query": "寫 prompt"})
        self.store.complete_turn("t", {"session_id": session, "query": "寫 prompt",
                                       "answer": "這一鏡只安排一個核心動作：夾開魚肉。"})
        with self.store.connect() as db:
            rows = dict(db.execute("SELECT evidence_key, overlap FROM delivery_outcomes").fetchall())
        self.assertGreater(rows["ev1:used"], 0.5)
        self.assertLess(rows["ev1:ignored"], 0.2)


class ScheduledAgentsSkipRecall(unittest.TestCase):
    def run_hook(self, env):
        import xkb_agent_hook as hook
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(hook, "read_event", return_value={"hook_event_name": "UserPromptSubmit", "prompt": "你是每日巡檢 Agent"}), \
             mock.patch.object(hook, "config", return_value={}), \
             mock.patch.object(hook, "on_prompt") as prompt:
            hook.main()
        return prompt.called

    def test_claude_print_mode_is_not_a_conversation(self):
        self.assertFalse(self.run_hook({"CLAUDE_CODE_SESSION_ATTENDED": "0", "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}))
        self.assertTrue(self.run_hook({"CLAUDE_CODE_SESSION_ATTENDED": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}))

    def test_entrypoint_decides_when_attendance_is_unknown(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_SESSION_ATTENDED", None)
            self.assertFalse(self.run_hook({"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}))
            self.assertTrue(self.run_hook({"CLAUDE_CODE_ENTRYPOINT": "cli"}))


if __name__ == "__main__":
    unittest.main()
