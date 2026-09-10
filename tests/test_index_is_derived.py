"""搜尋索引是衍生物：只有一個地方寫它，而且是從檔案重算。

2026-09-10 量到的狀態：八支程式各自對 search_index.json 做讀-改-寫。每一支都
是合併而不是覆蓋，所以沒有大規模掉資料，但它們對「這是同一個來源嗎」有不同
的認定（github-x / github_fork-x / github_star-x 被當成三個東西），也沒有共用
鎖。於是索引慢慢跟磁碟上的檔案說的不一樣。

把索引整份從檔案重建之後跟正在用的那份比：

    現行 1680 筆    重建 1680 筆
    重建後才出現的：3 筆（其中一張是當天健檢報出來的「孤兒卡」）
    重建後會消失的：1 筆（同一份內容，被記成另一個路徑）

也就是說重建不只是等價，它比較準。修補才是漂移的來源。

這份測試守三件事：只有 builder 會寫索引、值得留下來的東西寫在檔案裡、
重建之後那些東西還在。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import xkb_frontmatter  # noqa: E402

BUILDER = SCRIPTS / "build_search_index.sh"

# 唯一可以寫索引的檔案。要新增的話，先問「這件事為什麼不能由檔案推導出來」。
ALLOWED_WRITERS = {"build_search_index.sh"}

CARD = """---
id: {stem}
type: knowledge-card
source_type: local
source_url: https://example.test/{stem}
category: 99-general
tags: [a, b]
---

# {title}

