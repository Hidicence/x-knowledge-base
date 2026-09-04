from __future__ import annotations

import importlib.util
import tempfile
import unittest
import unittest.mock
from unittest import mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xkb_memory_service.py"
spec = importlib.util.spec_from_file_location("xkb_memory_service", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Store = module.Store

_wiki_patch = None


def setUpModule() -> None:
    """讓測試不要碰到真實的知識庫。

    recall 加入 wiki 搜尋之後，任何呼叫 knowledge_recall 的測試都會去載入
    使用者真實的語意索引（本機 1,872 筆），再對 Gemini 打一次 embedding。
    那讓測試變慢、要花錢、依賴網路，而且結果隨著知識庫內容改變——
    在這台機器上甚至會 segfault。

    預設回空，個別測試要驗 wiki 行為時再自己 patch。
    """
    global _wiki_patch
    _wiki_patch = mock.patch.object(module.KnowledgeCatalog, "_wiki_search", return_value=[])
    _wiki_patch.start()


def tearDownModule() -> None:
    if _wiki_patch is not None:
        _wiki_patch.stop()


class XKBMemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "memory.sqlite")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_session_turn_complete_recall_and_idempotency(self) -> None:
        session = self.store.open_session(
            {
                "source": "fixture",
                "agent_id": "fixture",
                "namespace": "private",
                "session_key": "session-1",
            }
        )
        resumed = self.store.open_session(
            {
                "source": "fixture",
                "agent_id": "fixture",
                "namespace": "private",
                "session_key": "session-1",
            }
        )
        self.assertFalse(session["resumed"])
        self.assertTrue(resumed["resumed"])
        turn = self.store.start_turn(
            {
                "session_id": session["session_id"],
                "turn_id": "turn-1",
                "query": "shared memory query",
            }
        )
        self.assertFalse(turn["resumed"])
        completed = self.store.complete_turn(
            "turn-1",
            {
                "session_id": session["session_id"],
                "query": "shared memory query",
                "answer": "shared memory answer",
                "status": "succeeded",
                "content": {"answer": "shared memory answer"},
            },
        )
        duplicate = self.store.complete_turn(
            "turn-1",
            {
                "session_id": session["session_id"],
                "query": "shared memory query",
                "answer": "shared memory answer",
                "status": "succeeded",
                "content": {"answer": "shared memory answer"},
            },
        )
        self.assertTrue(completed["stored"])
        self.assertEqual(completed["retrieval"]["query"], "shared memory query")
        with self.store.connect() as db:
            persisted = db.execute("SELECT status, retrieval_json, trace_id FROM turns WHERE turn_id=?", ("turn-1",)).fetchone()
        self.assertEqual(persisted["status"], "succeeded")
        self.assertEqual(persisted["trace_id"], completed["trace_id"])
        self.assertEqual(__import__("json").loads(persisted["retrieval_json"])["query"], "shared memory query")
        self.assertTrue(duplicate["deduplicated"])
        recall = self.store.recall("shared memory")
        self.assertEqual(recall["count"], 1)
        self.assertIn("shared memory answer", recall["context"])

        artifact = self.store.artifact(completed["trace_id"])
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact["retrieval"]["query"], "shared memory query")

    def test_input_validation_rejects_empty_query_bad_limits_and_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "query is required"):
            self.store.knowledge_recall("  ")
        with self.assertRaisesRegex(ValueError, "limit must be an integer"):
            self.store.knowledge_recall("query", limit="10")
        with self.assertRaisesRegex(ValueError, "between 1 and 50"):
            self.store.knowledge_recall("query", limit=0)
        session = self.store.open_session({"source": "fixture", "session_key": "validation"})
        with self.assertRaisesRegex(ValueError, "namespace is required"):
            self.store.open_session({"source": "fixture", "session_key": "bad", "namespace": ""})
        self.store.start_turn({"session_id": session["session_id"], "turn_id": "validation-turn", "query": "query"})
        with self.assertRaisesRegex(ValueError, "status must be"):
            self.store.complete_turn("validation-turn", {"query": "query", "answer": "answer", "status": "running"})

    def test_cancelled_turn_is_not_stored(self) -> None:
        session = self.store.open_session({"source": "fixture", "session_key": "session-2"})
        self.store.start_turn({"session_id": session["session_id"], "turn_id": "turn-2", "query": "cancel me"})
        result = self.store.complete_turn("turn-2", {"status": "cancelled"})
        self.assertFalse(result["stored"])
        self.assertEqual(self.store.recall("cancel me")["count"], 0)

    def test_unified_recall_includes_external_knowledge_records(self) -> None:
        original = self.store.catalog

        class FixtureCatalog:
            def search(self, query, limit=10, namespace="private"):
                return {
                    "records": [{
                        "record_type": "knowledge_card",
                        "id": "fixture-card",
                        "title": "Fixture knowledge",
                        "summary": "shared knowledge evidence",
                        "score": 3,
                    }]
                }

        self.store.catalog = FixtureCatalog()
        try:
            result = self.store.knowledge_recall("shared knowledge")
        finally:
            self.store.catalog = original
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["records"][0]["record_type"], "knowledge_card")
        self.assertIn("Fixture knowledge", result["context"])

    def test_catalog_fails_closed_across_namespaces_and_exposes_metadata(self) -> None:
        catalog = module.KnowledgeCatalog()
        root = Path(self.tmp.name)
        catalog.cards_dir = root / "cards"
        catalog.wiki_topics_dir = root / "topics"
        catalog.index_file = root / "index.json"
        catalog.cards_dir.mkdir()
        catalog.wiki_topics_dir.mkdir()
        (catalog.cards_dir / "secret.md").write_text("---\nnamespace: team-a\nsensitivity: internal\n---\nsecret", encoding="utf-8")
        (catalog.index_file).write_text('{"items":[{"id":"secret","title":"Secret"}]}', encoding="utf-8")
        self.assertIsNone(catalog.card("secret", "team-b"))
        visible = catalog.card("secret", "team-a")
        self.assertEqual(visible["namespace"], "team-a")
        self.assertEqual(catalog.search("secret", namespace="team-b")["count"], 0)

    def test_semantic_backend_is_used_and_declared(self) -> None:
        original = module.xbrain_query

        def fixture_query(query, *, limit, no_expand, semantic):
            self.assertEqual(query, "semantic fixture")
            self.assertTrue(semantic)
            return [{
                "slug": "fixture-semantic-card",
                "title": "Semantic fixture",
                "chunk_text": "vector evidence",
                "source_url": "https://example.test/source",
                "type": "knowledge-card",
                "score": 0.91,
            }]

        module.xbrain_query = fixture_query
        try:
            # 前提要寫出來。原本這個測試能過，只是因為 fixture 的 slug 剛好
            # 不在這台機器的 cards_index 裡——換一台機器、或哪天真的建了同名
            # 的卡片，斷言就會翻面。測試不該從磁碟繼承它的前提。
            import xkb_relevance
            with unittest.mock.patch.object(xkb_relevance, "similarities",
                                            return_value=None):
                result = self.store.catalog.search("semantic fixture", 3)
        finally:
            module.xbrain_query = original
        self.assertEqual(result["retrieval_mode"], "xbrain_hybrid")
        self.assertEqual(result["records"][0]["retrieval"], "xbrain_hybrid")
        record = result["records"][0]
        # 一個鍵都對不到向量索引時，這裡不做任何比較，也就不該假裝比過：
        # 後端的分數原樣帶著，只加上「沒驗證過」的標籤。
        # 跨層可比是合併點（knowledge_recall → xkb_score.rank）的事，
        # 不是這裡編一個位置出來——我在這個檔案上來回改了三輪才把它放對地方。
        self.assertEqual(record["score"], 0.91)
        self.assertEqual(record.get("score_basis"), "unverified")
        # 沒被驗證過的記錄有自己的尺度。這件事以前是靠在 xkb_relevance 裡
        # 改寫分數做到的，那等於捏造一個測量值；現在 score 保持是後端自己
        # 算的數字，排序交給 xkb_score 的尺度表。
        self.assertEqual(record.get("score_scale"), "unverified")
        self.assertNotIn("_unverified", record)

    def test_layers_are_ordered_by_a_comparable_score_not_raw_numbers(self) -> None:
        """合併之後要照跨層可比的分數排，不是照各層自己的原始數字。

        三層的尺度互不相同：卡片是 gbrain 的 RRF、wiki 是餘弦、對話是關鍵字
        比例（上限 0.65）。合併點原本直接照 score 排，等於拿公分比英吋。

        這是外部審查連續六輪繞著打轉的那個缺陷——每一輪我都在 xkb_relevance
        裡替驗不出相似度的項目找「壓到哪裡才對」，而不管壓到哪裡都會撞上另一層
        的尺度：壓高壓過 wiki 的真餘弦，壓低掉到對話軌跡之下，而後者會讓索引
        壞掉長得像「知識庫裡沒東西」。真正該修的是合併點。

        測試要走真正的路徑。我第一版直接呼叫 xkb_score.rank()，於是把合併點
        改回原始排序它照樣是綠的——測到的是那個函式，不是那條路。
        """
        def packet_for(card_score: float) -> list[str]:
            knowledge = {
                "records": [
                    {"title": "卡片", "score": card_score, "score_scale": "card_semantic",
                     "record_type": "knowledge_chunk"},
                    {"title": "wiki 結論", "score": 0.68, "score_scale": "wiki_semantic",
                     "record_type": "wiki_topic"},
                ],
                "retrieval_mode": "xbrain_hybrid", "filtered_counts": {},
                "semantic_retrieval_attempted": True,
                "semantic_backend": {"status": "used"},
                "dropped_as_irrelevant": 0,
            }
            conversation = {"memories": [{"query": "對話軌跡", "answer": "", "score": 0.65}]}
            with mock.patch.object(type(self.store.catalog), "search",
                                   return_value=knowledge), \
                 mock.patch.object(type(self.store), "recall", return_value=conversation):
                packet = self.store.knowledge_recall("任意問題", 5, "private")
            return [r.get("title") or r.get("query") for r in packet["records"]]

        strong = packet_for(0.75)
        self.assertEqual(strong[0], "wiki 結論",
                         "消化過的結論該排在相近強度的原始素材之前")
        self.assertLess(strong.index("卡片"), strong.index("對話軌跡"),
                        "語意命中的卡片該排在關鍵字命中的對話軌跡之前")

        # 對話軌跡仍然在封包裡——不是被系統性地丟掉。RRF 之下它就是另一條腿，
        # 照名次貢獻，不再被雙重折扣（呼叫端 0.65 折扣 + 尺度表未折扣錨點）
        # 壓到門檻之下、一填滿 limit 就整段消失。
        marginal = packet_for(0.56)
        self.assertIn("對話軌跡", marginal,
                      "對話層被整段擠出封包")

        # scale mixing 結構上不可能：關鍵字尺度的分數（6~20）跟餘弦尺度的
        # 分數（0~1）在 RRF 裡都只是「腿內第 N 名」，不會因為原始數字大就碾壓。
        import xkb_score
        mixed = xkb_score.rank([
            {"title": "關鍵字命中的書籤", "score": 15.0, "score_scale": "card_keyword"},
            {"title": "餘弦命中的卡片", "score": 0.62, "score_scale": "card_semantic"},
        ])
        self.assertLess(abs(mixed[0]["unified_score"] - mixed[1]["unified_score"]),
                        0.001,
                        "15 分的關鍵字書籤把 0.62 的餘弦卡片碾壓了——scale mixing 回來了")

    def test_each_producer_declares_a_scale_xkb_score_knows(self) -> None:
        """算出分數的地方要標對尺度，而且是 xkb_score 認得的那些。

        source_type 是「這是什麼東西」（會回給 API 用戶），score_scale 是
        「這個數字怎麼來的」。標錯或沒標，整層就會用錯錨點對齊，而且不會有
        任何錯誤訊息——不認得的尺度會靜靜掉到 0.5 的 fallback。

        我第一版這個測試是在原始碼文字裡找 `score_scale` 這個子字串，於是
        它對「值是錯的」完全無感——寫這個測試的當下就有兩個標籤是錯的，
        它照樣是綠的。改成檢查實際產出的值。
        """
        import xkb_score

        known = set(xkb_score.DEFAULT_ANCHORS) & set(xkb_score.DEFAULT_WEIGHTS)

        def fixture_query(query, *, limit, no_expand, semantic):
            return [{"slug": "s", "title": "t", "chunk_text": "c",
                     "source_url": "", "type": "knowledge-card", "score": 0.8}]

        original = module.xbrain_query
        module.xbrain_query = fixture_query
        try:
            packet = self.store.knowledge_recall("尺度測試", 5, "private")
        finally:
            module.xbrain_query = original

        scales = {r.get("score_scale") for r in packet["records"]}
        self.assertTrue(scales, "一筆記錄都沒有，測不到尺度")
        self.assertNotIn(None, scales, "有記錄沒有標尺度")
        for scale in scales:
            self.assertIn(scale, known,
                          f"{scale} 不在 xkb_score 的尺度表裡，會靜靜掉到 fallback")

    def test_every_record_template_declares_its_specific_scale(self) -> None:
        """每一個產生分數的樣板都要標對尺度——包括這個測試檔案 mock 掉的那些。

        動態的那個測試只走得到 fixture 那一筆：setUpModule 把 _wiki_search 整個
        換成回空陣列、store 也是空的，所以 wiki 與對話那兩個樣板沒有任何測試
        看著。我刪掉的靜態測試本來守著它們，替換的版本反而涵蓋更少。

        而靜態檢查要看**值**。我更早的版本只找 score_scale 這個欄位名，
        於是四個標籤裡有兩個是錯的時候它照樣是綠的。
        """
        source = (Path(module.__file__).parent / "xkb_memory_service.py").read_text(
            encoding="utf-8")
        expected = {
            '"retrieval": "xbrain_hybrid"': "card",
            '"retrieval": "wiki_semantic"': "wiki_semantic",
            '"record_type": "conversation_trace"': "conversation",
        }
        for marker, scale in expected.items():
            idx = source.index(marker)
            window = source[max(0, idx - 700):idx + 300]
            self.assertIn(f'"score_scale": "{scale}"', window,
                          f"{marker} 的樣板沒有標成 {scale}")

    def test_the_keyword_fallback_declares_a_keyword_scale(self) -> None:
        """關鍵字退路的分數是 0~1 的覆蓋率，不是 RRF 也不是餘弦。

        這條路徑上的記錄一度被標成 card / wiki_semantic——用餘弦與 RRF 的錨點
        去對齊覆蓋率，會把真實的先後順序翻過來，正是收斂想消滅的那件事，
        只是搬到了退路上。上一版的測試連這兩個樣板都沒看到。
        """
        source = (Path(module.__file__).parent / "xkb_memory_service.py").read_text(
            encoding="utf-8")
        for marker in ('"retrieval": "keyword"',):
            idx = 0
            found = 0
            while True:
                idx = source.find(marker, idx + 1)
                if idx < 0:
                    break
                found += 1
                template = source[max(0, idx - 700):idx]
                self.assertRegex(
                    template, r'"score_scale": "(card_keyword|wiki_keyword)"',
                    "關鍵字退路的記錄要標關鍵字尺度")
            self.assertGreater(found, 0, "找不到關鍵字退路的記錄樣板")

    def test_recall_packet_explains_acl_namespace_filtering_and_backend(self) -> None:
        original = module.xbrain_query

        def fixture_query(query, *, limit, no_expand, semantic):
            return [
                {"slug": "allowed", "title": "Allowed", "chunk_text": "visible", "namespace": "team-a"},
                {"slug": "hidden", "title": "Hidden", "chunk_text": "secret", "namespace": "team-b"},
            ]

        module.xbrain_query = fixture_query
        try:
            result = self.store.knowledge_recall("acl packet", namespace="team-a")
        finally:
            module.xbrain_query = original
        self.assertEqual(result["request_namespace"], "team-a")
        self.assertEqual(result["acl_policy"]["decision"], "allow_public_or_matching_namespace")
        self.assertEqual(result["filtered_counts"]["semantic"], 1)
        self.assertIn("records_filtered_by_acl", result["warnings"])
        self.assertEqual(result["semantic_backend"]["status"], "used")
        # The breakdown must say which layer was filtered, otherwise "nothing
        # came back" and "the wiki layer was filtered out" look identical.
        self.assertEqual(result["filtered_counts"]["total"], 1)
        self.assertEqual(result["filtered_counts"]["by_layer"]["semantic"], 1)
        self.assertEqual(result["filtered_counts"]["by_layer"]["conversation"], 0)

    def test_acl_filter_counts_are_attributed_per_layer(self) -> None:
        """A quiet result must say which layer the ACL removed."""
        module = importlib.import_module("xkb_memory_service")
        original = module.xbrain_query
        module.xbrain_query = None  # force the keyword path, where layers are known
        try:
            result = self.store.knowledge_recall("anything", namespace="team-a")
        finally:
            module.xbrain_query = original
        counts = result["filtered_counts"]
        self.assertEqual(result["retrieval_mode"], "keyword_fallback")
        self.assertEqual(set(counts["by_layer"]), {"card", "wiki", "semantic", "conversation"})
        self.assertEqual(counts["total"], sum(counts["by_layer"].values()))
        # No semantic backend ran, so nothing can be attributed to it.
        self.assertEqual(counts["by_layer"]["semantic"], 0)
        # Legacy channel keys stay consistent with the layer breakdown.
        self.assertEqual(counts["keyword"], counts["by_layer"]["card"] + counts["by_layer"]["wiki"])

    def test_turn_start_persists_retrieval_packet_and_source_ids(self) -> None:
        original = self.store.knowledge_recall
        packet = {
            "schema": "xkb-knowledge-service.v1",
            "query": "turn query",
            "records": [{"id": "knowledge-1", "record_type": "knowledge_chunk", "score": 0.8}],
            "context": "historical evidence",
            "retrieval_mode": "xbrain_hybrid",
            "semantic_retrieval_attempted": True,
            "warnings": [],
        }
        self.store.knowledge_recall = lambda query, limit=10, namespace="private": packet
        try:
            session = self.store.open_session({"source": "fixture", "session_key": "retrieval-session"})
            started = self.store.start_turn({"session_id": session["session_id"], "turn_id": "retrieval-turn", "query": "turn query"})
            resumed = self.store.start_turn({"session_id": session["session_id"], "turn_id": "retrieval-turn", "query": "turn query"})
        finally:
            self.store.knowledge_recall = original
        self.assertEqual(started["source_memory_ids"], ["knowledge-1"])
        self.assertEqual(started["retrieval"]["retrieval_mode"], "xbrain_hybrid")
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["retrieval"]["query"], "turn query")

    def test_turn_start_rejects_idempotency_conflicts(self) -> None:
        first = self.store.open_session({"source": "fixture", "session_key": "identity-1", "namespace": "private"})
        other = self.store.open_session({"source": "fixture", "session_key": "identity-2", "namespace": "private"})
        self.store.start_turn({"session_id": first["session_id"], "turn_id": "identity-turn", "query": "original"})
        with self.assertRaisesRegex(ValueError, "session_id"):
            self.store.start_turn({"session_id": other["session_id"], "turn_id": "identity-turn", "query": "original"})
        with self.assertRaisesRegex(ValueError, "query"):
            self.store.start_turn({"session_id": first["session_id"], "turn_id": "identity-turn", "query": "changed"})
        with self.assertRaisesRegex(ValueError, "namespace"):
            self.store.start_turn({"session_id": first["session_id"], "turn_id": "identity-turn", "query": "original", "namespace": "shared"})

    def test_recall_is_scoped_to_session_namespace_and_start_requires_matching_namespace(self) -> None:
        private = self.store.open_session({"source": "fixture", "session_key": "private", "namespace": "private"})
        shared = self.store.open_session({"source": "fixture", "session_key": "shared", "namespace": "shared"})
        self.store.start_turn({"session_id": private["session_id"], "turn_id": "private-turn", "query": "same namespace secret"})
        self.store.complete_turn("private-turn", {"query": "same namespace secret", "answer": "private answer"})
        self.store.start_turn({"session_id": shared["session_id"], "turn_id": "shared-turn", "query": "same namespace secret"})
        self.store.complete_turn("shared-turn", {"query": "same namespace secret", "answer": "shared answer"})
        self.assertEqual([m["answer"] for m in self.store.recall("same namespace", namespace="private")["memories"]], ["private answer"])
        self.assertEqual([m["answer"] for m in self.store.recall("same namespace", namespace="shared")["memories"]], ["shared answer"])
        with self.assertRaisesRegex(ValueError, "namespace conflicts with session"):
            self.store.start_turn({"session_id": private["session_id"], "turn_id": "bad-namespace", "query": "q", "namespace": "shared"})

    def test_turn_start_rejects_unknown_session(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown session_id"):
            self.store.start_turn({"session_id": "missing", "turn_id": "missing-turn", "query": "q"})

    def test_pipeline_snapshot_is_read_only_and_lists_known_stages(self) -> None:
        snapshot = self.store.catalog.pipeline_snapshot(days=7)
        self.assertTrue(snapshot["read_only"])
        self.assertEqual(snapshot["control_plane"], "observed_status_only")
        stages = {item["stage"] for item in snapshot["stages"]}
        self.assertEqual(
            stages,
            {"ingest", "card_generation", "index", "distill", "promotion", "publish"},
        )
        self.assertTrue(all(item["control"] == "read_only" for item in snapshot["stages"]))
        self.assertIn("summary", snapshot)

    def test_job_event_is_persisted_and_filterable_without_running_worker(self) -> None:
        created = self.store.record_job_event({
            "job_id": "job-1",
            "stage": "index",
            "worker": "build_vector_index.py",
            "status": "running",
            "input_ref": "cards:batch-1",
            "metadata": {"count": 3},
        })
        self.assertTrue(created["stored"])
        self.assertTrue(created["observed_only"])
        completed = self.store.record_job_event({
            "job_id": "job-1",
            "stage": "index",
            "worker": "build_vector_index.py",
            "status": "succeeded",
            "output_ref": "vector-index:batch-1",
        })
        self.assertEqual(completed["status"], "succeeded")
        result = self.store.list_jobs(stage="index", status="succeeded")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["jobs"][0]["output_ref"], "vector-index:batch-1")
        self.assertFalse(self.store.list_jobs(stage="distill")["count"])

