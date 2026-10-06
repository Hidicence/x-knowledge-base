"""Ranking and proactive delivery: Jev verdicts plus local rules, no generation model."""
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
from xkb_eval import score_case


def record(key, judge, body='', score=.9, scale='wiki_semantic'):
    return {'id': key, 'judge': judge, 'summary': body or key,
            'score': score, 'score_scale': scale, 'namespace': 'private'}


class ProactiveDelivery(unittest.TestCase):
    def select(self, records, judge, conversation, delivered=frozenset()):
        return delivery.select(records, judge, conversation, query='Help with the current task', delivered=delivered)

    def test_current_need_overrides_old_topic_retrieval_rank(self):
        source = [record('old-video', .36, score=.95), record('old-shot', .22, score=.9),
                  record('carbon', .89, score=.8)]
        ranked = delivery.rank(source, {'status': 'judged'})
        self.assertEqual([r['id'] for r in ranked], ['carbon', 'old-video', 'old-shot'])
        selected = self.select(ranked, {'status': 'judged'}, [])
        self.assertEqual([r['id'] for r in selected['records']], ['carbon'])
        self.assertEqual(len(ranked), 3)  # Selection does not erase candidates.

    def test_at_most_three_and_no_generation_model_is_called(self):
        records = [record(str(i), .9, f'distinct step {i}') for i in range(10)]
        result = self.select(records, {'status': 'judged'}, [])
        self.assertEqual([r['id'] for r in result['records']], ['0', '1', '2'])
        self.assertEqual(result['status'], 'ready')
        self.assertFalse(hasattr(delivery, '_llm'))

    def test_partial_judge_delivers_positive_verdicts_and_stays_visible(self):
        source = [record('weak', .2, score=.98), record('unknown', None, score=.95),
                  record('useful', .9, score=.9)]
        ranked = delivery.rank(source, {'status': 'partial'})
        self.assertEqual([r['id'] for r in ranked], ['useful', 'unknown', 'weak'])
        selected = self.select(ranked, {'status': 'partial'}, [])
        self.assertEqual([r['id'] for r in selected['records']], ['useful'])
        self.assertEqual(selected['status'], 'degraded')
        fallback = delivery.rank(copy.deepcopy(source), {'status': 'unavailable'})
        self.assertEqual([r['id'] for r in fallback], ['weak', 'unknown', 'useful'])
        unavailable = self.select(fallback, {'status': 'unavailable'}, [])
        self.assertEqual(unavailable['mode'], 'background')
        self.assertEqual(unavailable['status'], 'degraded')

    def test_exact_duplicates_and_text_already_in_a_reply_are_not_delivered(self):
        claim = 'Keep original source, reporting period, units and coefficient version for every calculation.'
        history = [{'role': 'assistant', 'content': claim}]
        records = [record('already', .9, claim), record('first', .9, 'A distinct useful step'),
                   record('copy', .8, 'A distinct useful step'), record('weak', .2, 'Another step')]
        selected = self.select(records, {'status': 'judged'}, history)
        self.assertEqual([r['id'] for r in selected['records']], ['first'])
        self.assertEqual([r['reason'] for r in selected['withheld']],
                         ['already_in_conversation', 'duplicate_claim', 'weak_relevance'])
        self.assertEqual(len(selected['previously_presented']), 1)

    def test_a_user_statement_does_not_count_as_already_presented(self):
        claim = 'Keep original source, reporting period, units and coefficient version for every calculation.'
        result = self.select([record('evidence', .9, claim)], {'status': 'judged'},
                             [{'role': 'user', 'content': claim}])
        self.assertEqual(result['mode'], 'suggest')

    def test_display_only_backend_snippets_are_never_injected(self):
        candidate = {**record('a', .9, 'Delete the backup'), 'excerpt_truncated': True}
        result = self.select([candidate], {'status': 'judged'}, [])
        self.assertEqual(result['records'], [])
        self.assertEqual(result['withheld'][0]['reason'], 'incomplete_source_excerpt')

    def test_id_only_titles_are_replaced_by_the_first_sentence(self):
        card = {**record('2044072293383712878', .9, '單一鏡頭只安排一個核心動作。其餘拆成下一鏡。'),
                'title': '2044072293383712878'}
        shown = self.select([card], {'status': 'judged'}, [])['records'][0]
        self.assertEqual(shown['title'], '單一鏡頭只安排一個核心動作')
        named = {**record('x', .9, 'Body.'), 'title': 'Seedance 2.0 workflow'}
        self.assertEqual(self.select([named], {'status': 'judged'}, [])['records'][0]['title'], 'Seedance 2.0 workflow')

    def test_long_bodies_keep_whole_paragraphs_and_are_labelled(self):
        body = 'First complete paragraph.\n\n' + 'later detail. ' * 200
        shown = delivery.excerpt(body)
        self.assertTrue(shown.startswith('First complete paragraph.'))
        self.assertTrue(shown.endswith('（節錄）'))
        single = 'One long sentence. ' * 100
        cut = delivery.excerpt(single)
        self.assertTrue(cut.endswith('（節錄）'))
        self.assertLessEqual(len(cut), delivery.EXCERPT_CHARS + 10)
        self.assertEqual(delivery.excerpt('short'), 'short')

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


if __name__ == '__main__':
    unittest.main()
