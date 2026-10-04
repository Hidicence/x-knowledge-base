"""Publisher must not report searchable when embedding fails."""
import sys, unittest, tempfile, subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import _card_prompt as c
class PublishRecovery(unittest.TestCase):
 def test_existing_bookmark_retries_without_generation(self):
  import run_bookmark_worker as worker
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'2016309053379772746.md'; p.write_text('# saved evidence', encoding='utf-8')
   with patch.object(worker,'CARDS_DIR',Path(d)), patch.object(worker,'_gbrain_put',return_value=True) as put, patch.object(worker,'_call_llm') as llm:
    result=worker._process_item({'id':p.stem,'source_path':'missing.md'},'',False)
   self.assertEqual(result,('done',''))
   put.assert_called_once();llm.assert_not_called()
 def test_hyphenated_github_provenance_is_never_guessed(self):
  import xbrain_recall as reader
  self.assertEqual(reader._url_from_slug('github_star-some-owner-some-repo'), '')
  item={'slug':'github_star-some-owner-some-repo','frontmatter':{'source_url':'https://github.com/some-owner/some-repo'}}
  self.assertEqual(reader._source_url(item), 'https://github.com/some-owner/some-repo')
 def test_github_saved_card_does_not_regenerate(self):
  import fetch_github_repos as g
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'github_star-owner-repo.md').write_text('# saved', encoding='utf-8')
   with patch.object(g,'CARDS_DIR',root),patch.object(g,'GITHUB_RAW_DIR',root/'raw'),patch.object(g,'_gbrain_put',return_value=True) as put,patch.object(g,'_llm_call',return_value='---\nid: sample\n---\n# generated') as llm,patch.object(g,'get_readme_preview',return_value='readme'),patch.object(g,'find_related_context',return_value=''),patch.object(g,'classify_content',return_value={'category':'tech'}):
    g.process_repos([{'full_name':'owner/repo'}],'github_star',False,'',set(),1)
   put.assert_called_once();llm.assert_not_called()
 def test_local_saved_card_does_not_regenerate(self):
  import local_ingest as local
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); source=root/'input.txt';source.write_text('source', encoding='utf-8')
   cards=root/'cards';cards.mkdir();p=cards/(local.card_id_for_file(source)+'.md');p.write_text('---\nid: saved\n---\n# Saved', encoding='utf-8')
   with patch.object(local,'CARDS_DIR',cards),patch.object(local,'_gbrain_put',return_value=True) as put,patch.object(local,'llm_call',side_effect=AssertionError('must not regenerate')):
    local.process_file(source,'',set(),[],'tech',[],False)
   put.assert_called_once()
 def test_case_sensitive_ids_get_lossless_backend_keys(self):
  import gbrain_publish as pub
  self.assertNotEqual(pub.storage_slug('youtube-AbCdEf12345'),pub.storage_slug('youtube-abcdef12345'))
  self.assertEqual(pub.storage_slug('2016309053379772746'),'2016309053379772746')
 def test_youtube_saved_card_retries_without_generation(self):
  import fetch_youtube_playlist as y
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'AbCdEf12345.md').write_text('# saved', encoding='utf-8')
   with patch.object(y,'YOUTUBE_DIR',root),patch.object(y,'load_index',return_value={'items':[]}),patch.object(y,'get_playlist_videos',return_value=[{'id':'AbCdEf12345','title':'video','duration':120}]),patch.object(y,'_gbrain_put',return_value=True) as put,patch.object(y,'download_subtitles',side_effect=AssertionError('must not regenerate')),patch('gbrain_publish.check_backend'),patch('xkb_index.finish_ingest',return_value=True),patch.object(sys,'argv',['youtube','--playlist','fixture']):
    y.main()
   put.assert_called_once()



