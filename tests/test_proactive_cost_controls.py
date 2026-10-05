"""2026-10-05 第一天真實使用照出的四件事，各自要有一個會失敗的測試。

- wiki 比對逐段跑 Python 迴圈，每輪多花約 3 秒
- 「好 直接送」這種輪次等完檢索、jev、挑段落才回空
- 同一個 session 裡同一條建議被塞三次，每次都付一次挑段落
- 召回把同一段對話裡 Claude 自己的回覆當成既有知識送回來
"""
import os
import sys
import tempfile
import threading
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
    task = {"needs": ["how to shoot food"], "constraints": [], "repeat": False, "model": "fixture"}

    def test_evidence_already_suggested_in_this_session_is_not_reviewed_again(self):
        given = delivery.identity_key(record("given"))
        with mock.patch.object(delivery, "_select_grounded") as choose:
            result = delivery.select([record("given")], {"status": "judged"}, [], query="next shot",
                                     task=self.task, delivered=frozenset({given}))
        choose.assert_not_called()  # 全給過：連挑段落的模型都不叫
        self.assertEqual(result["records"], [])
        self.assertEqual(result["withheld"][0]["reason"], "delivered_earlier_in_session")
        self.assertEqual(result["status"], "ready")

    def test_fresh_evidence_takes_the_freed_review_slot(self):
        given = delivery.identity_key(record("given"))
        def choose(query, conversation, records, task):
            return {**task, "selected": [{"index": 0, "need": 0, "quote": records[0]["summary"]}]}
        with mock.patch.object(delivery, "_select_grounded", side_effect=choose):
            result = delivery.select([record("given"), record("fresh")], {"status": "judged"}, [],
                                     query="next shot", task=self.task, delivered=frozenset({given}))
        self.assertEqual([r["id"] for r in result["records"]], ["fresh"])

    def test_an_explicit_repeat_request_can_deliver_it_again(self):
        given = delivery.identity_key(record("given"))
        def choose(query, conversation, records, task):
            return {**task, "selected": [{"index": 0, "need": 0, "quote": records[0]["summary"]}]}
        with mock.patch.object(delivery, "_select_grounded", side_effect=choose):
            result = delivery.select([record("given")], {"status": "judged"}, [], query="再講一次",
                                     task={**self.task, "repeat": True}, delivered=frozenset({given}))
        self.assertEqual([r["id"] for r in result["records"]], ["given"])

    def test_task_repeat_flag_is_optional_but_must_be_boolean(self):
        def plan(raw):
            with mock.patch.object(delivery._llm, "_runtime_settings", return_value={}), \
                 mock.patch.object(delivery._llm, "_direct_api_call", return_value=raw):
                return delivery.prepare("q", [])
        self.assertFalse(plan('{"needs": ["x"], "constraints": []}')["repeat"])
        self.assertTrue(plan('{"needs": ["x"], "constraints": [], "repeat": true}')["repeat"])
        self.assertIsNone(plan('{"needs": ["x"], "constraints": [], "repeat": "yes"}'))
        self.assertIsNone(plan('{"needs": ["x"], "constraints": [], "other": 1}'))


class ServiceBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = service.Store(Path(self.tmp.name) / "knowledge.sqlite")
        env = mock.patch.dict(os.environ, {"XKB_JEV_DECIDE": "1"})
        env.start()
        self.addCleanup(env.stop)
        available = mock.patch.object(service.xkb_jev, "available", return_value=True)
        available.start()
        self.addCleanup(available.stop)

    def test_quiet_hook_turn_returns_without_waiting_for_retrieval_or_judging(self):
        release = threading.Event()
        def slow_search(*args, **kwargs):
            release.wait(5)
            return {"records": [record("late")]}
        quiet = {"needs": [], "constraints": [], "repeat": False, "model": "fixture"}
        with mock.patch.object(self.store.catalog, "search", side_effect=slow_search), \
             mock.patch.object(delivery, "prepare", return_value=quiet), \
             mock.patch.object(service, "judge_relevance") as judge:
            packet = self.store.knowledge_recall("好 直接送吧", proactive=True)
        release.set()
        judge.assert_not_called()
        self.assertEqual(packet["skip_reason"], "no_current_information_need")
        self.assertEqual(packet["delivery"]["intervention"]["state"], "quiet")
        self.assertEqual(packet["quality"]["status"], "ready")
        self.assertLess(packet["timing_ms"]["total"], 4000)

    def test_explicit_recall_is_never_cut_short_by_the_planner(self):
        quiet = {"needs": [], "constraints": [], "repeat": False, "model": "fixture"}
        with mock.patch.object(self.store.catalog, "search", return_value={"records": [record("hit")]}), \
             mock.patch.object(self.store, "recall", return_value={"memories": []}), \
             mock.patch.object(delivery, "prepare", return_value=quiet), \
             mock.patch.object(service, "judge_relevance", side_effect=lambda q, r: (r, {"status": "judged"})) as judge:
            packet = self.store.knowledge_recall("seedance workflow")
        judge.assert_called_once()
        self.assertEqual([r["id"] for r in packet["records"]], ["hit"])

    def test_failed_planning_is_unknown_not_quiet(self):
        with mock.patch.object(self.store.catalog, "search", return_value={"records": [record("hit")]}), \
             mock.patch.object(self.store, "recall", return_value={"memories": []}), \
             mock.patch.object(delivery, "prepare", return_value=None), \
             mock.patch.object(service, "judge_relevance", side_effect=lambda q, r: (r, {"status": "judged"})) as judge:
            packet = self.store.knowledge_recall("好 直接送吧", proactive=True)
        judge.assert_called_once()
        self.assertNotEqual(packet.get("skip_reason"), "no_current_information_need")

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
             mock.patch.object(delivery, "prepare", return_value=None), \
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
            self.assertTrue(run.call_args.kwargs["proactive"])
            self.store.start_turn({"session_id": other, "turn_id": "c", "query": "second"})
            self.assertEqual(run.call_args.kwargs["delivered"], frozenset())


if __name__ == "__main__":
    unittest.main()
