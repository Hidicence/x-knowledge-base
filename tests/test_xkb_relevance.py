from __future__ import annotations


import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _isolation import clean_env

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Import normally rather than via importlib: loading a second copy under the
# same name would give the modules under test a different object than the one
# these tests patch, and the patches would silently do nothing.
import xkb_relevance as rel  # noqa: E402


class VectorKeyTests(unittest.TestCase):
    def test_slug_gets_the_index_suffix(self) -> None:
        self.assertEqual(rel.vector_key("01-topic/12345"), "01-topic/12345.md")
        self.assertEqual(rel.vector_key("01-topic/12345.md"), "01-topic/12345.md")

    def test_things_that_are_not_cards_are_not_guessed_at(self) -> None:
        for value in ("https://x.com/i/status/1", "semantic:3", "", "   "):
            self.assertEqual(rel.vector_key(value), "")


class FilterTests(unittest.TestCase):
    def test_below_the_floor_is_dropped(self) -> None:
        items = [{"id": "a/1", "score": 0.88}, {"id": "a/2", "score": 0.87}]
        with mock.patch.object(rel, "similarities", return_value={"a/1.md": 0.72, "a/2.md": 0.30}):
            kept, dropped, _ = rel.filter_irrelevant(items=items, query="q", key_of=lambda i: i["id"])
        self.assertEqual(dropped, 1)
        self.assertEqual([i["id"] for i in kept], ["a/1"])

    def test_rank_score_is_preserved_but_no_longer_the_score(self) -> None:
        """The rank score must stop being mistaken for relevance."""
        items = [{"id": "a/1", "score": 0.88}]
        with mock.patch.object(rel, "similarities", return_value={"a/1.md": 0.72}):
            kept, _, _ = rel.filter_irrelevant(items=items, query="q", key_of=lambda i: i["id"])
        self.assertEqual(kept[0]["score"], 0.72)
        self.assertEqual(kept[0]["rank_score"], 0.88)

    def test_unknown_similarity_is_not_treated_as_irrelevant(self) -> None:
        """A missing index is a config problem, not an empty knowledge base."""
        items = [{"id": "a/1", "score": 0.88}]
        with mock.patch.object(rel, "similarities", return_value=None):
            kept, dropped, _ = rel.filter_irrelevant(items=items, query="q", key_of=lambda i: i["id"])
        self.assertEqual((len(kept), dropped), (1, 0))
        with mock.patch.object(rel, "similarities", return_value={}):
            kept, dropped, _ = rel.filter_irrelevant(items=items, query="q", key_of=lambda i: i["id"])
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_items_without_a_resolvable_key_pass_through(self) -> None:
        items = [{"id": "https://x.com/i/status/1", "score": 0.9}]
        with mock.patch.object(rel, "similarities", return_value={"a/1.md": 0.9}):
            kept, dropped, _ = rel.filter_irrelevant(items=items, query="q", key_of=lambda i: i["id"])
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_results_are_ordered_by_measured_relevance(self) -> None:
        items = [{"id": "a/1", "score": 0.9}, {"id": "a/2", "score": 0.8}]
        with mock.patch.object(rel, "similarities", return_value={"a/1.md": 0.60, "a/2.md": 0.85}):
            kept, _, _ = rel.filter_irrelevant(items=items, query="q", key_of=lambda i: i["id"])
        self.assertEqual([i["id"] for i in kept], ["a/2", "a/1"])


class SingleSourceOfTruthTests(unittest.TestCase):
    """The rank-score mistake was made twice because the fix lived in one file.

    These guard the structural fix rather than the behaviour: nothing outside
    this module may define its own relevance threshold or re-implement the
    similarity filter.
    """

    CALLERS = ("recall_router.py", "xkb_memory_service.py")

    def test_callers_do_not_define_their_own_threshold(self) -> None:
        for name in self.CALLERS:
            source = (SCRIPTS / name).read_text(encoding="utf-8")
            offenders = re.findall(r"^[A-Z_]*(?:MIN_SIMILARITY|MIN_RELEVANCE)[A-Z_]*\s*=", source, re.M)
            self.assertEqual(offenders, [], f"{name} redefines the relevance threshold; use xkb_relevance")

    def test_callers_go_through_the_shared_module(self) -> None:
        for name in self.CALLERS:
            source = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn("xkb_relevance", source, f"{name} should filter via xkb_relevance")

    def test_threshold_honours_the_legacy_variable_names(self) -> None:
        for name in ("XKB_MIN_SIMILARITY", "XKB_SERVICE_MIN_RELEVANCE", "XKB_CARD_MIN_SIMILARITY"):
            with mock.patch.dict("os.environ", {**clean_env(), name: "0.71"}, clear=True):
                self.assertAlmostEqual(rel.min_similarity(), 0.71)
        with mock.patch.dict("os.environ", {**clean_env(), }, clear=True):
            self.assertAlmostEqual(rel.min_similarity(), rel.DEFAULT_MIN_SIMILARITY)


if __name__ == "__main__":
    unittest.main()
