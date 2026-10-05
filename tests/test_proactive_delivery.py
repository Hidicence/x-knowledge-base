"""Exercise current-need ranking and proactive delivery without provider calls."""
import copy
import json
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
        intent = patch.object(delivery, 'prepare', return_value={'needs': ['source', 'version'], 'constraints': [], 'model': 'fixture'})
        self.intent = intent.start()
        self.addCleanup(intent.stop)
        patcher = patch.object(delivery, '_select_grounded', side_effect=lambda query, conversation, records, task: {
            'needs': ['current need', 'another need'], 'constraints': [], 'model': 'fixture',
            'selected': [{'index': i, 'quote': records[i]['summary']} for i in range(min(2, len(records)))]})
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
        self.assertEqual(selected['records'], [])
        self.intervention.assert_not_called()
        self.intent.assert_not_called()
        self.assertEqual(selected['withheld'][0]['reason'], 'incomplete_candidate_judgement')
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
        self.assertEqual([r['id'] for r in selected['records']], ['first'])
        self.assertEqual({r['reason'] for r in selected['withheld']}, {'weak_current_need', 'duplicate_claim', 'not_selected_for_current_task'})
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

    def test_valid_empty_decision_is_quiet_without_erasing_candidates(self):
        self.intervention.side_effect = None
        self.intervention.return_value = {'needs': [], 'constraints': [], 'selected': [], 'model': 'fixture'}
        records = [record('topic-match', .99)]
        result = self.select(records, {'status': 'judged'}, [])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(len(records), 1)

    def test_invalid_provider_output_degrades_without_negative_preference(self):
        self.intervention.side_effect = ValueError('invalid quote reference')
        result = self.select([record('a', .9)], {'status': 'judged'}, [])
        self.assertEqual(result['status'], 'degraded')
        self.assertEqual(result['records'], [])
        self.assertEqual(result['withheld'][0]['reason'], 'intervention_unverified')

    def test_independent_quiet_decision_vetoes_evidence_biased_selection(self):
        self.intent.return_value = {'needs': [], 'constraints': [], 'model': 'fixture'}
        result = self.select([record('plausible-but-unrequested', .9)], {'status': 'judged'}, [])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['status'], 'ready')
        self.intervention.side_effect = TimeoutError('selection unavailable')
        self.assertEqual(self.select([record('a', .9)], {'status': 'judged'}, [])['status'], 'ready')

    def test_review_is_bounded_and_unjudged_outage_does_not_make_another_call(self):
        records = [record(str(i), .9) for i in range(20)]
        result = self.select(records, {'status': 'judged'}, [])
        self.assertEqual(result['intervention']['reviewed'], 4)
        self.assertEqual(len(self.intervention.call_args.args[2]), 4)
        self.intervention.reset_mock()
        self.select(records, {'status': 'unavailable'}, [])
        self.intervention.assert_not_called()

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