class RelevanceAndIntentTests(unittest.TestCase):
    """Injecting ten results into every turn is what makes an unrelated
    question cost as much as a real one."""

    def setUp(self) -> None:
        self.module = module

    def test_acknowledgements_and_greetings_skip_retrieval_entirely(self) -> None:
        skip = self.module.Store._skip_reason
        self.assertEqual(skip("好"), "acknowledgement")
        self.assertEqual(skip("謝謝"), "acknowledgement")
        self.assertEqual(skip("早安"), "greeting")
        self.assertEqual(skip("hi"), "greeting")

    def test_real_questions_are_never_skipped(self) -> None:
        """Eight Chinese characters is a question, not chitchat."""
        skip = self.module.Store._skip_reason
        self.assertEqual(skip("碳盤查的計算方式"), "")
        self.assertEqual(skip("XKB 召回"), "")
        self.assertEqual(skip("我們之前怎麼處理碳盤查的"), "")

    def test_vector_key_maps_slug_onto_index_key(self) -> None:
        import xkb_relevance
        self.assertEqual(xkb_relevance.vector_key("01-topic/12345"), "01-topic/12345.md")
        self.assertEqual(xkb_relevance.vector_key("01-topic/12345.md"), "01-topic/12345.md")
        self.assertEqual(xkb_relevance.vector_key("https://x.com/i/status/1"), "")
        self.assertEqual(xkb_relevance.vector_key(""), "")

    def test_rank_score_is_replaced_by_measured_similarity(self) -> None:
        """The backend's score says "ranked first", not "is relevant"."""
        catalog = self.module.KnowledgeCatalog()
        records = [{"id": "a/1", "score": 0.88}, {"id": "a/2", "score": 0.87}]
        with mock.patch("xkb_relevance.similarities",
                               return_value={"a/1.md": 0.72, "a/2.md": 0.30}):
            kept, dropped = catalog._drop_irrelevant("q", records)
        self.assertEqual(dropped, 1)
        self.assertEqual([item["id"] for item in kept], ["a/1"])
        self.assertEqual(kept[0]["score"], 0.72)
        self.assertEqual(kept[0]["rank_score"], 0.88)

    def test_unavailable_similarity_passes_records_through(self) -> None:
        """A missing index must not silently delete every result."""
        catalog = self.module.KnowledgeCatalog()
        records = [{"id": "a/1", "score": 0.88}]
        with mock.patch("xkb_relevance.similarities", return_value=None):
            kept, dropped = catalog._drop_irrelevant("q", records)
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_warnings_separate_broken_backend_from_nothing_relevant(self) -> None:
        broken = self.module._recall_warnings(
            {"retrieval_mode": "keyword_fallback", "dropped_as_irrelevant": 0}, {})
        self.assertIn("semantic_backend_unavailable_or_empty", broken[0])
        nothing_relevant = self.module._recall_warnings(
            {"retrieval_mode": "keyword_fallback", "dropped_as_irrelevant": 10}, {})
        self.assertIn("relevance floor", nothing_relevant[0])
        self.assertNotIn("unavailable", nothing_relevant[0])


