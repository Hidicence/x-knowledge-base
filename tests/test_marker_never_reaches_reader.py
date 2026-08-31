"""治理的指紋不能出現在人看得到的地方。

促成這個測試的兩次失敗都很有教育意義：

第一次，只修了 continuity_recall 一個渲染器，拿一個查詢驗過就當作修好了。
router 另外還會渲染卡片與書籤，xbrain_recall 又自己組一份摘要，兩邊都沒聽過
這個標記。

第二次，規則寫成「指紋後面必須接 --> 或字串結尾」。但摘要是先截斷、再補一個
「…」，所以真正送到讀者眼前的是半個指紋加一個字元，兩個條件都不符合。

所以這裡驗的是性質本身，不是某一條路徑：任何一個渲染器的輸出都不准出現
xkb-candidate。第四個渲染器加進來時，它要嘛繼承這個性質，要嘛在這裡失敗。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import xkb_provenance as provenance

FINGERPRINT = "fe9d9cd7c1ba5f17098af9b21aa3961dab7b0a1c6d0e2077d23772d3aec0b1d5"
CLAIM = "長任務 agent 的狀態要外部化，不能只留在對話視窗裡。"


class MarkerNeverReachesReaderTest(unittest.TestCase):
    def assert_clean(self, text: str) -> str:
        out = provenance.strip_markers(text)
        self.assertNotIn("xkb-candidate", out)
        self.assertNotIn("<!--", out)
        return out

    def test_a_complete_marker_goes(self) -> None:
        out = self.assert_clean(f"{CLAIM} <!-- xkb-candidate:{FINGERPRINT} -->")
        self.assertEqual(out, CLAIM)

    def test_the_provenance_note_stays(self) -> None:
        # self-derived 是給人看的出處，不是內部記帳，不能一起砍掉。
        note = "*(self-derived · source: 2026-08-10-evening-candidates.md)*"
        out = self.assert_clean(f"{CLAIM} <!-- xkb-candidate:{FINGERPRINT} --> {note}")
        self.assertIn(note, out)

    def test_a_marker_cut_off_by_truncation_goes(self) -> None:
        cut = f"{CLAIM} <!-- xkb-candidate:{FINGERPRINT}"[:len(CLAIM) + 40]
        self.assertEqual(self.assert_clean(cut), CLAIM)

    def test_a_marker_cut_off_and_then_ellipsised_goes(self) -> None:
        # 這一種真的送到過使用者眼前：截斷之後補上「…」，於是「後面必須是
        # --> 或字串結尾」的規則對不上。
        cut = f"{CLAIM} <!-- xkb-candidate:{FINGERPRINT}"[:len(CLAIM) + 40] + "…"
        self.assertEqual(self.assert_clean(cut), CLAIM)

    def test_every_renderer_strips(self) -> None:
        """三個渲染器都要用同一個出口，不是各自為政。"""
        import continuity_recall
        import recall_router
        import xbrain_recall

        for module in (continuity_recall, recall_router, xbrain_recall):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertIn(
                "strip_markers", source,
                f"{Path(module.__file__).name} 會把召回結果組成文字給人看，"
                "但沒有經過 xkb_provenance.strip_markers",
            )


if __name__ == "__main__":
    unittest.main()
