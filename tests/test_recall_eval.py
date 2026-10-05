"""An evaluator must reject wrong evidence and outages, not just print scores."""
from __future__ import annotations

import sys
import unittest
import json
import tempfile
import contextlib
import io
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

    def test_conversational_need_accepts_equivalent_evidence_but_not_unrelated_hits(self):
        case = {"id": "statement", "expected_ids": [], "expected_any_ids": ["card", "wiki"],
                "expected_delivery": "evidence", "need": "Resolve the current obstacle"}
        hit = xkb_eval.score_case(case, self.packet(["wiki"]))
        self.assertTrue(hit["ok"])
        self.assertFalse(hit["no_answer"])
        self.assertEqual(hit["recall_at_k"], 1)
        miss = xkb_eval.score_case(case, self.packet(["unrelated"]))
        self.assertFalse(miss["ok"])
        self.assertEqual(miss["recall_at_k"], 0)

    def test_distinct_sections_are_not_duplicates_and_document_precision_is_consistent(self):
        packet = self.packet(["guide", "guide", "noise"])
        for record, section in zip(packet["records"], ["setup", "recovery", ""]):
            record.update(record_type="wiki_topic", section=section)
        result = xkb_eval.score_case({"id": "q", "expected_ids": ["guide"]}, packet)
        self.assertNotIn("duplicate evidence", result["errors"])
        self.assertEqual(result["precision_at_k"], .5)
        packet["records"].pop()
        self.assertTrue(xkb_eval.score_case({"id": "q", "expected_ids": ["guide"]}, packet)["ok"])

    def test_evidence_labels_detect_missing_section_and_namespace(self):
        records = [{"id": "guide", "record_type": "wiki_topic", "section": section,
                    "namespace": namespace} for section, namespace in
                   [("setup", "private"), ("recovery", "private"), ("setup", "other")]]
        keys = [xkb_eval.identity_key(r) for r in records]
        case = {"id": "q", "label_unit": "evidence", "expected_ids": keys[:2]}
        packet = self.packet([])
        packet["records"] = records[::2]
        result = xkb_eval.score_case(case, packet)
        self.assertFalse(result["ok"])
        self.assertEqual(result["recall_at_k"], .5)
        self.assertEqual(result["precision_at_k"], .5)
        self.assertIn("missing: " + keys[1], result["errors"])

    def test_same_evidence_from_two_retrieval_legs_is_a_duplicate(self):
        packet = self.packet(["cards/guide.md", "guide"])
        self.assertIn("duplicate evidence", xkb_eval.score_case(
            {"id": "q", "expected_ids": [], "allowed_ids": ["cards/guide.md", "guide"]},
            packet)["errors"])

    def test_anonymous_records_fail_as_unidentified_not_duplicates(self):
        result = xkb_eval.score_case({"id": "q", "expected_ids": []}, self.packet(["", ""]))
        self.assertIn("unidentified evidence", result["errors"])
        self.assertNotIn("duplicate evidence", result["errors"])

    def test_transport_failure_is_not_dropped_from_recall_denominator(self):
        cases = [{"id": "positive", "query": "q", "expected_ids": ["good"]},
                 {"id": "negative", "query": "q", "expected_ids": []}]
        with mock.patch.object(xkb_eval, "probe", side_effect=RuntimeError("offline")):
            result = xkb_eval.run_cases(cases, env={})
        self.assertFalse(result["ok"])
        self.assertEqual(result["mean_recall_at_k"], 0)
        self.assertEqual(result["no_answer_cases"], 1)
        self.assertEqual(result["no_answer_passed"], 0)

    def test_intervention_cli_is_explicit_and_preserves_a_paid_run_receipt(self):
        import xkb_delivery
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp)/'cases.json'
            output = Path(tmp)/'receipt.json'
            suite.write_text(json.dumps({'evidence': {'a': 'An actionable step'}, 'cases': [
                {'id': 'q', 'query': 'Help with this task', 'candidate_ids': ['a'], 'expected_ids': ['a']}]}), encoding='utf-8')
            args = ['xkb_eval', '--intervention', '--cases', str(suite), '--output', str(output)]
            with mock.patch('sys.argv', args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                xkb_eval.main()
            self.assertFalse(output.exists())
            def judge(context, candidates):
                pending = json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(pending['pending_case'], 'q')
                self.assertEqual(pending['evidence']['a'], 'An actionable step')
                return {'need': {'noul': .9}, 'use_0': {'noul': .9}, 'applies_0': {'noul': .9}}
            with mock.patch('sys.argv', args+['--live']), mock.patch.object(xkb_delivery.xkb_jev, 'intervention', side_effect=judge), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(xkb_eval.main(), 0)
            before = output.read_bytes()
            receipt = json.loads(before)
            self.assertEqual(receipt['status'], 'completed')
            self.assertEqual(receipt['results'][0]['packet']['delivery']['records'][0]['id'], 'a')
            with mock.patch('sys.argv', args+['--live']), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                xkb_eval.main()
            self.assertEqual(output.read_bytes(), before)

    def test_delivery_evaluation_rejects_overdelivery_and_unknown_judgement(self):
        packet = self.packet(['a','b'])
        packet['delivery'] = {'records': packet['records'], 'status': 'degraded'}
        row = xkb_eval.score_case({'id': 'q', 'surface': 'delivery', 'expected_ids': ['a','b'], 'max_delivered': 1}, packet)
        self.assertIn('delivery budget exceeded', row['errors'])
        self.assertIn('proactive delivery judgement incomplete', row['errors'])

    def test_intervention_fixture_uses_the_same_bounded_dialogue_as_production(self):
        import xkb_delivery
        from xkb_recall import conversation_messages, contextual_query
        history = [{'role': 'assistant', 'content': str(i)+'x'*700} for i in range(8)]
        case = {'id': 'q', 'query': 'Current need', 'expected_ids': [], 'candidate_ids': ['a'], 'conversation': history}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(xkb_delivery.xkb_jev, 'intervention', return_value=None) as call:
            result = xkb_eval.run_intervention_cases([case], {'a': 'Evidence'}, Path(tmp)/'receipt.json')
        effective = conversation_messages(history)
        self.assertEqual(call.call_args.args[0], contextual_query(case['query'], effective, for_judge=True))
        self.assertEqual(result['results'][0]['packet']['effective_conversation'], effective)
        self.assertEqual(result['input']['cases'][0]['conversation'], history)


if __name__ == "__main__":
    unittest.main()
