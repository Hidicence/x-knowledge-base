"""2026-10-05 第一天真實使用照出的四件事，各自要有一個會失敗的測試。

- wiki 比對逐段跑 Python 迴圈，每輪多花約 3 秒
- 同一個 session 裡同一條建議被塞三次
- 召回把同一段對話裡 Claude 自己的回覆當成既有知識送回來
"""
import json
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

    def test_without_an_explicit_flag_nothing_is_skipped(self):
        # stream-json 互動前端也回報 sdk-cli；不能靠 ENTRYPOINT 猜。
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_SESSION_ATTENDED", None)
            for entrypoint in ("sdk-cli", "sdk-ts", "cli"):
                self.assertTrue(self.run_hook({"CLAUDE_CODE_ENTRYPOINT": entrypoint}))


if __name__ == "__main__":
    unittest.main()


class ReviewFindings20261006(unittest.TestCase):
    """Codex 審查（tmp/opus-review-oct06/REVIEW.md）重現的三個漏召回。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = service.Store(Path(self.tmp.name) / "knowledge.sqlite")
        env = mock.patch.dict(os.environ, {"XKB_JEV_DECIDE": "1"})
        env.start()
        self.addCleanup(env.stop)

    def test_eleventh_candidate_fills_the_slot_when_top_ten_were_already_given(self):
        hits = [{**record(f"old{i}", 0.9, f"already given step {i}"), "record_type": "knowledge_card"}
                for i in range(10)] + [{**record("fresh", 0.8, "a new relevant step"), "record_type": "knowledge_card"}]
        given = frozenset(delivery.identity_key(h) for h in hits[:10])
        with mock.patch.object(self.store.catalog, "search", return_value={"records": [dict(h) for h in hits]}), \
             mock.patch.object(self.store, "recall", return_value={"memories": []}), \
             mock.patch.object(service, "judge_relevance", side_effect=lambda q, r: (r, {"status": "judged"})):
            packet = self.store.knowledge_recall("next step", 10, delivered=given)
        self.assertEqual(len(packet["records"]), 10)  # the response contract is unchanged
        self.assertEqual([r["id"] for r in packet["delivery"]["records"]], ["fresh"])

    def _turn(self, session, turn_id, query, answer, at):
        with self.store.connect() as db:
            db.execute("INSERT INTO turns(turn_id,session_id,query,answer,status,trace_id,started_at,completed_at) "
                       "VALUES(?,?,?,?,?,?,?,?)", (turn_id, session, query, answer, "succeeded", "trace:" + turn_id, at, at))

    def test_own_session_cannot_crowd_other_sessions_out_of_the_result_limit(self):
        mine = self.store.open_session({"source": "test", "session_key": "mine"})["session_id"]
        other = self.store.open_session({"source": "test", "session_key": "other"})["session_id"]
        self._turn(other, "o1", "download video", "Use ffprobe to verify duration and codec.", "2026-10-01T00:00:00")
        for i in range(10):
            self._turn(mine, f"m{i}", "download video", f"downloaded file {i}", f"2026-10-06T00:00:{i:02d}")
        answers = [m["answer"] for m in self.store.recall("download video", 10, exclude_session=mine)["memories"]]
        self.assertEqual(answers, ["Use ffprobe to verify duration and codec."])

    def test_recall_path_keeps_other_sessions_when_own_turns_are_newer(self):
        mine = self.store.open_session({"source": "test", "session_key": "mine"})["session_id"]
        other = self.store.open_session({"source": "test", "session_key": "other"})["session_id"]
        self._turn(other, "o1", "download video", "Use ffprobe to verify duration and codec.", "2026-10-01T00:00:00")
        for i in range(10):
            self._turn(mine, f"m{i}", "download video", f"downloaded file {i}", f"2026-10-06T00:00:{i:02d}")
        with mock.patch.object(self.store.catalog, "search", return_value={"records": []}),              mock.patch.object(service, "judge_relevance", side_effect=lambda q, r: (r, {"status": "judged"})):
            packet = self.store.knowledge_recall("download video", 10, session_id=mine)
        answers = [r.get("answer") for r in packet["records"] if r.get("record_type") == "conversation_trace"]
        self.assertEqual(answers, ["Use ffprobe to verify duration and codec."])

    def test_own_session_cannot_fill_the_500_row_window(self):
        mine = self.store.open_session({"source": "test", "session_key": "mine"})["session_id"]
        other = self.store.open_session({"source": "test", "session_key": "other"})["session_id"]
        self._turn(other, "o1", "download video", "Use ffprobe to verify duration and codec.", "2026-09-01T00:00:00")
        with self.store.connect() as db:
            db.executemany("INSERT INTO turns(turn_id,session_id,query,answer,status,trace_id,started_at,completed_at) "
                           "VALUES(?,?,?,?,?,?,?,?)",
                           [(f"m{i}", mine, "unrelated chatter", "ok", "succeeded", f"trace:m{i}",
                             "2026-10-06T00:00:00", f"2026-10-06T{i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}")
                            for i in range(500)])
        answers = [m["answer"] for m in self.store.recall("download video", 10, exclude_session=mine)["memories"]]
        self.assertEqual(answers, ["Use ffprobe to verify duration and codec."])

    def test_updated_source_can_be_delivered_again_but_the_same_content_cannot(self):
        guide = lambda text: {**record("wiki/topics/guide.md", 0.9, text), "record_type": "wiki_topic", "section": "Guide"}
        sent = delivery.select([guide("Use version 1")], {"status": "judged"}, [], query="q")["records"][0]
        given = frozenset({(delivery.identity_key(sent), sent["content_fingerprint"])})
        same = delivery.select([guide("Use  version 1")], {"status": "judged"}, [], query="q", delivered=given)
        self.assertEqual(same["records"], [])
        updated = delivery.select([guide("Version 1 is obsolete; use version 2")],
                                  {"status": "judged"}, [], query="q", delivered=given)
        self.assertEqual([r["summary"] for r in updated["records"]], ["Version 1 is obsolete; use version 2"])

    def test_turn_start_carries_fingerprints_and_keeps_legacy_keys_blocking(self):
        session = self.store.open_session({"source": "test", "session_key": "s"})["session_id"]
        packets = [{"records": [], "delivery": {"records": [{"evidence_key": "ev1:new", "content_fingerprint": "abc"}]}},
                   {"records": [], "delivery": {"records": [{"evidence_key": "ev1:legacy"}]}}]
        for i, packet in enumerate(packets):
            with mock.patch.object(self.store, "knowledge_recall", return_value=packet):
                self.store.start_turn({"session_id": session, "turn_id": f"t{i}", "query": f"q{i}"})
        with mock.patch.object(self.store, "knowledge_recall", return_value={"records": []}) as run:
            self.store.start_turn({"session_id": session, "turn_id": "t9", "query": "next"})
        self.assertEqual(run.call_args.kwargs["delivered"], frozenset({("ev1:new", "abc"), "ev1:legacy"}))


class CodeReviewFindings20261006(unittest.TestCase):
    """/code-review high 1a80821..HEAD 的十項，各一個在修正前會失敗的測試。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = service.Store(Path(self.tmp.name) / "knowledge.sqlite")
        env = mock.patch.dict(os.environ, {"XKB_JEV_DECIDE": "1"})
        env.start()
        self.addCleanup(env.stop)

    def _turn(self, session, turn_id, query, answer, at, retrieval=None):
        with self.store.connect() as db:
            db.execute("INSERT INTO turns(turn_id,session_id,query,answer,status,trace_id,retrieval_json,started_at,completed_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (turn_id, session, query, answer, "succeeded", "trace:" + turn_id,
                                                     json.dumps(retrieval or {}), at, at))

    def test_another_chunk_of_the_same_card_is_not_resent(self):
        card = lambda text: {**record("card-x", 0.9, text), "record_type": "knowledge_chunk"}
        first = delivery.select([card("chunk A of the card")], {"status": "judged"}, [], query="q")["records"][0]
        self.assertNotIn("content_fingerprint", first)
        wiki = {**record("wiki/topics/g.md", 0.9, "Use version 1"), "record_type": "wiki_topic", "section": "Guide"}
        self.assertIn("content_fingerprint", delivery.select([wiki], {"status": "judged"}, [], query="q")["records"][0])
        given = frozenset({delivery.identity_key(card("x"))})
        again = delivery.select([card("chunk B of the same card")], {"status": "judged"}, [], query="q", delivered=given)
        self.assertEqual(again["records"], [])

    def test_a_suggestion_stays_blocked_for_the_whole_session(self):
        # 第二輪審查：只看最近 10 輪會在內容還在畫面上時重送（10/5 的實際事件）。
        session = self.store.open_session({"source": "test", "session_key": "s"})["session_id"]
        old = {"records": [], "delivery": {"records": [{"evidence_key": "ev1:old"}]}}
        self._turn(session, "t00", "q", "a", "2026-10-06T00:00:00", old)
        for i in range(1, 16):
            self._turn(session, f"t{i:02d}", "q", "a", f"2026-10-06T00:{i:02d}:00")
        with mock.patch.object(self.store, "knowledge_recall", return_value={"records": []}) as run:
            self.store.start_turn({"session_id": session, "turn_id": "now", "query": "next"})
        self.assertIn("ev1:old", run.call_args.kwargs["delivered"])

    def test_own_traces_stay_excluded_for_the_whole_session(self):
        mine = self.store.open_session({"source": "test", "session_key": "mine"})["session_id"]
        self._turn(mine, "early", "coefficient version", "my own early reply", "2026-10-06T00:00:00")
        for i in range(15):
            self._turn(mine, f"r{i}", "coefficient version", f"recent reply {i}", f"2026-10-06T01:{i:02d}:00")
        self.assertEqual(self.store.recall("coefficient version", 20, exclude_session=mine)["memories"], [])

    def test_a_format_change_at_deploy_does_not_resend(self):
        card = {**record("card-x", 0.9, "chunk"), "record_type": "knowledge_chunk"}
        stored_by_previous_version = frozenset({(delivery.identity_key(card), "0123456789abcdef")})
        again = delivery.select([card], {"status": "judged"}, [], query="q", delivered=stored_by_previous_version)
        self.assertEqual(again["records"], [])

    def test_report_still_raises_real_database_errors(self):
        import sqlite3
        import xkb_delivery_report as report
        path = Path(self.tmp.name) / "broken.sqlite"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE turns(retrieval_json TEXT, started_at TEXT, status TEXT)")
        db.execute("CREATE TABLE delivery_outcomes(evidence_key TEXT, recorded_at TEXT)")  # no overlap column
        db.commit(); db.close()
        with self.assertRaises(sqlite3.OperationalError):
            report.report(path, 7)

    def test_source_ids_cover_traces_and_never_include_missing_ids(self):
        packet = {"records": [{"id": "a"}, {"trace_id": "trace:r"}, {}],
                  "delivery": {"records": [{"trace_id": "trace:x"}, {"id": "a"}, {}]}}
        self.assertEqual(service.source_memory_ids(packet), ["a", "trace:r", "trace:x"])

    def test_a_cut_source_reference_is_marked(self):
        import xkb_agent_hook as hook
        rendered = hook.render([{"title": "t", "summary": "body", "source_url": "https://example.test/" + "p" * 400}])
        self.assertIn("p [來源過長，以上已截斷]", rendered)

    def test_a_delivered_record_beyond_the_limit_counts_as_returned(self):
        hits = [{**record(f"old{i}", 0.9, f"given step {i}"), "record_type": "knowledge_card"} for i in range(10)]                + [{**record("fresh", 0.8, "new step"), "record_type": "knowledge_card"}]
        given = frozenset(delivery.identity_key(h) for h in hits[:10])
        with mock.patch.object(self.store.catalog, "search", return_value={"records": [dict(h) for h in hits]}),              mock.patch.object(self.store, "recall", return_value={"memories": []}),              mock.patch.object(service, "judge_relevance", side_effect=lambda q, r: (r, {"status": "judged"})):
            packet = self.store.knowledge_recall("next", 10, delivered=given)
        fresh = delivery.identity_key(hits[10])
        with self.store.connect() as db:
            row = db.execute("SELECT returned_count FROM recall_usage WHERE record_id=?", (fresh,)).fetchone()
        self.assertEqual(row[0], 1)
        self.assertIn("fresh", service.source_memory_ids(packet))

    def test_a_ragged_index_is_not_rebuilt_on_every_query(self):
        import numpy as np
        ragged = {"a": [1.0, 0.0], "b": [1.0, 0.0, 0.0]}
        cr._score_all(ragged, [1.0, 0.0])
        with mock.patch.object(np, "asarray", wraps=np.asarray) as convert:
            cr._score_all(ragged, [1.0, 0.0])
        index_rebuilds = [c for c in convert.call_args_list
                          if c.args and isinstance(c.args[0], list) and c.args[0] and isinstance(c.args[0][0], list)]
        self.assertEqual(index_rebuilds, [])

    def test_three_full_excerpts_with_long_sources_all_reach_the_agent(self):
        import xkb_agent_hook as hook
        items = [{"record_type": "knowledge_card", "title": f"title {i}", "summary": "x" * delivery.EXCERPT_CHARS,
                  "source_url": "https://example.test/" + "p" * 500} for i in range(3)]
        self.assertEqual(hook.render(items).count("title "), 3)

    def test_report_tolerates_a_database_without_the_outcomes_table(self):
        import sqlite3
        import xkb_delivery_report as report
        path = Path(self.tmp.name) / "old.sqlite"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE turns(retrieval_json TEXT, started_at TEXT, status TEXT)")
        db.commit(); db.close()
        self.assertEqual(report.report(path, 7)["suggestions"], 0)

    def test_the_excerpt_label_does_not_count_against_reuse(self):
        session = self.store.open_session({"source": "test", "session_key": "s"})["session_id"]
        given = {"records": [], "delivery": {"records": [
            {"evidence_key": "ev1:x", "summary": "單一鏡頭只安排一個核心動作\n（節錄）"}]}}
        with mock.patch.object(self.store, "knowledge_recall", return_value=given):
            self.store.start_turn({"session_id": session, "turn_id": "t", "query": "q"})
        self.store.complete_turn("t", {"session_id": session, "query": "q", "answer": "單一鏡頭只安排一個核心動作"})
        with self.store.connect() as db:
            overlap = db.execute("SELECT overlap FROM delivery_outcomes").fetchone()[0]
        self.assertEqual(overlap, 1.0)



