"""索引裡的鍵要真的讀得回內容。

一個查得到、但摘要是空的命中，比查不到更糟：它佔掉一個名額，然後什麼都
沒給你。而顯示層在摘要為空時會退回顯示標題，所以它看起來一直很正常——
這種「優雅降級」把一個缺陷藏了好幾個月。

兩件真的發生過的事：

  路徑疊了兩層   索引鍵是 wiki/topics/x.md#標題，呼叫端拿掉 wiki/ 之後傳
                 topics/x.md，而 WIKI_TOPICS_DIR 本身就以 topics 結尾。
                 於是每一個 wiki 命中的摘要都是空的——不是部分，是全部。

  後綴沒被剝掉   索引用 @N 標示切塊、~N 標示同名標題的第幾個，都加在 # 之後。
                 讀取端拿「做法-Workflow~2」去找標題，找不到。實測 895 個。

這個測試對真實索引抽樣，所以它同時擋住「路徑組錯」與「又加了一種後綴卻
忘了教讀取端」。
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import continuity_recall as cr
import xkb_paths

SAMPLE = 120
# 索引不完美，允許少量鍵指向已經被改掉的標題（頁面會變，索引是增量的）。
MIN_RESOLVED = 0.9


def _wiki_keys() -> list[str]:
    if not xkb_paths.VECTOR_FILE.exists():
        return []
    data = json.loads(xkb_paths.VECTOR_FILE.read_text(encoding="utf-8"))
    keys = data.get("vectors", data)
    return [k for k in keys if k.startswith("wiki/")]


class ExcerptsResolveTest(unittest.TestCase):
    def _rate(self, keys: list[str]) -> float:
        if not keys:
            self.skipTest("這台機器上沒有索引")
        resolved = 0
        for key in keys[:SAMPLE]:
            rest, _, section = key[len("wiki/"):].partition("#")
            if cr._section_text(rest, cr._base_heading(rest, section)):
                resolved += 1
        return resolved / min(SAMPLE, len(keys))

    def test_ordinary_keys_resolve(self) -> None:
        """路徑組錯的話這裡會是 0——而且顯示層看起來仍然正常。"""
        keys = [k for k in _wiki_keys() if not re.search(r"[@~]\d+$", k)]
        rate = self._rate(keys)
        self.assertGreaterEqual(rate, MIN_RESOLVED, f"只有 {rate:.0%} 的 wiki 鍵讀得回內容")

    def test_suffixed_keys_resolve(self) -> None:
        """@N / ~N 是索引自己加的，讀取端要認得。"""
        keys = [k for k in _wiki_keys() if re.search(r"[@~]\d+$", k)]
        rate = self._rate(keys)
        self.assertGreaterEqual(rate, MIN_RESOLVED, f"只有 {rate:.0%} 的帶後綴鍵讀得回內容")

    def test_a_real_suffix_is_not_stripped_blindly(self) -> None:
        """標題本身就以 ~2 結尾時，不能被當成索引後綴砍掉。"""
        self.assertEqual(cr._base_heading("does-not-exist.md", "核心概念"), "核心概念")


if __name__ == "__main__":
    unittest.main()