class CheckedPublication(unittest.TestCase):
    def setUp(self):
        import contextlib
        import gbrain_publish as pub
        self.pub = pub
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.card = self.root / "card.md"
        self.card.write_text('---\nid: 2016309053379772746\n---\n# Evidence', encoding="utf-8")
        self.stack.enter_context(patch.dict('os.environ', {'XKB_PUBLISH_OUTBOX': str(self.root / 'q.sqlite')}))
        self.stack.enter_context(patch.object(pub, '_runtime', return_value=(self.root, {'GEMINI_API_KEY': 'fixture'})))
        self.index = self.stack.enter_context(patch.object(pub.xkb_index, 'finish_ingest', return_value=True))

    def result(self, status, code=0):
        import json
        return subprocess.CompletedProcess([], code, stdout=json.dumps({'status': status}), stderr='')

    def row(self):
        with self.pub._db() as db:
            return db.execute('SELECT status,stage FROM outbox').fetchone()

    def test_current_backend_verified_even_after_a_successful_receipt(self):
        with patch.object(self.pub.subprocess, 'run', return_value=self.result('verified')) as run:
            self.assertEqual(self.pub.publish(self.card, 'card'), 'verified')
            self.assertEqual(self.pub.publish(self.card, 'card'), 'verified')
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all('--stdin' in call.args[0] for call in run.call_args_list))
        self.assertEqual(self.row(), ('verified', 'verified'))

    def test_confirmed_mismatch_repairs_and_rechecks_captured_content(self):
        with patch.object(self.pub.subprocess, 'run', side_effect=[self.result('mismatch', 1),
                self.result(''), self.result(''), self.result('verified')]) as run:
            self.assertEqual(self.pub.publish(self.card, 'card'), 'published')
        calls = run.call_args_list
        self.assertIn('put', calls[1].args[0])
        self.assertIn('embed', calls[2].args[0])
        self.assertEqual(calls[0].kwargs['input'], calls[1].kwargs['input'])
        self.assertEqual(calls[1].kwargs['input'], calls[3].kwargs['input'])
        self.assertIn('id: "2016309053379772746"', calls[1].kwargs['input'])

    def test_unavailable_or_broken_verifier_never_authorizes_writes(self):
        for result in [self.result('unavailable', 2), self.result('mismatch', 2),
                       subprocess.CompletedProcess([], 0, stdout='bad protocol', stderr='secret')]:
            with self.subTest(result=result), patch.object(self.pub.subprocess, 'run', return_value=result) as run:
                with self.assertRaises(self.pub.PublicationError) as caught:
                    self.pub.publish(self.card, 'card')
                self.assertTrue(caught.exception.unavailable)
                self.assertNotIn('secret', str(caught.exception))
                run.assert_called_once()
                self.assertEqual(self.row()[0], 'pending')

    def test_embedding_failure_can_retry_from_disk_without_generation(self):
        with patch.object(self.pub.subprocess, 'run', side_effect=[self.result('mismatch', 1),
                self.result(''), subprocess.CompletedProcess([], 1, stdout='', stderr='HTTP 503 secret')]):
            with self.assertRaises(self.pub.PublicationError) as caught:
                self.pub.publish(self.card, 'card')
        self.assertTrue(caught.exception.unavailable)
        self.assertEqual(self.row(), ('pending', 'embed'))
        with patch.object(self.pub.subprocess, 'run', return_value=self.result('verified')):
            self.assertEqual(self.pub.retry_pending(), {'attempted': 1, 'failed': 0})
        self.assertEqual(self.row(), ('verified', 'verified'))

    def test_local_change_during_verification_cannot_get_verified_receipt(self):
        def verify(*args, **kwargs):
            self.card.write_text('# Changed concurrently', encoding='utf-8')
            return self.result('verified')
        with patch.object(self.pub.subprocess, 'run', side_effect=verify):
            with self.assertRaises(self.pub.PublicationError):
                self.pub.publish(self.card, 'card')
        self.assertEqual(self.row(), ('pending', 'card changed'))

    def test_missing_pending_file_does_not_block_other_cards(self):
        with self.pub._db() as db:
            for i, path in enumerate([self.root / 'missing.md', self.card]):
                db.execute('INSERT INTO outbox VALUES (?,?,?,?,?,0,?)',
                           (str(i), str(path), '', 'pending', 'put', i))
        with patch.object(self.pub.subprocess, 'run', return_value=self.result('verified')) as run:
            result = self.pub.retry_pending()
        self.assertEqual(result, {'attempted': 2, 'failed': 1})
        run.assert_called_once()

    def test_item_failure_continues_but_outage_stops_and_indexes_saved_cards(self):
        from unittest.mock import Mock
        for unavailable, expected_calls in [(False, 2), (True, 1)]:
            with self.subTest(unavailable=unavailable):
                publisher = Mock(side_effect=[self.pub.PublicationError('verify', unavailable=unavailable), 'published'])
                batch = self.pub.PublicationBatch(publisher)
                batch.publish(self.card, 'one', generated=True)
                batch.publish(self.card, 'two', generated=True)
                self.assertFalse(batch.finish())
                self.assertEqual(publisher.call_count, expected_calls)
                self.index.assert_called_with(expected_calls)

    def test_github_saved_prefix_does_not_consume_new_generation_limit(self):
        import fetch_github_repos as github
        from unittest.mock import Mock
        (self.root / 'github_star-owner-old.md').write_text('# saved', encoding='utf-8')
        publisher = Mock(return_value='verified')
        batch = self.pub.PublicationBatch(publisher)
        with patch.object(github, 'CARDS_DIR', self.root), patch.object(github, 'GITHUB_RAW_DIR', self.root / 'raw'), \
             patch.object(github, '_llm_call', return_value='---\nid: new\n---\n# New') as llm, \
             patch.object(github, 'get_readme_preview', return_value='readme'), \
             patch.object(github, 'find_related_context', return_value=''), \
             patch.object(github, 'classify_content', return_value={'category': 'tech'}):
            count, items = github.process_repos([{'full_name': 'owner/old'}, {'full_name': 'owner/new'},
                {'full_name': 'owner/later'}], 'github_star', False, '', set(), 1, publication=batch)
        self.assertEqual(count, 1)
        llm.assert_called_once()
        self.assertEqual(batch.generated, 1)
        self.assertEqual(batch.verified, 1)
        self.assertEqual(publisher.call_count, 2)

    def test_background_recovery_rotates_and_explicit_inputs_are_all_checked(self):
        cards = []
        for i in range(3):
            card = self.root / f"saved{i}.md"
            card.write_text(f"# Card {i}", encoding='utf-8')
            cards.append((card, f"saved{i}"))
        with patch.object(self.pub.subprocess, 'run', return_value=self.result('verified')) as run:
            first = self.pub.PublicationBatch(recovery_limit=2)
            first.recover_saved(cards)
            self.assertEqual(first.deferred, 1)
            second = self.pub.PublicationBatch(recovery_limit=2)
            second.recover_saved(cards)
            with self.pub._db() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0], 3)
            explicit = self.pub.PublicationBatch(recovery_limit=1)
            for card, slug in cards:
                self.assertTrue(explicit.existing(card, slug))
            self.assertEqual(explicit.verified, 3)

    def test_unreadable_unreceipted_files_cannot_starve_other_saved_cards(self):
        broken = self.root / 'bad.md'
        broken.write_bytes(b'\xff')
        cards = [(broken, 'bad'), (self.card, 'card')]
        first = self.pub.PublicationBatch(recovery_limit=1)
        first.recover_saved(cards)
        self.assertEqual(first.failed, 1)
        with self.pub._db() as db:
            self.assertEqual(db.execute("SELECT stage FROM outbox WHERE slug='bad'").fetchone()[0], 'read saved card')
        with patch.object(self.pub.subprocess, 'run', return_value=self.result('verified')) as run:
            second = self.pub.PublicationBatch(recovery_limit=1)
            second.recover_saved(cards)
        run.assert_called_once()
        self.assertEqual(second.verified, 1)

    def test_pending_failures_share_budget_with_receiptless_discovery(self):
        with self.pub._db() as db:
            db.execute('INSERT INTO outbox VALUES (?,?,?,?,?,0,?)',
                       ('missing', str(self.root / 'missing.md'), '', 'pending', 'read saved card', 1))
        with patch.object(self.pub.subprocess, 'run', return_value=self.result('verified')) as run:
            batch = self.pub.PublicationBatch(recovery_limit=1)
            batch.recover_saved([(self.card, 'newly-discovered')])
        run.assert_called_once()
        self.assertEqual(batch.verified, 1)
        self.assertEqual(batch.deferred, 1)

    def test_index_failure_remains_retryable_after_publication_succeeded(self):
        with patch.object(self.pub.subprocess, 'run', return_value=self.result('verified')):
            first = self.pub.PublicationBatch()
            first.publish(self.card, 'card', generated=True)
            self.index.return_value = False
            self.assertFalse(first.finish())
            self.assertEqual(self.row(), ('pending', 'index'))
            self.index.return_value = True
            retry = self.pub.PublicationBatch()
            self.assertEqual(self.pub.retry_pending(batch=retry), {'attempted': 1, 'failed': 0})
            self.assertTrue(retry.finish())
            self.assertEqual(self.index.call_count, 2)
            self.assertEqual(self.row(), ('verified', 'verified'))

    def test_scan_discovers_saved_card_without_outbox_receipt(self):
        import run_scan_worker as scan
        with patch.object(scan.xkb_paths, 'card_files', return_value=[self.card]), \
             patch.object(scan, 'CARDS_DIR', self.root), \
             patch.object(scan, '_get_api_key', return_value='fixture'), \
             patch.object(scan, 'scan_missing', return_value=[]), \
             patch.object(scan, '_call_llm') as llm, \
             patch.object(self.pub, 'check_backend'), \
             patch.object(self.pub.subprocess, 'run', return_value=self.result('verified')), \
             patch.object(sys, 'argv', ['scan', '--limit', '1']):
            self.assertEqual(scan.main(), 0)
        llm.assert_not_called()
        self.assertEqual(self.row(), ('verified', 'verified'))
        self.index.assert_called_once_with(1)

    def test_bookmark_recovers_when_queue_reconciliation_already_marked_done(self):
        import run_bookmark_worker as worker
        with self.pub._db() as db:
            db.execute('INSERT INTO outbox VALUES (?,?,?,?,?,0,?)',
                       ('card', str(self.card), '', 'pending', 'embed', 0))
        with patch.object(worker.xkb_paths, 'card_files', return_value=[self.card]), \
             patch.object(worker, 'CARDS_DIR', self.root), \
             patch.object(worker, '_sync_queue'), \
             patch.object(worker, '_load_queue', return_value={'items': [{'id': 'card', 'status': 'done'}]}), \
             patch.object(worker, '_sync_enriched_index', return_value=True) as index, \
             patch.object(worker, '_call_llm') as llm, \
             patch.object(self.pub, 'check_backend'), \
             patch.object(self.pub.subprocess, 'run', return_value=self.result('verified')), \
             patch.object(sys, 'argv', ['bookmark', '--limit', '1']):
            self.assertEqual(worker.main(), 0)
        llm.assert_not_called()
        index.assert_called_once()
        self.assertEqual(self.row(), ('verified', 'verified'))

    def test_bookmark_item_failure_continues_but_backend_failure_stops_generation(self):
        import run_bookmark_worker as worker
        for unavailable, expected in [(False, 2), (True, 1)]:
            with self.subTest(unavailable=unavailable):
                cards = self.root / str(unavailable)
                items = [{'id': str(i), 'status': 'todo', 'source_path': f'{i}.md'} for i in range(2)]
                with patch.object(worker.xkb_paths, 'card_files', return_value=[]), \
                     patch.object(worker, 'CARDS_DIR', cards), \
                     patch.object(worker, '_sync_queue'), patch.object(worker, '_save_queue'), \
                     patch.object(worker, '_load_queue', return_value={'items': items}), \
                     patch.object(worker, '_read_bookmark', return_value='source'), \
                     patch.object(worker, '_find_related_context', return_value=''), \
                     patch.object(worker, '_call_llm', return_value='# Saved') as llm, \
                     patch.object(worker, '_sync_enriched_index', return_value=True) as index, \
                     patch.object(worker, '_gbrain_put', side_effect=[self.pub.PublicationError('verify', unavailable=unavailable), 'published']), \
                     patch.object(self.pub, 'check_backend'), \
                     patch.object(sys, 'argv', ['bookmark', '--limit', '2']):
                    self.assertEqual(worker.main(), 1)
                self.assertEqual(llm.call_count, expected)
                index.assert_called_once()
                if unavailable:
                    self.assertEqual(items[1]['status'], 'todo')

    def test_preflight_unavailable_spends_nothing(self):
        import local_ingest as local
        with patch.object(self.pub, 'check_backend', side_effect=self.pub.PublicationError('probe', unavailable=True)), \
             patch.object(local, 'INDEX_FILE', self.root / 'index.json'), \
             patch.object(local, 'llm_call') as llm, \
             patch.object(sys, 'argv', ['local', str(self.card)]):
            self.assertEqual(local.main(), 1)
        llm.assert_not_called()
        self.index.assert_not_called()


if __name__ == '__main__':
    unittest.main()
