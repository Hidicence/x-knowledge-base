"""Exercise current-need ranking and proactive delivery without provider calls."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import xkb_delivery as delivery
import xkb_memory_service as service
import xkb_score
from xkb_eval import score_case


def record(key, judge, body='', score=.9, scale='wiki_semantic'):
    return {'id': key, 'judge': judge, 'summary': body or key,
            'score': score, 'score_scale': scale, 'namespace': 'private'}


class ProactiveDelivery(unittest.TestCase):
    def test_current_need_overrides_old_topic_retrieval_rank(self):
        source = [record('old-video', .36, score=.95), record('old-shot', .22, score=.9),
                  record('carbon', .89, score=.8)]
        ranked = delivery.rank(source, {'status': 'judged'})
        self.assertEqual([r['id'] for r in ranked], ['carbon', 'old-video', 'old-shot'])
        selected = delivery.select(ranked, {'status': 'judged'}, [])
        self.assertEqual([r['id'] for r in selected['records']], ['carbon'])
        self.assertEqual(len(ranked), 3)  # Selection does not erase candidates.

    def test_partial_judge_keeps_unknown_positions_and_marks_delivery_degraded(self):
        source = [record('weak', .2, score=.98), record('unknown', None, score=.95),
                  record('useful', .9, score=.9)]
        ranked = delivery.rank(source, {'status': 'partial'})
        self.assertEqual([r['id'] for r in ranked], ['useful', 'unknown', 'weak'])
        selected = delivery.select(ranked, {'status': 'partial'}, [])
        self.assertEqual([r['id'] for r in selected['records']], ['useful'])
        self.assertEqual(selected['status'], 'degraded')
        fallback = delivery.rank(copy.deepcopy(source), {'status': 'unavailable'})
        self.assertEqual([r['id'] for r in fallback], ['weak', 'unknown', 'useful'])
        self.assertEqual(delivery.select(fallback, {'status': 'unavailable'}, [])['mode'], 'background')

    def test_fusion_preserves_all_legs_and_strongest_judged_text(self):
        source = [record('same', .2, 'weak excerpt', .8),
                  record('other', .7, score=.95),
                  record('same', .9, 'actionable excerpt', 2, 'card_keyword')]
        ranked = delivery.rank(source, {'status': 'judged'})
        self.assertEqual(ranked[0]['id'], 'same')
        self.assertEqual(ranked[0]['summary'], 'actionable excerpt')
        self.assertEqual(set(ranked[0]['matched_by']), {'wiki_semantic', 'card_keyword'})
        self.assertEqual(len(ranked), 2)
        anonymous = [record('', .7, 'one'), record('', .8, 'two')]
        self.assertEqual(len(delivery.rank(anonymous, {'status': 'judged'})), 2)

    def test_exact_claim_duplicates_and_presented_reply_do_not_consume_delivery_budget(self):
        claim = 'Keep original source, reporting period, units and coefficient version for every calculation.'
        history = [{'role': 'assistant', 'content': claim}]
        records = [record('already', .2, claim), record('first', .9, 'A distinct useful step'),
                   record('copy', .8, 'A distinct useful step'), record('second', .8, 'Another useful step')]
        selected = delivery.select(records, {'status': 'judged'}, history)
        self.assertEqual([r['id'] for r in selected['records']], ['first', 'second'])
        self.assertEqual({r['reason'] for r in selected['withheld']}, {'weak_current_need', 'duplicate_claim'})
        self.assertEqual(len(selected['previously_presented']), 1)
        self.assertEqual(len(records), 4)

    def test_silence_or_user_statement_does_not_mean_a_claim_was_already_presented(self):
        claim = 'Keep original source, reporting period, units and coefficient version for every calculation.'
        records = [record('evidence', .9, claim)]
        for history in ([], [{'role': 'user', 'content': claim}]):
            self.assertEqual(delivery.select(records, {'status': 'judged'}, history)['mode'], 'suggest')

    def test_requested_repeat_or_new_applicability_can_reuse_presented_evidence(self):
        claim = 'Keep original source, reporting period, units and coefficient version for every calculation.'
        # Current-turn judgement already includes the request and prior answer.
        selected = delivery.select([record('checklist', .95, claim)], {'status': 'judged'},
                                   [{'role': 'assistant', 'content': claim}])
        self.assertEqual(selected['mode'], 'suggest')
        self.assertEqual(len(selected['previously_presented']), 1)

    def test_delivery_evaluation_does_not_relabel_candidate_results(self):
        packet = {'records': [record('useful', .9), record('noise', .2)], 'retrieval_mode': 'keyword',
                  'judge': {'status': 'judged'}}
        packet['delivery'] = delivery.select(packet['records'], packet['judge'], [])
        case = {'id': 'one', 'expected_ids': ['useful'], 'expected_delivery': 'evidence'}
        self.assertFalse(score_case(case, packet)['ok'])
        self.assertTrue(score_case({**case, 'surface': 'delivery'}, packet)['ok'])

    def test_noise_has_explicit_empty_delivery_and_does_not_call_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = service.Store(Path(tmp)/'test.sqlite')
            with patch.object(store.catalog, 'search') as search:
                packet = store.knowledge_recall('ok')
            search.assert_not_called()
            self.assertEqual(packet['delivery']['mode'], 'none')


if __name__ == '__main__':
    unittest.main()