## 📌 一句話摘要
{summary}
"""


def _require_python3() -> None:
    """builder 用 python3 跑。Windows 上那是 Microsoft Store 的轉接器，
    直接執行會以 49 結束、stderr 全空——看起來像 builder 壞了，其實它一行
    都沒跑到。這種假失敗要說得出原因，不要讓人去查 build_search_index.sh。"""
    try:
        proc = subprocess.run(["python3", "-c", "pass"],
                              capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("這個環境沒有可執行的 python3")
    if proc.returncode != 0:
        raise unittest.SkipTest("python3 不是真的直譯器（Windows Store 轉接器）")


def _bash() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in ["C:/Program Files/Git/bin/bash.exe",
                      "C:/Program Files (x86)/Git/bin/bash.exe"]:
        if Path(candidate).exists():
            return candidate
    raise unittest.SkipTest("no POSIX bash available")


class OnlyOneWriter(unittest.TestCase):
    def test_nothing_else_writes_the_index(self):
        offenders = []
        for path in sorted(list(SCRIPTS.glob("*.py")) + list(SCRIPTS.glob("*.sh"))):
            if path.name in ALLOWED_WRITERS:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "INDEX_FILE.write_text" in text or "index_file.write_text" in text:
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            "索引是衍生物，只能由 build_search_index.sh 寫。這些又開始自己修補了：\n  "
            + "\n  ".join(offenders))

    def test_the_ingesters_go_through_the_rebuild(self):
        for name in ["fetch_github_repos.py", "fetch_youtube_playlist.py",
                     "local_ingest.py", "sync_enriched_index.py"]:
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn("xkb_index.rebuild()", text)


class Frontmatter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _card(self, stem: str = "c1") -> Path:
        path = self.root / f"{stem}.md"
        path.write_text(CARD.format(stem=stem, title="標題", summary="一句話"),
                        encoding="utf-8")
        return path

    def test_marking_adds_the_flag_without_disturbing_the_rest(self):
        card = self._card()
        before = card.read_text(encoding="utf-8")

        self.assertTrue(xkb_frontmatter.mark_excluded(card, "duplicate_source_url"))

        after = card.read_text(encoding="utf-8")
        self.assertTrue(xkb_frontmatter.is_excluded(card))
        self.assertIn("excluded_reason: duplicate_source_url", after)
        # 其他欄位與內文原樣保留——不重排、不重寫。
        for line in ["id: c1", "type: knowledge-card", "category: 99-general",
                     "tags: [a, b]", "# 標題", "一句話"]:
            self.assertIn(line, after)
        self.assertEqual(before.count("---"), after.count("---"))

    def test_marking_twice_does_not_rewrite(self):
        card = self._card()
        xkb_frontmatter.mark_excluded(card, "reason")
        self.assertFalse(xkb_frontmatter.mark_excluded(card, "reason"))

    def test_a_file_without_frontmatter_reports_failure(self):
        """標記不上跟標記了不一樣。回 False 讓呼叫端說得出來。"""
        path = self.root / "plain.md"
        path.write_text("# 沒有 frontmatter\n", encoding="utf-8")
        self.assertFalse(xkb_frontmatter.mark_excluded(path, "reason"))


class TheFlagSurvivesARebuild(unittest.TestCase):
    """這是整個設計的關鍵：值得留下來的東西寫在檔案裡，所以重建洗不掉。

    原本它寫在索引列上，於是實測正式索引 1680 筆裡帶 excluded 的有 0 筆——
    兩支腳本的成果一次都沒留下來，而讀它的四個地方一直讀到 False。
    """

    def test_an_excluded_card_is_still_excluded_after_a_full_rebuild(self):
        bash = _bash()
        _require_python3()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cards = root / "memory" / "cards"
            bookmarks = root / "memory" / "bookmarks"
            cards.mkdir(parents=True)
            bookmarks.mkdir(parents=True)
            index = bookmarks / "search_index.json"

            keep = cards / "keep.md"
            drop = cards / "drop.md"
            keep.write_text(CARD.format(stem="keep", title="留著", summary="摘要"),
                            encoding="utf-8")
            drop.write_text(CARD.format(stem="drop", title="重複", summary="摘要"),
                            encoding="utf-8")
            xkb_frontmatter.mark_excluded(drop, "duplicate_source_url")

            env = {**os.environ,
                   "WORKSPACE_DIR": str(root),
                   "BOOKMARKS_DIR": str(bookmarks),
                   "CARDS_DIR": str(cards),
                   "INDEX_FILE": str(index),
                   "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            proc = subprocess.run([bash, str(BUILDER)], env=env,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            rows = json.loads(index.read_text(encoding="utf-8"))["items"]

        by_stem = {Path(r["relative_path"]).stem: r for r in rows}
        self.assertTrue(by_stem["drop"].get("excluded"))
        self.assertIn("duplicate_source_url", by_stem["drop"].get("excluded_reason", ""))
        self.assertFalse(by_stem["keep"].get("excluded"))


class TheQualityScriptsActuallyRun(unittest.TestCase):
    """真的把它們跑一次。

    2026-09-10：把 canonicalize_duplicates 改成標記檔案時，補上 _card_path
    的那一步沒有跑到，於是它引用了一個不存在的名字。py_compile 過、既有測試
    全綠、部署也成功——直到在 VPS 上真的執行才炸成 NameError。

    當時的測試只檢查「原始碼裡有沒有那個字串」。字串在不在跟跑不跑得起來是
    兩件事，而這個專案已經在別的地方吃過同一種虧。
    """

    def _fixture(self, root: Path) -> Path:
        cards = root / "memory" / "cards"
        bookmarks = root / "memory" / "bookmarks"
        cards.mkdir(parents=True)
        bookmarks.mkdir(parents=True)
        for stem in ("a", "b"):
            (cards / f"{stem}.md").write_text(
                CARD.format(stem=stem, title="標題", summary="一句話"),
                encoding="utf-8")
        index = bookmarks / "search_index.json"
        # a 與 b 指向同一個來源：canonicalize 應該挑一個留下、標記另一個。
        index.write_text(json.dumps({"version": "1.1", "items": [
            {"path": str(cards / "a.md"), "relative_path": "memory/cards/a.md",
             "title": "標題", "summary": "一句話", "tags": [], "category": "99-general",
             "source_url": "https://example.test/same", "source_type": "local",
             "enriched": True},
            {"path": str(cards / "b.md"), "relative_path": "memory/cards/b.md",
             "title": "2026-09-10", "summary": "", "tags": [], "category": "99-general",
             "source_url": "https://example.test/same", "source_type": "local",
             "enriched": True},
        ]}, ensure_ascii=False), encoding="utf-8")
        return index

    def _run(self, script: str, root: Path, index: Path):
        env = {**os.environ,
               "XKB_DATA_DIR": str(root / "memory"),
               "CARDS_DIR": str(root / "memory" / "cards"),
               "BOOKMARKS_DIR": str(root / "memory" / "bookmarks"),
               "INDEX_FILE": str(index),
               "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        return subprocess.run([sys.executable, str(SCRIPTS / script), "--dry-run"],
                              capture_output=True, text=True, env=env)

    def test_canonicalize_duplicates_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = self._fixture(root)
            proc = self._run("canonicalize_duplicates.py", root, index)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("duplicate groups", proc.stdout)

    def test_normalize_index_quality_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = self._fixture(root)
            proc = self._run("normalize_index_quality.py", root, index)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("總項目數", proc.stdout)


class LocalIngestsAreNotLowQuality(unittest.TestCase):
    """本機擷取的出處是一個檔案路徑，不是網址——那不是品質問題。

    2026-09-10：品質規則差一點把兩張本機匯入的好卡片排除掉，
    「every-app/open-seo — Semrush/Ahrefs 開源替代方案」與那本 Obsidian 的書，
    理由只是 source_url 不以 http 開頭。
    """

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        import normalize_index_quality
        self.reasons = normalize_index_quality.exclusion_reasons

    def test_a_file_path_is_valid_provenance_for_a_local_ingest(self):
        item = {"title": "一份本機文件", "summary": "有內容的摘要",
                "source_type": "local-paper",
                "source_url": "memory/bookmarks/github/stars/x.md"}
        self.assertNotIn("invalid_source_url", self.reasons(item))

    def test_a_broken_url_on_a_web_source_is_still_caught(self):
        item = {"title": "測試書籤", "summary": "摘要",
                "source_type": "x-bookmark",
                "source_url": "https://x.com/i/status/test"}
        self.assertIn("invalid_source_url", self.reasons(item))

    def test_an_unenriched_bookmark_is_still_caught(self):
        item = {"title": "Tweet 2073558354233094315", "summary": "",
                "source_type": "x-bookmark",
                "source_url": "https://x.com/i/status/2073558354233094315"}
        self.assertIn("tweet_numeric_low_signal", self.reasons(item))


class GovernanceRunsTheTidyStep(unittest.TestCase):
    def test_the_scheduled_job_calls_both_and_rebuilds(self):
        """排上去了才會跑。原本這兩支沒有任何排程在叫。"""
        text = (SCRIPTS / "run_candidate_governance.sh").read_text(encoding="utf-8")
        self.assertIn("canonicalize_duplicates", text)
        self.assertIn("normalize_index_quality", text)
        self.assertIn("xkb_index", text)


class DeduplicationMustLeaveOneCopy(unittest.TestCase):
    """去重不可以把整組都標掉——那不是去重，那是刪除。

    2026-09-10：把「標記一個檔案」改成「標記同一份知識的所有檔案」時，用的是
    source_url 去找。品質排除要的正是那個（空殼書籤的每一份副本都該排除），但
    重複組本來就共用同一個來源，於是「要保留的那一份」也被撈進來標掉。七份全
    排除，那份知識會從召回裡完全消失，而輸出只會說「已標記 6 張」。
    """

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        import xkb_index
        self.xkb_index = xkb_index
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cards = self.root / "cards"
        self.cards.mkdir()
        self.addCleanup(self._tmp.cleanup)
        patches = [
            mock.patch.object(xkb_index.xkb_paths, "CARDS_DIR", self.cards),
            mock.patch.object(xkb_index.xkb_paths, "BOOKMARKS_DIR", self.root / "bookmarks"),
            mock.patch.object(xkb_index.xkb_paths, "WORKSPACE", self.root),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        xkb_index.reset_scan()
        self.addCleanup(xkb_index.reset_scan)

    def _card(self, stem: str, url: str) -> Path:
        path = self.cards / f"{stem}.md"
        path.write_text(
            "---\n"
            f"id: {stem}\n"
            "type: knowledge-card\n"
            f"source_url: {url}\n"
            "category: 99-general\n"
            "tags: [a]\n"
            "---\n"
            "\n"
            f"# {stem}\n",
            encoding="utf-8")
        return path

    def test_by_source_gathers_every_copy(self):
        """品質排除要的是這個：同一份知識的每一份副本。"""
        url = "https://x.com/i/status/123456789012345678"
        self._card("copy_a", url)
        self._card("copy_b", url)
        item = {"relative_path": "copy_a.md", "source_url": url}

        found = self.xkb_index.files_for_item(item, by_source=True)

        self.assertEqual({p.stem for p in found}, {"copy_a", "copy_b"})

    def test_without_by_source_only_this_row_files(self):
        """去重要的是這個：不要把同組的其他成員撈進來。"""
        url = "https://x.com/i/status/123456789012345678"
        self._card("copy_a", url)
        self._card("copy_b", url)
        item = {"relative_path": "copy_a.md", "source_url": url}

        found = self.xkb_index.files_for_item(item, by_source=False)

        self.assertEqual({p.stem for p in found}, {"copy_a"})

    def test_the_canonical_copy_is_never_marked(self):
        """跑完整支腳本，確認保留的那一份沒有被標。"""
        url = "https://x.com/i/status/123456789012345678"
        keep = self._card("keeper", url)
        drop = self._card("dropper", url)
        index = self.root / "search_index.json"
        index.write_text(json.dumps({"version": "1.1", "items": [
            {"path": str(keep), "relative_path": "keeper.md", "title": "好標題",
             "summary": "夠長的摘要內容，超過十八個字元的門檻",
             "tags": [], "category": "99-general", "source_url": url,
             "source_type": "x-bookmark", "enriched": True},
            {"path": str(drop), "relative_path": "dropper.md", "title": "dropper",
             "summary": "", "tags": [], "category": "99-general", "source_url": url,
             "source_type": "x-bookmark", "enriched": False},
        ]}, ensure_ascii=False), encoding="utf-8")

        env = {**os.environ,
               "XKB_DATA_DIR": str(self.root),
               "CARDS_DIR": str(self.cards),
               "BOOKMARKS_DIR": str(self.root / "bookmarks"),
               "INDEX_FILE": str(index),
               "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "canonicalize_duplicates.py")],
            capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        self.assertFalse(xkb_frontmatter.is_excluded(keep), "保留的那一份被標掉了")
        self.assertTrue(xkb_frontmatter.is_excluded(drop))

    def test_a_marking_can_be_undone(self):
        """把知識從召回裡拿掉的決定，必須有辦法反悔。"""
        card = self._card("x", "https://example.test/x")
        xkb_frontmatter.mark_excluded(card, "reason")
        self.assertTrue(xkb_frontmatter.is_excluded(card))

        self.assertTrue(xkb_frontmatter.unmark_excluded(card))

        self.assertFalse(xkb_frontmatter.is_excluded(card))
        self.assertFalse(xkb_frontmatter.unmark_excluded(card))


if __name__ == "__main__":
    unittest.main()