class GroundedSelection(unittest.TestCase):
    def test_task_resolution_never_receives_candidates_and_selection_cannot_rewrite_it(self):
        task = {'needs': ['Reduce the unchecked backlog'], 'constraints': ['Do not increase production']}
        selected = {'selected': [{'need': 0, 'index': 0, 'unit': 0}]}
        records = [record('queue', .9, 'Limit pending work; pause at capacity.')]
        with patch.object(delivery._llm, '_runtime_settings', return_value={}), \
             patch.object(delivery._llm, '_direct_api_call', side_effect=[json.dumps(task), json.dumps(selected)]) as call:
            result = delivery.select(records, {'status': 'judged'}, [], query='The unchecked queue keeps growing')
        planning = json.loads(call.call_args_list[0].args[1])
        selection = json.loads(call.call_args_list[1].args[1])
        self.assertEqual(set(planning), {'current', 'history'})
        self.assertEqual(selection['task'], task)
        self.assertEqual(result['intervention']['needs'], task['needs'])
        for invalid in ({**selected, 'needs': ['Increase production']}, {'selected': [{'need': 1, 'index': 0, 'unit': 0}]}):
            with patch.object(delivery._llm, '_runtime_settings', return_value={}), \
                 patch.object(delivery._llm, '_direct_api_call', return_value=json.dumps(invalid)), self.assertRaises(ValueError):
                delivery._select_grounded('backlog', [], records, task)

    def test_unknown_task_and_quiet_task_remain_distinct(self):
        with patch.object(delivery._llm, '_runtime_settings', return_value={}), \
             patch.object(delivery._llm, '_direct_api_call', return_value='{"needs": [], "constraints": []}') as call:
            result = delivery.select([record('a', .9)], {'status': 'judged'}, [], query='Finished')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result['intervention']['state'], 'quiet')
        self.assertEqual(result['status'], 'ready')
        with patch.object(delivery, 'prepare', return_value=None):
            self.assertEqual(delivery.select([record('a', .9)], {'status': 'judged'}, [], query='Need')['status'], 'degraded')

    def test_upstream_wiki_boundaries_preserve_late_qualifiers(self):
        import continuity_recall as cr
        long_body = 'Deployment detail. ' * 24 + 'Delete the backup only after verified recovery. Never delete an unverified backup.'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'guide.md').write_text('# Deployment\n\n'+long_body, encoding='utf-8')
            store = service.Store(root/'test.sqlite')
            store.catalog.wiki_topics_dir = root
            packet = store.catalog.search('deployment', 5, options={'semantic': False, 'cards': False, 'conversations': False})
            body = packet['records'][0]['summary']
            self.assertIn('Never delete an unverified backup.', body)
        marked = ('Operational detail. ' + '<!-- xkb-candidate:' + 'a'*64 + ' --> ')*18 + 'Delete the backup only after verified recovery.'
        with patch.object(cr, '_load_semantic_vectors', return_value={'wiki/topics/guide.md#Guide': [1.,0.]}), \
             patch.object(cr, '_embed_query', return_value=[1.,0.]), \
             patch.object(cr, '_section_text', return_value=marked):
            hit = cr.recall_semantic('deployment', sections_per_document=2, excerpt_limit=2000)[0]
        self.assertIn('only after verified recovery.', hit.excerpt)

    def test_display_only_backend_snippets_are_never_injected(self):
        candidate = {**record('a', .9, 'Delete the backup'), 'excerpt_truncated': True}
        with patch.object(delivery, 'prepare') as prepare:
            result = delivery.select([candidate], {'status': 'judged'}, [], query='Recovery procedure')
        prepare.assert_not_called()
        self.assertEqual(result['records'], [])
        self.assertEqual(result['status'], 'degraded')
        self.assertEqual(result['withheld'][0]['reason'], 'incomplete_source_excerpt')

    def test_explicit_attribute_can_recover_a_complementary_section(self):
        import continuity_recall as cr
        vectors = {'wiki/topics/guide.md#General deployment': [1., 0.],
                   'wiki/topics/guide.md#More general advice': [.99, .01],
                   'wiki/topics/guide.md#Record artifact version and source': [.8, .6]}
        with patch.object(cr, '_load_semantic_vectors', return_value=vectors), \
             patch.object(cr, '_embed_query', return_value=[1., 0.]), \
             patch.object(cr, '_base_heading', side_effect=lambda rest, section: section), \
             patch.object(cr, '_section_text', side_effect=lambda rest, section: section):
            hits = cr.recall_semantic('deployment version source', top_k=2, sections_per_document=2)
        self.assertEqual([h.section for h in hits], ['General deployment', 'Record artifact version and source'])
        self.assertEqual(hits[1].score, .8)

    def test_semantic_recall_can_return_action_beside_same_document_intro(self):
        import continuity_recall as cr
        vectors = {'wiki/topics/guide.md#Intro': [1., 0.],
                   'wiki/topics/guide.md#Procedure': [.99, .01]}
        with patch.object(cr, '_load_semantic_vectors', return_value=vectors), \
             patch.object(cr, '_embed_query', return_value=[1., 0.]), \
             patch.object(cr, '_base_heading', side_effect=lambda rest, section: section), \
             patch.object(cr, '_section_text', side_effect=lambda rest, section: section + '\n\n' + 'step '*60):
            self.assertEqual(len(cr.recall_semantic('workflow', top_k=4)), 1)
            hits = cr.recall_semantic('workflow', top_k=4, sections_per_document=2, excerpt_limit=2000)
        self.assertEqual([hit.section for hit in hits], ['Intro', 'Procedure'])
        self.assertGreater(len(hits[1].excerpt), 200)
        self.assertIn('\n\n', hits[1].excerpt)

    def test_evaluation_rejects_document_hit_with_useless_excerpt(self):
        case = {'id': 'lineage', 'surface': 'delivery', 'expected_ids': ['a'],
                'required_excerpt_patterns': ['source', 'version'],
                'forbidden_excerpt_patterns': ['page covers']}
        packet = {'records': [], 'retrieval_mode': 'fixture',
                  'delivery': {'status': 'ready', 'records': [record('a', .9, '1.')]}}
        self.assertFalse(score_case(case, packet)['ok'])
        packet['delivery']['records'][0]['summary'] = 'This page covers source and version.'
        self.assertFalse(score_case(case, packet)['ok'])
        packet['delivery']['records'][0]['summary'] = 'Keep version. *(self-derived · source: notes.md)*'
        self.assertFalse(score_case(case, packet)['ok'])
        packet['delivery']['records'][0]['summary'] = 'For every result preserve source and version.'
        self.assertTrue(score_case(case, packet)['ok'])

    def test_numbered_steps_and_qualifiers_remain_complete(self):
        body = '1. **角色設定卡**：保留正面、側面及表情。\n2. 每鏡帶入設定卡。\n3. 除非造型改變，否則不要重畫。'
        self.assertEqual(delivery._evidence_units(body), [body])
        self.assertEqual(delivery._evidence_units('1.\n\n### Method\n\nUse version 1.2 only if approved.'),
                         ['### Method\n\nUse version 1.2 only if approved.'])
        self.assertEqual(delivery._evidence_units('1.\n\n---\n\n### Heading only'), [])

    def test_partial_paragraph_is_not_presented_as_complete_evidence(self):
        self.assertEqual(delivery._evidence_units('Complete paragraph.\n\n' + 'partial '*400),
                         ['Complete paragraph.'])
        self.assertEqual(delivery._evidence_units('partial '*400), [])

    def test_number_only_provider_reference_is_rejected(self):
        response = json.dumps({'selected': [{'need': 0, 'index': 0, 'unit': 0}]})
        with patch.object(delivery._llm, '_runtime_settings', return_value={}), \
             patch.object(delivery._llm, '_direct_api_call', return_value=response), \
             self.assertRaises(ValueError):
            delivery._select_grounded('method', [], [record('a', .9, '1.')], {'needs': ['method'], 'constraints': []})

    def call(self, answer):
        with patch.object(delivery._llm, '_runtime_settings', return_value={}), \
             patch.object(delivery._llm, '_direct_api_call', return_value=answer) as call:
            result = delivery._select_grounded('Current need', [], [record('a', .9, 'Keep source.\n\nPreserve version.')], {'needs': ['lineage'], 'constraints': []})
        return result, call

    def test_copies_evidence_locally_and_bounds_transport(self):
        raw = '```json\r\n' + json.dumps({'selected': [{'need': 0, 'index': 0, 'unit': 1}]}) + '\r\n```'
        result, call = self.call(raw)
        self.assertEqual(result['selected'][0]['quote'], 'Preserve version.')
        self.assertEqual(call.call_args.kwargs['attempts'], 1)
        self.assertEqual(call.call_args.kwargs['timeout'], 12)
        self.assertEqual(call.call_args.kwargs['max_tokens'], 512)

    def test_invalid_references_cannot_invent_evidence(self):
        for item in ({'need': 0, 'index': 2, 'unit': 0}, {'need': 0, 'index': 0, 'unit': -1},
                     {'need': True, 'index': 0, 'unit': 0}, {'need': 0, 'index': 0, 'unit': True},
                     {'need': 1, 'index': 0, 'unit': 0}, {'index': 0, 'quote': 'Invented method'}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.call(json.dumps({'selected': [item]}))
        for raw in ('{"needs":', 'Commentary {"selected": []}', '[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.call(raw)

    def test_one_information_gap_can_keep_two_complementary_steps(self):
        result, _ = self.call(json.dumps({'selected': [
            {'need': 0, 'index': 0, 'unit': 0}, {'need': 0, 'index': 0, 'unit': 1}]}))
        self.assertEqual([item['quote'] for item in result['selected']], ['Keep source.', 'Preserve version.'])

    def test_duplicate_assignment_does_not_repeat_a_step(self):
        result, _ = self.call(json.dumps({'selected': [
            {'need': 0, 'index': 0, 'unit': 0}, {'need': 0, 'index': 0, 'unit': 0}]}))
        self.assertEqual(len(result['selected']), 1)

    def test_one_unit_can_support_two_needs_without_duplicate_presentation(self):
        response = json.dumps({'selected': [
            {'need': 0, 'index': 0, 'unit': 0}, {'need': 1, 'index': 0, 'unit': 0}]})
        with patch.object(delivery._llm, '_runtime_settings', return_value={}), \
             patch.object(delivery, 'prepare', return_value={'needs': ['source', 'version'], 'constraints': [], 'model': 'fixture'}), \
             patch.object(delivery._llm, '_direct_api_call', return_value=response):
            result = delivery.select([record('a', .9, 'Keep source and version.')], {'status': 'judged'}, [], query='handoff')
        self.assertEqual(result['records'][0]['summary'], 'Keep source and version.')
        self.assertEqual(len(result['intervention']['quotes']), 2)
        self.assertEqual(len(result['records']), 1)

    def test_long_source_cannot_displace_the_body_or_dialogue(self):
        records = [{'source_url': 'https://example.test/'+'x'*2000, 'title': 'x'*2000,
                    'summary': 'UNIQUE_ACTION.\n\n'+'body '*1000}]
        raw = json.dumps({'selected': []})
        with patch.object(delivery._llm, '_runtime_settings', return_value={}), \
             patch.object(delivery._llm, '_direct_api_call', return_value=raw) as call:
            delivery._select_grounded('current '*400, [{'role': 'user', 'content': 'history '*400}], records, {'needs': ['task'], 'constraints': []})
        state = json.loads(call.call_args.args[1])
        self.assertLessEqual(len(state['current']), 1200)
        self.assertLessEqual(len(state['history'][0]['content']), 600)
        self.assertLessEqual(len(state['evidence'][0]['source']), 120)
        self.assertLessEqual(len(state['evidence'][0]['title']), 80)
        self.assertIn('UNIQUE_ACTION.', state['evidence'][0]['units'])


if __name__ == '__main__':
    unittest.main()
