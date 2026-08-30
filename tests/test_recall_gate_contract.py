from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import conversation_state_parser as parser  # noqa: E402


class NoiseListTests(unittest.TestCase):
    def test_acknowledgements_are_noise(self) -> None:
        for text in ("ok", "好的", "收到", "ok 收到", "好的收到", "謝謝"):
            self.assertTrue(parser.is_noise(text), text)

    def test_compound_acknowledgement(self) -> None:
        """The case that reached the live path: five characters, not in the
        exact set, so a subset copy of this list let it through and ten
        knowledge records were injected into a conversation asking nothing."""
        self.assertTrue(parser.is_noise("ok 收到"))

    def test_greetings_are_noise(self) -> None:
        for text in ("早安", "嗨", "hi", "hello", "哈哈"):
            self.assertTrue(parser.is_noise(text), text)

    def test_real_questions_are_not_noise(self) -> None:
        for text in ("XKB 的召回架構是什麼", "碳盤查的計算方式", "我們之前怎麼處理報價"):
            self.assertFalse(parser.is_noise(text), text)


class TaskVersusQuestionTests(unittest.TestCase):
    """A task verb is only noise when it is about nothing we know.

    A bare ^計算 used to suppress 計算碳排放要用什麼係數 — exactly the kind of
    question this knowledge base exists to answer.
    """

    def test_a_bare_task_is_noise(self) -> None:
        self.assertTrue(parser.is_noise("計算 3+5"))
        self.assertTrue(parser.is_noise("翻譯這段文字"))
        self.assertTrue(parser.is_noise("寫一個 function 把字串反轉"))

    def test_a_task_about_a_known_domain_is_a_question(self) -> None:
        with mock.patch.object(parser, "high_freq_domains", return_value=("seedance-2",)):
            self.assertFalse(parser.is_noise("計算 seedance-2 的鏡頭長度"))


class DomainVocabularyTests(unittest.TestCase):
    def test_domains_come_from_the_wiki_not_a_hand_written_list(self) -> None:
        """The hand-written list had ten technical terms while the wiki had
        grown to cover video, imaging and SEO, so short questions about those
        counted as having no domain and were suppressed."""
        domains = parser.high_freq_domains()
        self.assertGreater(len(domains), len(parser.GENERIC_DOMAINS))
        for baseline in parser.GENERIC_DOMAINS:
            self.assertIn(baseline, domains)

    def test_it_survives_an_unreadable_wiki(self) -> None:
        parser.high_freq_domains.cache_clear()
        try:
            with mock.patch.dict(sys.modules, {"xkb_paths": None}):
                self.assertEqual(set(parser.high_freq_domains()), set(parser.GENERIC_DOMAINS))
        finally:
            parser.high_freq_domains.cache_clear()


class ServiceSharesTheGateTests(unittest.TestCase):
    """The service must not carry its own copy of the list.

    It did, and the copy drifted. Both ends now read the same one, and this
    test fails if a future change reintroduces a private list.
    """

    def test_service_delegates_to_the_shared_list(self) -> None:
        source = (SCRIPTS / "xkb_memory_service.py").read_text(encoding="utf-8")
        self.assertIn("from conversation_state_parser import", source)
        # The shape of the private copy that drifted: a pattern list and an
        # exact-match set living beside the caller instead of with the list.
        self.assertNotIn("GREETING_PATTERNS", source)
        self.assertNotIn("ACK_ONLY", source)
        self.assertNotIn("SUPPRESS_PATTERNS = ", source)

    def test_service_skips_exactly_what_the_parser_calls_noise(self) -> None:
        service = importlib.import_module("xkb_memory_service")
        for text in ("ok 收到", "早安", "好的"):
            self.assertTrue(service.Store._skip_reason(text), text)
        for text in ("XKB 的召回架構是什麼", "Seedance 的場景參考怎麼用"):
            self.assertFalse(service.Store._skip_reason(text), text)


if __name__ == "__main__":
    unittest.main()
