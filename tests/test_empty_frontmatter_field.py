"""空的 frontmatter 欄位不可以把下一行吞進來。

2026-09-10：品質檢查說有兩張卡該被排除，理由是 source_url 不是合法網址。
查下去，那兩張卡的 source_url 在索引裡是 `category: 02-seo-geo`——而檔案裡
那一行其實是空的：

    source_url:
    category: 02-seo-geo

builder 的正則是 `^source_url:\\s*"?([^"\\n]+)"?\\s*$`，而 `\\s` 含換行。欄位
空的時候 `\\s*` 跨過換行，`[^"\\n]+` 就吃到了下一行整行。

一個解析 bug 一路走成「把好卡片藏起來」：欄位被讀成一段不是網址的文字 →
品質規則判定 invalid_source_url → 排除 → 那張卡從此不出現在召回裡。其中一
張是「Obsidian + Claude Code：用AI重建你的第二大脑」，當天稍早的召回還引用
過它的內容。

這個模式在 builder 裡用在每一個欄位上，所以這裡把每一個都測一次。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_search_index.sh"

# 每個欄位都留空，下一行都是一段「看起來像值」的文字。
CARD_WITH_EMPTY_FIELDS = """---
id: empty-fields
type: knowledge-card
title:
category: 02-seo-geo
source_url:
tags: [seo, mcp]
source_type:
sensitivity: public
tweet_id:
confidence: medium
excluded:
excluded_reason: 不應該被讀成排除
---

# 真正的標題

## 📌 一句話摘要
真正的摘要
"""


def _bash() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in ["C:/Program Files/Git/bin/bash.exe",
                      "C:/Program Files (x86)/Git/bin/bash.exe"]:
        if Path(candidate).exists():
            return candidate
    raise unittest.SkipTest("no POSIX bash available")


def _require_python3() -> None:
    try:
        proc = subprocess.run(["python3", "-c", "pass"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("這個環境沒有可執行的 python3")
    if proc.returncode != 0:
        raise unittest.SkipTest("python3 不是真的直譯器（Windows Store 轉接器）")


class EmptyFieldsDoNotSwallowTheNextLine(unittest.TestCase):
    def _build(self) -> dict:
        bash = _bash()
        _require_python3()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cards = root / "memory" / "cards"
            bookmarks = root / "memory" / "bookmarks"
            cards.mkdir(parents=True)
            bookmarks.mkdir(parents=True)
            index = bookmarks / "search_index.json"
            (cards / "empty-fields.md").write_text(CARD_WITH_EMPTY_FIELDS,
                                                   encoding="utf-8")
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
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_an_empty_source_url_stays_empty(self):
        """這就是那兩張卡被標成該排除的原因。"""
        row = self._build()
        self.assertNotIn("category:", row["source_url"])
        self.assertEqual(row["source_url"], "")

    def test_an_empty_title_falls_back_to_the_heading_not_the_next_line(self):
        row = self._build()
        self.assertEqual(row["title"], "真正的標題")

    def test_an_empty_source_type_is_inferred_not_swallowed(self):
        row = self._build()
        self.assertNotIn("sensitivity", row["source_type"])

    def test_an_empty_excluded_field_does_not_mean_excluded(self):
        """`excluded:` 空著就是沒有排除。讀成排除等於憑空藏掉一張卡。"""
        row = self._build()
        self.assertFalse(row.get("excluded"))

    def test_the_real_category_and_tags_still_parse(self):
        """修法不能把有值的情況一起弄壞。"""
        row = self._build()
        self.assertEqual(row["category"], "02-seo-geo")
        self.assertEqual(row["tags"], ["seo", "mcp"])
        self.assertEqual(row["summary"], "真正的摘要")


if __name__ == "__main__":
    unittest.main()
