from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import conversation_state_parser as parser  # noqa: E402


class RecallFalsePositiveTests(unittest.TestCase):
    def test_compound_acknowledgement_is_suppressed(self) -> None:
        result = parser.parse("ok 收到")
        self.assertEqual(result.trigger_class, "suppress")
        self.assertEqual(result.suggested_query, "")

    def test_customer_quote_question_is_suppressed(self) -> None:
        result = parser.parse("上次那個客戶的報價怎麼算的")
        self.assertEqual(result.trigger_class, "suppress")
        self.assertEqual(result.suggested_query, "")


if __name__ == "__main__":
    unittest.main()