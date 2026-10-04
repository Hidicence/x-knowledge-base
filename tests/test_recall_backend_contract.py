"""Regressions for lossy CLI output and stale side-index reranking."""
import sys,json,subprocess,unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tools')]
import xbrain_recall as xb

class StructuredBackend(unittest.TestCase):
    def test_hybrid_rank_is_not_rewritten_by_a_different_index(self):
        import xkb_memory_service as svc
        hits=[{'slug':'new-card','chunk_text':'direct relevant evidence','score':.95,'score_scale':'card_hybrid'},
              {'slug':'old-card','chunk_text':'adjacent topic','score':.8,'score_scale':'card_hybrid'}]
        catalog=svc.KnowledgeCatalog()
        catalog._reset_request_stats()
        with mock.patch.object(svc,'xbrain_query',return_value=hits), mock.patch.object(catalog,'_drop_irrelevant',side_effect=lambda q,rows: (list(reversed(rows)),0)) as stale:
            rows=catalog._semantic_search('natural query',10)
        self.assertEqual([r['id'] for r in rows],['new-card','old-card'])
        stale.assert_not_called()
        self.assertTrue(all(r['score_scale']=='card_hybrid' for r in rows))
        import xkb_score
        ranked=xkb_score.rank(rows)
        self.assertEqual(ranked[0]['id'],'new-card')
        self.assertEqual(xkb_score.weight_for('card_hybrid'),xkb_score.weight_for('card'))

    def test_unavailable_judge_is_an_explicit_quality_warning(self):
        import xkb_memory_service as svc
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store=svc.Store(Path(tmp)/'service.sqlite')
            with mock.patch.object(store.catalog,'search',return_value={'records':[{'id':'fixture','summary':'candidate','score':.9,'score_scale':'card_hybrid'}],'retrieval_mode':'xbrain_hybrid'}), mock.patch.object(store,'recall',return_value={'memories':[]}), mock.patch.object(svc.xkb_jev,'relevance',return_value=None):
                packet=store.knowledge_recall('natural query')
        self.assertEqual(packet['judge']['status'],'unavailable')
        self.assertTrue(any('judge' in w and 'unverified' in w for w in packet['warnings']))
        self.assertEqual(packet['count'],1)

    def test_query_uses_lossless_operation_transport(self):
        hit={'slug':'fixture','title':'Complete title','chunk_text':'evidence '*80,'score':.91}
        envelope=[hit]
        with mock.patch.object(xb,'_resolve_gbrain_dir',return_value=Path('fixture')), mock.patch.object(xb.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(envelope),'')) as run:
            rows=xb.xbrain_query('natural question',limit=7)
        self.assertEqual(rows[0]['chunk_text'],hit['chunk_text'])
        command=run.call_args.args[0]
        self.assertIn('call',command)
        payload=json.loads(command[-1])
        self.assertEqual(payload,{'query':'natural question','limit':7,'expand':False})
        self.assertEqual(rows[0]['score_scale'],'card_hybrid')

if __name__ == "__main__":
    unittest.main()
