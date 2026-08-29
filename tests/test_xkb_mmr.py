from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from xkb_mmr import mmr_select, text_similarity, text_terms  # noqa: E402


class TextSimilarityTests(unittest.TestCase):
    def test_each_han_character_is_its_own_term(self) -> None:
        self.assertEqual(text_terms("記憶"), {"記", "憶"})

    def test_ascii_runs_stay_whole(self) -> None:
        self.assertIn("gpt-image-2", text_terms("用 gpt-image-2 做前製"))

    def test_single_ascii_characters_are_ignored(self) -> None:
        self.assertNotIn("a", text_terms("a b"))

    def test_identical_text_is_fully_redundant(self) -> None:
        self.assertEqual(text_similarity("agent 記憶系統", "agent 記憶系統"), 1.0)

    def test_unrelated_text_is_not_redundant(self) -> None:
        self.assertEqual(text_similarity("agent 記憶系統", "Seedance 運鏡"), 0.0)

    def test_empty_text_is_not_redundant(self) -> None:
        self.assertEqual(text_similarity("", "agent 記憶"), 0.0)


class MmrSelectionTests(unittest.TestCase):
    def _items(self, third_score: int):
        return [
            {"score": 60, "t": "agent 記憶當成內部服務，要有明確介面與權限"},
            {"score": 58, "t": "agent 記憶應該當作內部服務，需要介面與權限"},
            {"score": third_score, "t": "Seedance 場景參考可以放攝影機路徑"},
        ]

    def test_comparable_scores_prefer_something_new(self) -> None:
        picked = mmr_select(self._items(56), 2, text=lambda i: i["t"])
        self.assertEqual([i["score"] for i in picked], [60, 56])

    def test_pure_relevance_keeps_the_near_duplicate(self) -> None:
        picked = mmr_select(self._items(56), 2, text=lambda i: i["t"], lambda_=1.0)
        self.assertEqual([i["score"] for i in picked], [60, 58])

    def test_diversity_never_promotes_a_far_weaker_result(self) -> None:
        """A different subject is not worth two thirds less relevance."""
        picked = mmr_select(self._items(12), 2, text=lambda i: i["t"])
        self.assertEqual([i["score"] for i in picked], [60, 58])

    def test_scale_of_incoming_scores_does_not_matter(self) -> None:
        """Token counts and cosine similarities must behave the same.

        Mixing score scales is the failure this repository keeps repeating,
        so the selection has to be invariant to a caller's units.
        """
        counts = self._items(56)
        cosines = [dict(item, score=item["score"] / 100.0) for item in counts]
        self.assertEqual(
            [i["t"] for i in mmr_select(counts, 2, text=lambda i: i["t"])],
            [i["t"] for i in mmr_select(cosines, 2, text=lambda i: i["t"])],
        )

    def test_negative_scores_are_handled(self) -> None:
        """Penalties such as the self-derived down-weighting can go below zero."""
        items = [
            {"score": 0.9, "t": "外部來源的說法"},
            {"score": -0.15, "t": "自己講過的話"},
        ]
        picked = mmr_select(items, 2, text=lambda i: i["t"])
        self.assertEqual([i["score"] for i in picked], [0.9, -0.15])

    def test_limit_and_empty_input(self) -> None:
        self.assertEqual(mmr_select([], 3, text=lambda i: i["t"]), [])
        self.assertEqual(len(mmr_select(self._items(56), 0, text=lambda i: i["t"])), 0)
        self.assertEqual(len(mmr_select(self._items(56), 99, text=lambda i: i["t"])), 3)

    def test_equal_scores_keep_input_order_for_the_first_pick(self) -> None:
        items = [{"score": 1, "t": "甲"}, {"score": 1, "t": "乙"}]
        self.assertEqual(mmr_select(items, 1, text=lambda i: i["t"])[0]["t"], "甲")


if __name__ == "__main__":
    unittest.main()
