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

import category_classifier as cc  # noqa: E402


class TaxonomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "category-taxonomy.json"
        patcher = mock.patch.object(cc, "RUNTIME_TAXONOMY_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_runtime_categories_are_not_counted_twice(self) -> None:
        self.path.write_text(json.dumps({"categories": ["07-esg"]}), encoding="utf-8")
        cats = cc.taxonomy()
        self.assertEqual(cats.count("07-esg"), 1)
        self.assertEqual(len(cats), len(set(cats)))

    def test_new_category_lands_in_data_not_in_skill_code(self) -> None:
        """分類是使用者的知識結構,不該寫進工具的程式碼。"""
        self.assertTrue(cc.register_category("07-esg-reporting", reason="測試"))
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("07-esg-reporting", stored["categories"])
        self.assertIn("測試", stored["added"]["07-esg-reporting"]["reason"])
        self.assertIn("at", stored["added"]["07-esg-reporting"])

    def test_existing_category_is_not_registered_again(self) -> None:
        self.assertFalse(cc.register_category("99-general"))
        self.assertFalse(self.path.exists())

    def test_slug_rejects_free_form_names(self) -> None:
        self.assertEqual(cc._slug("ESG Reporting!!"), "esg-reporting")
        self.assertEqual(cc._slug("   "), "")


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 兩個檔都要隔離。只隔離 taxonomy 的話，提案會寫進真實資料目錄——
        # 跑過幾次之後真的在 memory/x-knowledge-base/ 留下一筆 count=5 的 07-esg，
        # 而且票數跨測試累積，測試結果開始取決於它之前跑過幾次。
        for name, attr in (("category-taxonomy.json", "RUNTIME_TAXONOMY_PATH"),
                           ("category-proposals.json", "PROPOSALS_PATH")):
            patcher = mock.patch.object(cc, attr, Path(self.tmp.name) / name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _llm(self, payload: dict):
        return mock.patch.object(cc, "_llm_call", return_value=json.dumps(payload, ensure_ascii=False))

    def test_llm_failure_falls_back_and_never_blocks_ingest(self) -> None:
        """LLM 掛掉時攝取仍要能繼續——擋住攝取比分錯類更糟。"""
        with mock.patch.object(cc, "_llm_call", side_effect=RuntimeError("provider down")):
            result = cc.classify_content("gpt-image 出圖提示詞")
        self.assertFalse(result["llm"])
        self.assertEqual(result["confidence"], "low")
        self.assertTrue(result["category"])

    def test_existing_category_is_used_as_returned(self) -> None:
        with self._llm({"category": "06-visual-ai-prompts", "confidence": "high", "tags": ["a"]}):
            result = cc.classify_content("gpt-image")
        self.assertEqual(result["category"], "06-visual-ai-prompts")
        self.assertFalse(result["new_category"])

    def test_new_category_needs_high_confidence(self) -> None:
        """低信心時不新增分類——分類一旦長出來就會留著。"""
        with self._llm({"category": "NEW_CATEGORY", "new_category": "07-esg",
                        "confidence": "medium", "tags": []}):
            result = cc.classify_content("碳盤查報告")
        self.assertNotEqual(result["category"], "07-esg")
        self.assertNotIn("07-esg", cc.taxonomy())

    def test_high_confidence_new_category_is_only_a_proposal(self) -> None:
        """一票不開。規則是同一個名字累積到門檻才開。

        這個測試原本釘的是相反的行為（一筆高信心就 register 成永久分類）。而
        wiki topic 那一層早就是「被提議到門檻才值得開一頁」，分類這一層不該不一樣
        ——門檻機制與完整理由見 tests/test_new_categories_wait_for_a_quorum.py。
        """
        with self._llm({"category": "NEW_CATEGORY", "new_category": "07-esg",
                        "confidence": "high", "reason": "碳盤查", "tags": []}):
            result = cc.classify_content("碳盤查報告")
        self.assertNotEqual(result["category"], "07-esg")
        self.assertIn(result["category"], cc.taxonomy())
        self.assertEqual(result["proposed_category"], "07-esg")
        self.assertNotIn("07-esg", cc.taxonomy())

    def test_the_quorum_opens_it_exactly_once(self) -> None:
        with self._llm({"category": "NEW_CATEGORY", "new_category": "07-esg",
                        "confidence": "high", "reason": "碳盤查", "tags": []}):
            for _ in range(cc.PROMOTE_AFTER):
                result = cc.classify_content("碳盤查報告")
        self.assertEqual(result["category"], "07-esg")
        self.assertEqual(cc.taxonomy().count("07-esg"), 1)

    def test_allow_new_false_blocks_invention(self) -> None:
        with self._llm({"category": "NEW_CATEGORY", "new_category": "07-esg",
                        "confidence": "high", "tags": []}):
            result = cc.classify_content("碳盤查報告", allow_new=False)
        self.assertNotEqual(result["category"], "07-esg")


class ApplyCategoryTests(unittest.TestCase):
    def test_replaces_existing_frontmatter_field_only(self) -> None:
        card = "---\nid: 1\ncategory: 99-general\n---\n\n# T\n\ncategory: 不該被改\n"
        out = cc.apply_category(card, "06-visual-ai-prompts")
        self.assertIn("category: 06-visual-ai-prompts", out)
        self.assertIn("category: 不該被改", out)
        self.assertNotIn("category: 99-general", out)

    def test_inserts_when_absent(self) -> None:
        out = cc.apply_category("---\nid: 1\n---\n\n# T\n", "06-visual-ai-prompts")
        self.assertIn("category: 06-visual-ai-prompts", out)

    def test_empty_category_leaves_card_untouched(self) -> None:
        card = "---\nid: 1\n---\n"
        self.assertEqual(cc.apply_category(card, ""), card)


if __name__ == "__main__":
    unittest.main()
