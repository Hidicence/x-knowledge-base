"""重新分類要只動分類，而且要分得出「壞掉」跟「沒分對」。

1,706 筆索引裡有 68 筆的 category 在正式清單外。這支腳本要處理它們，而這個專案
在「批次改卡片」上踩過的坑都在這份測試裡：

    只標了索引那一列指向的檔 → 索引去重挑了沒標的那一份，等於沒發生（2026-09-10）
    改完 frontmatter 只做增量重建 → 沒動到判斷條件的列不會重讀（2026-09 migrate_schema）
    把壞掉的東西「修好」→ 模板漏進檔案的卡片被分類，看起來就正常了
    自己抄一份清單 → 跟 taxonomy() 不一致，而那是使用者的知識結構
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import category_classifier  # noqa: E402
import xkb_recategorize as rc  # noqa: E402


class BrokenIsNotMiscategorised(unittest.TestCase):
    """模板漏進檔案是內容壞了。幫它分類只會讓壞卡片看起來正常。"""

    def test_it_spots_the_template_leak_found_in_the_data(self):
        for text in ("---\ncategory: <category or empty>\n---\n# <clean title>\n",
                     "---\ntitle: <clean title>\n---\n"):
            with self.subTest(text=text[:30]):
                self.assertIsNotNone(rc.broken_reason(text))

    def test_it_spots_a_failed_fetch(self):
        self.assertIsNotNone(
            rc.broken_reason("---\ncategory:\n---\n# X 書籤內容擷取失敗：匿名存取遭封鎖\n"))

    def test_html_in_a_good_card_is_not_broken(self):
        """`<div>`／`<img src=...>` 是內容，不是模板殘跡。

        不要求佔位符裡有空格的話，內容夾一個 HTML 標記的好卡片會被跳過。
        """
        for text in ("---\ncategory: tech\n---\n# 正常標題\n<div>\n<pre>x</pre>\n",
                     '---\ncategory: tech\n---\n# 正常\n<img src="https://x/y.svg">\n',
                     "---\ncategory: tech\n---\n# 比較 a<b 與 c>d 的寫法\n"):
            with self.subTest(text=text[:40]):
                self.assertIsNone(rc.broken_reason(text))


class TheTaxonomyHasOneDefinition(unittest.TestCase):
    def test_off_taxonomy_asks_the_classifier_not_a_local_copy(self):
        items = [{"category": "01-openclaw-workflows"}, {"category": "ai-tools"},
                 {"category": ""}, {"category": "made-up"}]
        with mock.patch.object(category_classifier, "taxonomy",
                               return_value=["01-openclaw-workflows", "made-up"]):
            off = rc.off_taxonomy(items)
        self.assertEqual([i["category"] for i in off], ["ai-tools", ""],
                         "判準要跟著 taxonomy() 走，包含 runtime 註冊的分類")

    def test_a_classifier_answer_off_taxonomy_falls_back(self):
        """分類器回了清單外的東西，不准就這樣寫下去。"""
        with mock.patch.object(category_classifier, "classify_content",
                               return_value={"category": "我自己發明的分類",
                                             "confidence": "high", "llm": True}):
            category, why, _proposed = rc.decide({"category": "tech"}, "內容")
        self.assertEqual(category, "99-general")
        self.assertIn("清單外", why)

    def test_a_valid_answer_is_used_as_is(self):
        with mock.patch.object(category_classifier, "classify_content",
                               return_value={"category": "03-video-prompts",
                                             "confidence": "high", "llm": True}):
            category, _why, _proposed = rc.decide({"category": "tech"}, "內容")
        self.assertEqual(category, "03-video-prompts")

    def test_it_does_not_forbid_new_categories(self):
        """規則不是「不能開新分類」，是「不能馬上開」。

        我一度傳 allow_new=False，那會把提案整個丟掉——跟「一票就開」是相反方向
        的同一種錯。新分類要走候診：同一個名字累積到門檻才開。
        """
        seen = {}

        def spy(content, **kwargs):
            seen.update(kwargs)
            return {"category": "99-general", "confidence": "low", "llm": True,
                    "proposed_category": "", "proposal_count": 0}

        with mock.patch.object(category_classifier, "classify_content", spy):
            rc.decide({"category": "tech"}, "內容")
        self.assertNotIn("allow_new", seen,
                         "不要關掉提案——提案是門檻機制的輸入")

    def test_a_proposal_is_carried_back_to_the_caller(self):
        """沒達門檻時，提議的名字要回到呼叫端，好寫在卡片上。"""
        with mock.patch.object(category_classifier, "classify_content",
                               return_value={"category": "99-general",
                                             "confidence": "high", "llm": True,
                                             "proposed_category": "07-carbon",
                                             "proposal_count": 2}):
            category, why, proposed = rc.decide({"category": "tech"}, "內容")
        self.assertEqual(category, "99-general")
        self.assertEqual(proposed, "07-carbon")
        self.assertIn("2/5", why, "票數要講出來，不然看不出它在等什麼")

    def test_a_keyword_fallback_is_not_reported_as_llm_judgement(self):
        """LLM 掛掉時 classify_content 安靜退回關鍵字，只留 llm: False。

        不把它講出來的話，68 筆全是關鍵字猜的也會被當成「分類器判斷」。
        """
        with mock.patch.object(category_classifier, "classify_content",
                               return_value={"category": "99-general",
                                             "confidence": "low", "llm": False}):
            _category, why, _proposed = rc.decide({"category": "tech"}, "內容")
        self.assertIn("關鍵字", why)
        self.assertNotIn("LLM", why)


class ItWritesTheWholeGroup(unittest.TestCase):
    """索引依 source_url 去重，同一份知識在磁碟上可能有卡片與書籤各一份。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _card(self, name: str, category: str) -> Path:
        path = self.root / name
        path.write_text(f"---\ncategory: {category}\ntitle: t\n---\n# t\n內容\n",
                        encoding="utf-8")
        return path

    def test_every_file_in_the_group_gets_the_category(self):
        files = [self._card("card.md", "tech"), self._card("bookmark.md", "tech")]

        written = rc.apply_to(files, "03-video-prompts")

        self.assertEqual(len(written), 2,
                         "只寫一份的話，索引去重可能挑到沒改的那一份")
        for path in files:
            self.assertIn("category: 03-video-prompts",
                          path.read_text(encoding="utf-8"))

    def test_it_changes_nothing_else(self):
        path = self._card("card.md", "tech")
        before = path.read_text(encoding="utf-8")

        rc.apply_to([path], "99-general")
        after = path.read_text(encoding="utf-8")

        self.assertEqual(before.replace("category: tech", "category: 99-general"),
                         after, "只准改 category 那一行")

    def test_a_file_without_frontmatter_is_left_alone(self):
        path = self.root / "plain.md"
        path.write_text("# 沒有 frontmatter\n", encoding="utf-8")
        self.assertEqual(rc.apply_to([path], "99-general"), [])
        self.assertEqual(path.read_text(encoding="utf-8"), "# 沒有 frontmatter\n")


