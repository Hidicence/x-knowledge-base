"""Ask is an answer client; retrieval options cannot select a second search stack."""
from __future__ import annotations
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import xkb_ask as ask


class AskRuntime(unittest.TestCase):
    def test_legacy_switches_use_the_shared_contract(self):
        packet = {"records": [], "retrieval_mode": "keyword", "query": "fixture", "count": 0}
        args = ["xkb_ask", "fixture", "--json", "--legacy-search", "--no-wiki",
                "--no-gbrain", "--max-cards", "2", "--env-file", "fixture.env"]
        with mock.patch.object(sys, "argv", args), mock.patch.object(ask, "run_configured", return_value=packet) as run, \
                mock.patch.object(ask, "llm_call", return_value="answer"), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(ask.main(), 0)
        self.assertFalse(run.call_args.kwargs["options"]["wiki"])
        self.assertFalse(run.call_args.kwargs["options"]["semantic"])
        self.assertEqual(run.call_args.kwargs["options"]["max_cards"], 2)
        self.assertEqual(run.call_args.kwargs["env_file"], "fixture.env")
        self.assertIn("--legacy-search", err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["answer"], "answer")

    def test_invalid_quota_stops_before_retrieval(self):
        for value in ("-1", "51"):
            with mock.patch.object(sys, "argv", ["xkb_ask", "fixture", "--max-wiki", value]), \
                    mock.patch.object(ask, "run_configured") as run, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    ask.main()
                run.assert_not_called()

    def test_no_cards_remains_an_explicit_remote_option(self):
        with mock.patch.object(sys, "argv", ["xkb_ask", "fixture", "--no-cards"]), \
                mock.patch.object(ask, "run_configured", side_effect=RuntimeError("HTTP 401")) as run, \
                mock.patch.object(ask, "llm_call") as llm, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ask.main(), 2)
        self.assertFalse(run.call_args.kwargs["options"]["cards"])
        llm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
