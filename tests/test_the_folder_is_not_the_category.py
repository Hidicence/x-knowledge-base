"""資料夾是位址，不是分類。

1,754 筆書籤裡有 650 筆的 frontmatter `category` 跟所在目錄不一致（最大宗是 188
筆放在 02-seo-geo 但自稱 03-video-prompts）。這個不一致本身沒有破壞任何東西——
召回完全不讀分類（沒有任何過濾或加權用它，嵌入只有內容，路徑只是鍵），wiki 路由、
lint、健檢讀的全都是 frontmatter。

所以這份測試不要求兩邊一致。它要求的是**只有一邊算分類**：

    frontmatter 的 category 是分類
    目錄名是檔案放在哪，沒有別的意思

兩個「修好」它的方向都比不修更糟，所以不能讓它們悄悄發生：
  搬檔案對齊 frontmatter → relative_path 變了，而那是向量索引與 knowledge_usage
    的鍵；267 筆使用統計會變孤兒，退場量測歸零。
  改 frontmatter 對齊目錄 → 650 張卡被重新路由到別的 wiki topic，等於讓 legacy
    目錄名蓋掉分類器的判斷。

2026-09-12 我自己被這個前綴騙過一次：看到 record_id `02-seo-geo/2032...` 就推論
那張卡「被歸錯類所以被 SEO 問題撈出來」。它其實是日文 AI 生圖 prompt 合集，
frontmatter 寫 04-ai-tools-agents，而它被撈出來是內容嵌入弱相關。報告印前綴卻不
印真正的分類，就是在邀請這個推論——所以下面也釘住報告要印分類。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

# 從路徑拿片段的寫法。這些本身沒問題（很多地方合法地用它們找檔案），
# 問題是把結果當成分類。
PATH_SEGMENT = re.compile(
    r"parent\.name|os\.path\.dirname|\.parts\[|\bdirname\b|"
    r"split\(['\"]/['\"]\)")


class OnlyFrontmatterDecidesTheCategory(unittest.TestCase):
    def test_no_script_derives_a_category_from_a_path(self):
        """同一行同時出現「category」和「從路徑取片段」就是在把位址當分類。"""
        offenders = []
        for path in sorted(SCRIPTS.glob("*.py")):
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                code = line.split("#", 1)[0]
                if "category" in code.lower() and PATH_SEGMENT.search(code):
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "資料夾名不是分類——分類只從 frontmatter 來。若真的需要位址，"
            "把變數叫 folder，別叫 category：\n" + "\n".join(offenders))

    def test_the_search_index_reads_the_frontmatter(self):
        """search_index.json 的 category 必須來自 frontmatter。

        它是 wiki 路由、lint、健檢唯一的分類來源（sync_cards_to_wiki 讀的是
        search_index 的 category 欄位）。改成讀目錄會一次改掉 650 張卡的路由。
        """
        builder = (SCRIPTS / "build_search_index.sh").read_text(
            encoding="utf-8", errors="replace")
        self.assertRegex(builder, r"\^category:",
                         "分類要從 frontmatter 的 category: 欄位解析")

    def test_recall_does_not_filter_or_boost_by_category(self):
        """召回不讀分類。讀了就會讓 650 筆不一致變成真正的 bug。"""
        for name in ("xkb_memory_service.py", "xkb_score.py", "xkb_relevance.py",
                     "recall_router.py"):
            text = (SCRIPTS / name).read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0]
                if "category" not in code.lower():
                    continue
                with self.subTest(where=f"{name}:{lineno}"):
                    # 當成欄位原樣搬運可以；拿來比較、過濾、加權不行。
                    self.assertNotRegex(
                        code, r"category.*(==|!=|\bin\b|>=|<=)|"
                              r"(==|!=|\bin\b).*category",
                        "召回不該依分類過濾或加權")


class ReportsMustNotPassTheFolderOffAsTheCategory(unittest.TestCase):
    def test_the_evict_report_shows_the_real_category(self):
        """報告印 record_id 前綴卻不印真正的分類，就是在邀請錯誤推論。"""
        import xkb_evict_report as rep
        self.assertTrue(hasattr(rep, "describe"))
        source = (SCRIPTS / "xkb_evict_report.py").read_text(
            encoding="utf-8", errors="replace")
        self.assertIn("describe(r[\"record_id\"])", source,
                      "降權清單要印出 frontmatter 的分類與標題")

    def test_describe_prefers_frontmatter_over_the_prefix(self):
        import tempfile
        from unittest import mock
        import xkb_evict_report as rep
        import xkb_paths

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # 不要把測試卡片寫進真的 cards/ ——那是資產。
        card = Path(tmp.name) / "cat-check-1.md"
        card.write_text("---\ncategory: 04-ai-tools-agents\n---\n# 日文 prompt 合集\n",
                        encoding="utf-8")

        with mock.patch.object(xkb_paths, "CARDS_DIR", Path(tmp.name)):
            title, category = rep.describe("02-seo-geo/cat-check-1")

        self.assertEqual(category, "04-ai-tools-agents",
                         "分類要從 frontmatter 來，不是從 record_id 前綴")
        self.assertEqual(title, "日文 prompt 合集")

    def test_describe_survives_a_missing_card(self):
        import xkb_evict_report as rep
        self.assertEqual(rep.describe("02-seo-geo/does-not-exist"), ("", ""))


if __name__ == "__main__":
    unittest.main()
