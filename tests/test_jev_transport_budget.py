import sys,json,unittest,io
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import xkb_jev as j

class Transport(unittest.TestCase):

    def test_nested_answers(self):
        with patch.object(j,'runtime_env',return_value={'LLM_API_URL':'https://example/v1','LLM_API_KEY':'placeholder'}), patch.object(j.urllib.request,'urlopen',return_value=io.BytesIO(b'{"data":{"answers":{"q":{"noul":0}}}}')):
            self.assertEqual(j.judge('s',{'q':{'type':'noul'}}),{'q':{'noul':0}})

    def test_unicode_batches_are_bounded_and_complete(self):
        sizes=[]
        def respond(req, **kw):
            sizes.append(len(req.data)); self.assertLessEqual(len(req.data),32768)
            body=json.loads(req.data)
            return io.BytesIO(json.dumps({'answers':{q:{'noul':0.8} for q in body['questions']}}).encode())
        with patch.object(j,'runtime_env',return_value={'LLM_API_URL':'https://example/v1','LLM_API_KEY':'placeholder'}), patch.object(j.urllib.request,'urlopen',side_effect=respond):
            out=j.relevance('中文問題😀',[(str(i),str(i)+'中😀\\\"'*300) for i in range(25)])
        self.assertEqual(len(out),25); self.assertGreater(len(sizes),1)

    def test_invalid_probabilities_are_not_rejections(self):
        for value in [True, False, -1, 1.1, float('nan'), float('inf'), '0']:
            with self.subTest(value=value), patch.object(j,'judge',return_value={'q0':{'noul':value}}):
                self.assertIsNone(j.relevance('question',[('a','text')]))

    def test_exact_byte_boundary(self):
        questions={'q':{'type':'noul'}}
        size=len(j._body('',questions))
        for delta in [0,1]:
            state='a'*(32768-size+delta)
            with patch.object(j,'runtime_env',return_value={'LLM_API_URL':'https://example/v1','LLM_API_KEY':'placeholder'}), patch.object(j.urllib.request,'urlopen',return_value=io.BytesIO(b'{"answers":{"q":{"noul":0}}}')) as call:
                result=j.judge(state,questions)
                self.assertEqual(call.call_count,1 if delta==0 else 0)

    def test_partial_failed_batch_keeps_other_answers(self):
        def respond(state,questions,**kwargs):
            return None if 'q0' in questions else {q:{'noul':0} for q in questions}
        with patch.object(j,'judge',side_effect=respond):
            out=j.relevance('問題',[(str(i),str(i)+'中😀'*450) for i in range(25)])
        self.assertTrue(out);self.assertNotIn('0',out);self.assertIn('24',out)

    def test_identical_questions_share_a_verdict_without_losing_candidate_keys(self):
        calls=[]
        def respond(state, questions, **kwargs):
            calls.append((state, questions))
            return {q:{'noul':0} for q in questions}
        candidates=[('semantic','Keep source.\nPreserve version.'),
                    ('keyword','Keep source. Preserve version.'),
                    ('constraint','Do not preserve version.')]
        with patch.object(j,'judge',side_effect=respond):
            first=j.relevance('current request',candidates)
            j.relevance('different current request',candidates)
        self.assertEqual(first,dict.fromkeys(['semantic','keyword','constraint'],0.0))
        self.assertEqual(len(calls),2)
        self.assertTrue(all(len(questions)==2 for state,questions in calls))
        self.assertNotEqual(calls[0][0],calls[1][0])

    def test_missing_shared_verdict_leaves_every_alias_unknown(self):
        import xkb_memory_service as svc
        records=[{'id':'a','title':'Same claim'}, {'id':'a','title':'Same claim'},
                 {'id':'b','title':'Distinct claim'}]
        with patch.object(j,'judge',return_value={'q2':{'noul':.8}}) as call:
            kept,note=svc.judge_relevance('current request',records)
        self.assertEqual(len(call.call_args.args[1]),2)
        self.assertEqual([r['judge'] for r in kept],[None,None,.8])
        self.assertEqual(note['unjudged'],2)
        self.assertEqual(note['status'],'partial')

    def test_partial_coverage_is_visible(self):
        import xkb_memory_service as svc
        records=[{'id':'a','title':'a'},{'id':'b','title':'b'}]
        with patch.object(j,'relevance',side_effect=lambda q,c:{c[0][0]:0.8}):
            kept,note=svc.judge_relevance('q',records)
        self.assertEqual(len(kept),2)
        self.assertEqual(note['status'],'partial')
        self.assertEqual(note['unjudged'],1)

    def test_partial_usage_counts_only_known_verdicts(self):
        import xkb_memory_service as svc
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            store=svc.Store(Path(d)/'test.sqlite')
            records=[{'id':'a','title':'a','judge':0.8},{'id':'b','title':'b','judge':None}]
            store.record_recall_usage('private',records,records,{'status':'partial','floor':0.1})
            with store.connect() as db:
                rows=db.execute('SELECT judged_count,relevant_count FROM recall_usage ORDER BY record_id').fetchall()
            self.assertEqual([tuple(r) for r in rows],[(1,1),(0,0)])

    def test_environment_contract(self):
        import os,tempfile
        from runtime_config import runtime_env
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'runtime.env'; f.write_text('LLM_API_KEY=file-placeholder\nLLM_API_URL=https://example/v1\n', encoding='utf-8')
            with patch.dict(os.environ,{"USERPROFILE":d,"HOME":d},clear=True):
                self.assertEqual(runtime_env(f)['LLM_API_KEY'],'file-placeholder')
                with patch.dict(os.environ,{'LLM_API_KEY':'process-placeholder'}):
                    self.assertEqual(runtime_env(f)['LLM_API_KEY'],'process-placeholder')
                    self.assertEqual(runtime_env()['LLM_API_KEY'],'process-placeholder')
                with self.assertRaises(FileNotFoundError):runtime_env(Path(d)/'absent')
            with patch.object(j,'runtime_env',return_value={}),patch.object(j.urllib.request,'urlopen') as call:
                self.assertIsNone(j.judge('s',{'q':{'type':'noul'}}));call.assert_not_called()

if __name__ == "__main__":
    unittest.main()