class KnowledgeRetirementTests(unittest.TestCase):
    """Wiki and cards only ever grew; nothing ever aged out."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "memory.sqlite")
        self.addCleanup(self.tmp.cleanup)

    def test_records_that_never_clear_the_floor_are_reported_as_cold(self) -> None:
        for _ in range(6):
            self.store.record_usage([("cold/1", 0.31, False), ("warm/1", 0.72, True)])
        report = self.store.cold_knowledge(min_considered=5)
        self.assertEqual([r["record_id"] for r in report["records"]], ["cold/1"])
        self.assertEqual(report["tracked_records"], 2)
        self.assertEqual(report["ever_useful"], 1)

    def test_useful_records_are_never_listed_however_often_retrieved(self) -> None:
        for _ in range(20):
            self.store.record_usage([("warm/1", 0.40, False)])
        self.store.record_usage([("warm/1", 0.80, True)])
        report = self.store.cold_knowledge(min_considered=5)
        self.assertEqual(report["records"], [])

    def test_counters_accumulate_and_keep_the_best_similarity(self) -> None:
        self.store.record_usage([("a/1", 0.30, False)])
        self.store.record_usage([("a/1", 0.51, False)])
        row = self.store.cold_knowledge(min_considered=1)["records"][0]
        self.assertEqual(row["considered_count"], 2)
        self.assertEqual(row["injected_count"], 0)
        self.assertAlmostEqual(row["best_similarity"], 0.51, places=4)
        self.assertIsNone(row["last_injected_at"])

    def test_retirement_is_advisory_only(self) -> None:
        """Provenance is the point; nothing may be deleted automatically."""
        self.store.record_usage([("cold/1", 0.10, False)] * 1)
        report = self.store.cold_knowledge(min_considered=1)
        self.assertFalse(report["automatic_retirement"])
        self.assertTrue(report["read_only"])

    def test_usage_accounting_never_breaks_recall(self) -> None:
        catalog = module.KnowledgeCatalog()
        catalog.usage_sink = lambda observations: (_ for _ in ()).throw(RuntimeError("db down"))
        with mock.patch("xkb_relevance.similarities", return_value={"a/1.md": 0.9}):
            kept, dropped = catalog._drop_irrelevant("q", [{"id": "a/1", "score": 0.88}])
        self.assertEqual((len(kept), dropped), (1, 0))


class QueryEmbeddingCacheTests(unittest.TestCase):
    """The same query is embedded twice per recall: once by hybrid search,
    once by the relevance filter. The second call is a wasted round trip."""

    def test_repeated_query_is_embedded_once(self) -> None:
        import continuity_recall as cr
        cr._QUERY_VECTORS.clear()
        calls = []

        def fake(query):
            calls.append(query)
            return [0.1, 0.2]

        with mock.patch.object(cr, "_embed_query_uncached", side_effect=fake):
            first = cr._embed_query("same question")
            second = cr._embed_query("same question")
            cr._embed_query("different question")
        self.assertEqual(first, second)
        self.assertEqual(calls, ["same question", "different question"])
        cr._QUERY_VECTORS.clear()

    def test_failure_is_cached_too_so_a_dead_provider_is_not_retried(self) -> None:
        import continuity_recall as cr
        cr._QUERY_VECTORS.clear()
        calls = []
        with mock.patch.object(cr, "_embed_query_uncached", side_effect=lambda q: calls.append(q) or None):
            self.assertIsNone(cr._embed_query("q"))
            self.assertIsNone(cr._embed_query("q"))
        self.assertEqual(len(calls), 1)
        cr._QUERY_VECTORS.clear()

    def test_cache_is_bounded(self) -> None:
        import continuity_recall as cr
        cr._QUERY_VECTORS.clear()
        with mock.patch.object(cr, "_embed_query_uncached", return_value=[0.0]):
            for i in range(cr._QUERY_VECTOR_LIMIT + 5):
                cr._embed_query(f"q{i}")
        self.assertLessEqual(len(cr._QUERY_VECTORS), cr._QUERY_VECTOR_LIMIT)
        cr._QUERY_VECTORS.clear()


if __name__ == "__main__":
    unittest.main()