class DryRunAndRebuild(unittest.TestCase):
    def test_the_default_writes_nothing(self):
        import inspect
        src = inspect.getsource(rc.main)
        self.assertIn('if not args.apply:', src)
        self.assertLess(src.index("if not args.apply:"), src.index("apply_to("),
                        "預覽的出口要在任何寫入之前")

    def test_the_rebuild_is_not_incremental(self):
        """改了 frontmatter 之後增量重建不會重讀那些列。

        2026-09 的 migrate_schema 就是這樣：改完只做增量，結果索引沒變。
        """
        import inspect
        src = inspect.getsource(rc.main)
        self.assertIn("rebuild(incremental=False)", src)


if __name__ == "__main__":
    unittest.main()


class TheCategoryWriterDoesNotEatTheNextLine(unittest.TestCase):
    r"""category_classifier.apply_category 的 `\s*` 會跨行。

    原本是 `^category:\s*.*$`：遇到空的 `category:` 欄位時 `\s*` 吃掉換行、
    `.*$` 吃掉下一行，下一個欄位就被替換掉。索引裡有 4 筆 category 是空的，
    而且這是**攝取時**就在跑的路徑——被吃掉的如果是 title，make_card 會回 None，
    那張卡從此不進任何 wiki topic。

    這是這個專案第 N 次的 `\s` 跨行 bug（build_search_index.sh 的八個 regex 為
    同一個原因改成 `[ \t]*`）。
    """

    def test_an_empty_category_field_does_not_swallow_the_title(self):
        text = ("---\ncategory:\ntitle: 重要標題\n"
                "source_url: https://x.com/1\n---\n# t\n")

        out = category_classifier.apply_category(text, "99-general")

        self.assertIn("title: 重要標題", out, "下一行被吃掉了")
        self.assertIn("category: 99-general", out)
        self.assertIn("source_url: https://x.com/1", out)

    def test_a_normal_category_field_is_replaced_in_place(self):
        text = "---\ncategory: tech\ntitle: t\n---\n# t\n"
        out = category_classifier.apply_category(text, "99-general")
        self.assertEqual(out, "---\ncategory: 99-general\ntitle: t\n---\n# t\n")

    def test_a_file_with_no_frontmatter_gets_one(self):
        out = category_classifier.apply_category("# 只有正文\n", "99-general")
        self.assertTrue(out.startswith("---\ncategory: 99-general\n---\n"))
        self.assertIn("# 只有正文", out)

    def test_an_empty_category_argument_changes_nothing(self):
        text = "---\ncategory: tech\n---\n"
        self.assertEqual(category_classifier.apply_category(text, ""), text)
