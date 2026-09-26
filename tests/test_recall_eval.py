"""An evaluator must reject wrong evidence and outages, not just print scores."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import xkb_eval


class RecallEvaluation(unittest.TestCase):
    def packet(self, ids):
        return {"records": [{"id": x} for x in ids], "count": len(ids),
                "retrieval_mode": "keyword_fallback", "judge": {"status": "unavailable"}}

    def test_wrong_evidence_lowers_precision_and_fails(self):
        result = xkb_eval.score_case({"id": "q", "expected_ids": ["good"]},
                                     self.packet(["good", "noise"]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["recall_at_k"], 1)
        self.assertEqual(result["precision_at_k"], .5)

    def test_missing_half_of_conflicting_evidence_fails(self):
        result = xkb_eval.score_case({"id": "q", "expected_ids": ["for", "against"]},
                                     self.packet(["for"]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["recall_at_k"], .5)

    def test_no_answer_query_rejects_any_result(self):
        case = {"id": "q", "expected_ids": []}
        self.assertFalse(xkb_eval.score_case(case, self.packet(["noise"]))["ok"])
        self.assertTrue(xkb_eval.score_case(case, self.packet([]))["ok"])

    def test_transport_failure_is_not_dropped_from_recall_denominator(self):
        cases = [{"id": "positive", "query": "q", "expected_ids": ["good"]},
                 {"id": "negative", "query": "q", "expected_ids": []}]
        with mock.patch.object(xkb_eval, "probe", side_effect=RuntimeError("offline")):
            result = xkb_eval.run_cases(cases, env={})
        self.assertFalse(result["ok"])
        self.assertEqual(result["mean_recall_at_k"], 0)
        self.assertEqual(result["no_answer_cases"], 1)
        self.assertEqual(result["no_answer_passed"], 0)


if __name__ == "__main__":
    unittest.main()