class CompactionBoundsWhatIsStillVisible(unittest.TestCase):
    """第三輪審查：輪數視窗在「整段」與「10 輪」之間來回，因為輪數只是在猜。
    回答者看得到什麼，由上次壓縮決定。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = service.Store(Path(self.tmp.name) / "knowledge.sqlite")
        self.session = self.store.open_session({"source": "test", "session_key": "s"})["session_id"]

    def _turn(self, turn_id, at, retrieval=None, answer="a", query="q"):
        with self.store.connect() as db:
            db.execute("INSERT INTO turns(turn_id,session_id,query,answer,status,trace_id,retrieval_json,started_at,completed_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (turn_id, self.session, query, answer, "succeeded", "trace:" + turn_id,
                                                     json.dumps(retrieval or {}), at, at))

    def _delivered(self, **body):
        import uuid
        with mock.patch.object(self.store, "knowledge_recall", return_value={"records": []}) as run:
            self.store.start_turn({"session_id": self.session, "turn_id": f"now-{uuid.uuid4()}", "query": "next", **body})
        return run.call_args.kwargs

    def test_suggestions_before_the_last_compaction_may_return(self):
        given = lambda key: {"records": [], "delivery": {"records": [{"evidence_key": key}]}}
        self._turn("before", "2026-10-06T01:00:00+00:00", given("ev1:before"))
        self._turn("after", "2026-10-06T03:00:00+00:00", given("ev1:after"))
        kwargs = self._delivered(context_since="2026-10-06T02:00:00.000Z")
        self.assertEqual(kwargs["delivered"], frozenset({"ev1:after"}))
        self.assertEqual(kwargs["context_since"], "2026-10-06T02:00:00+00:00")
        self.assertEqual(self._delivered()["delivered"], frozenset({"ev1:before", "ev1:after"}))

    def test_no_turn_count_limit_inside_the_visible_range(self):
        self._turn("first", "2026-10-06T00:00:00+00:00", {"records": [], "delivery": {"records": [{"evidence_key": "ev1:first"}]}})
        with self.store.connect() as db:
            db.executemany("INSERT INTO turns(turn_id,session_id,query,status,retrieval_json,started_at) VALUES(?,?,?,?,?,?)",
                           [(f"t{i}", self.session, "q", "succeeded", "not json", f"2026-10-06T01:{i // 60:02d}:{i % 60:02d}+00:00")
                            for i in range(250)])
        self.assertIn("ev1:first", self._delivered()["delivered"])

    def test_own_decisions_before_the_last_compaction_are_recallable(self):
        self._turn("early", "2026-10-06T01:00:00+00:00", answer="Agreed: record the coefficient version.",
                   query="coefficient version")
        self._turn("late", "2026-10-06T03:00:00+00:00", answer="still on screen", query="coefficient version")
        visible = self.store.recall("coefficient version", 10, exclude_session=self.session,
                                    exclude_since="2026-10-06T02:00:00+00:00")
        self.assertEqual([m["answer"] for m in visible["memories"]], ["Agreed: record the coefficient version."])

    def test_hook_reads_only_a_real_compaction_boundary(self):
        import xkb_agent_hook as hook
        path = Path(self.tmp.name) / "t.jsonl"
        talk = {"type": "user", "timestamp": "2026-10-06T05:00:00Z",
                "message": {"role": "user", "content": 'it logs "subtype":"compact_boundary" lines'}}
        lines = [{"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-06T01:00:00Z"},
                 {"type": "user", "message": {"role": "user", "content": "x" * 3_000_000}},
                 {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-06T04:00:00Z"},
                 {"type": "user", "message": {"role": "user", "content": "y" * 1_500_000}},
                 talk]
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        self.assertEqual(hook.last_compaction(str(path)), "2026-10-06T04:00:00Z")
        path.write_text(json.dumps(talk) + "\n", encoding="utf-8")
        self.assertEqual(hook.last_compaction(str(path)), "")
        self.assertEqual(hook.last_compaction(str(Path(self.tmp.name) / "missing.jsonl")), "")

    def test_anonymous_delivery_does_not_mark_other_anonymous_records_returned(self):
        records = [{**record(f"r{i}", 0.9, f"step {i}"), "record_type": "knowledge_card"} for i in range(2)]
        anonymous = [{"summary": f"anonymous step {i}", "judge": 0.6, "score": 0.5, "namespace": "private"} for i in range(3)]
        with mock.patch.object(self.store.catalog, "search", return_value={"records": records + anonymous}), \
             mock.patch.object(self.store, "recall", return_value={"memories": []}), \
             mock.patch.object(service, "judge_relevance", side_effect=lambda q, r: (r, {"status": "judged"})), \
             mock.patch.object(self.store, "record_recall_usage") as usage:
            self.store.knowledge_recall("next", 1)
        returned = usage.call_args.args[2]
        self.assertEqual([r.get("evidence_key") for r in returned if not r.get("evidence_key")], [])


if __name__ == "__main__":
    unittest.main()
