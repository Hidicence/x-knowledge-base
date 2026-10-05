"""Exercise current-need ranking and proactive delivery without provider calls."""
import copy
import os
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
    def setUp(self):
        patcher = patch.object(delivery.xkb_jev, 'intervention', side_effect=lambda context, candidates: {
            'need': {'noul': .9},
            **{f'use_{i}': {'noul': .9} for i in range(len(candidates))},
            **{f'applies_{i}': {'noul': .9} for i in range(len(candidates))},
            **{f'same_{j}_{i}': {'noul': 0} for i in range(len(candidates)) for j in range(i)}})
        self.intervention = patcher.start()
        self.addCleanup(patcher.stop)

    def select(self, records, judge, conversation):
        return delivery.select(records, judge, conversation, query='Help with the current task')

    def test_current_need_overrides_old_topic_retrieval_rank(self):
        source = [record('old-video', .36, score=.95), record('old-shot', .22, score=.9),
                  record('carbon', .89, score=.8)]
        ranked = delivery.rank(source, {'status': 'judged'})
        self.assertEqual([r['id'] for r in ranked], ['carbon', 'old-video', 'old-shot'])
        selected = self.select(ranked, {'status': 'judged'}, [])
        self.assertEqual([r['id'] for r in selected['records']], ['carbon'])
        self.assertEqual(len(ranked), 3)  # Selection does not erase candidates.

    def test_partial_judge_keeps_unknown_positions_and_marks_delivery_degraded(self):
        source = [record('weak', .2, score=.98), record('unknown', None, score=.95),
                  record('useful', .9, score=.9)]
        ranked = delivery.rank(source, {'status': 'partial'})
        self.assertEqual([r['id'] for r in ranked], ['useful', 'unknown', 'weak'])
        selected = self.select(ranked, {'status': 'partial'}, [])
        self.assertEqual([r['id'] for r in selected['records']], ['useful'])
        self.assertEqual(selected['status'], 'degraded')
        fallback = delivery.rank(copy.deepcopy(source), {'status': 'unavailable'})
        self.assertEqual([r['id'] for r in fallback], ['weak', 'unknown', 'useful'])
        self.assertEqual(self.select(fallback, {'status': 'unavailable'}, [])['mode'], 'background')

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
        selected = self.select(records, {'status': 'judged'}, history)
        self.assertEqual([r['id'] for r in selected['records']], ['first', 'second'])
        self.assertEqual({r['reason'] for r in selected['withheld']}, {'weak_current_need', 'duplicate_claim'})
        self.assertEqual(len(selected['previously_presented']), 1)
        self.assertEqual(len(records), 4)

    def test_silence_or_user_statement_does_not_mean_a_claim_was_already_presented(self):
        claim = 'Keep original source, reporting period, units and coefficient version for every calculation.'
        records = [record('evidence', .9, claim)]
        for history in ([], [{'role': 'user', 'content': claim}]):
            self.assertEqual(self.select(records, {'status': 'judged'}, history)['mode'], 'suggest')

    def test_requested_repeat_or_new_applicability_can_reuse_presented_evidence(self):
        claim = 'Keep original source, reporting period, units and coefficient version for every calculation.'
        # Current-turn judgement already includes the request and prior answer.
        selected = self.select([record('checklist', .95, claim)], {'status': 'judged'},
                                   [{'role': 'assistant', 'content': claim}])
        self.assertEqual(selected['mode'], 'suggest')
        self.assertEqual(len(selected['previously_presented']), 1)

    def test_delivery_evaluation_does_not_relabel_candidate_results(self):
        packet = {'records': [record('useful', .9), record('noise', .2)], 'retrieval_mode': 'keyword',
                  'judge': {'status': 'judged'}}
        packet['delivery'] = self.select(packet['records'], packet['judge'], [])
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

    def test_completed_turn_vetoes_high_relevance_without_erasing_candidates(self):
        self.intervention.side_effect = None
        self.intervention.return_value = {'need': {'noul': .05}, 'use_0': {'noul': .95}, 'applies_0': {'noul': .9}}
        records = [record('relevant', .99)]
        result = self.select(records, {'status': 'judged'}, [])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['withheld'][0]['reason'], 'no_open_need')
        self.assertEqual(len(records), 1)

    def test_marginal_value_and_semantic_overlap_control_delivery(self):
        self.intervention.side_effect = None
        self.intervention.return_value = {
            'need': {'noul': .9}, 'use_0': {'noul': .2}, 'use_1': {'noul': .8},
            'use_2': {'noul': .9}, 'use_3': {'noul': .7},
            **{f'applies_{i}': {'noul': .9} for i in range(4)},
            'same_1_2': {'noul': .95}, 'same_2_3': {'noul': .1}}
        records = [record('generic', .99), record('paraphrase', .98),
                   record('useful', .9), record('extra-step', .8)]
        result = self.select(records, {'status': 'judged'}, [])
        self.assertEqual([r['id'] for r in result['records']], ['useful', 'extra-step'])
        self.assertEqual({r['reason'] for r in result['withheld']}, {'no_added_value', 'redundant_advice'})

    def test_unknown_intervention_or_overlap_is_not_a_negative_user_preference(self):
        self.intervention.side_effect = None
        for answers in (None, {'need': {'noul': True}}, {'need': {'noul': .9}}):
            self.intervention.return_value = answers
            result = self.select([record('a', .9)], {'status': 'judged'}, [])
            self.assertEqual(result['status'], 'degraded')
            self.assertEqual(result['records'], [])
        self.intervention.return_value = {'need': {'noul': .9}, 'use_0': {'noul': .9}, 'use_1': {'noul': .8},
                                         'applies_0': {'noul': .9}, 'applies_1': {'noul': .9}}
        result = self.select([record('a', .9), record('b', .9)], {'status': 'judged'}, [])
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['status'], 'degraded')
        self.assertEqual(result['withheld'][0]['reason'], 'overlap_unverified')

    def test_review_is_bounded_and_unjudged_outage_does_not_make_another_call(self):
        records = [record(str(i), .9) for i in range(20)]
        result = self.select(records, {'status': 'judged'}, [])
        self.assertEqual(result['intervention']['reviewed'], 4)
        self.assertEqual(len(self.intervention.call_args.args[1]), 4)
        self.intervention.reset_mock()
        self.select(records, {'status': 'unavailable'}, [])
        self.intervention.assert_not_called()

    def test_high_usefulness_does_not_override_wrong_applicability(self):
        self.intervention.side_effect = None
        self.intervention.return_value = {'need': {'noul': .9}, 'use_0': {'noul': .99}, 'applies_0': {'noul': .1}}
        result = self.select([record('another-task', .99)], {'status': 'judged'}, [])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['withheld'][0]['reason'], 'wrong_applicability')

    def test_shared_judge_sees_bounded_wiki_candidates_below_legacy_cosine_floor(self):
        import continuity_recall as cr
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(cr, '_load_semantic_vectors', return_value={'wiki/topics/role.md#Guide': [.6,.8]}), \
             patch.object(cr, '_embed_query', return_value=[1.,0.]), \
             patch.object(cr, '_section_text', return_value='Use the same character reference in every shot.'), \
             patch.object(cr, 'WIKI_MIN_SIMILARITY', .65):
            store = service.Store(Path(tmp)/'test.sqlite')
            store.catalog.wiki_topics_dir = Path(tmp)/'topics'
            self.assertEqual(cr.recall_semantic('The actor changes between shots'), [])
            with patch.dict(os.environ, {'XKB_JEV_DECIDE': '1'}):
                hits = store.catalog._wiki_search('The actor changes between shots', 1)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['score'], .6)
            with patch.dict(os.environ, {'XKB_JEV_DECIDE': '0'}):
                self.assertEqual(store.catalog._wiki_search('The actor changes between shots', 1), [])


if __name__ == '__main__':
    unittest.main()
