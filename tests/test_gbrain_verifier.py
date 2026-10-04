"""Execute the real Bun verifier against an isolated read-only SQL adapter."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from _isolation import isolated_env

ROOT = Path(__file__).resolve().parents[1]
BUN = shutil.which('bun')


@unittest.skipUnless(BUN, 'Bun runtime required for the TypeScript verifier')
class VerifierProtocol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        files = {
            'src/core/config.ts': 'export function loadConfig() { return {database_url: "fixture"}; }',
            'src/core/markdown.ts': 'export function parseMarkdown(content) { return JSON.parse(content); }',
            'src/core/chunkers/recursive.ts': 'export function chunkText(text) { return [{text}]; }',
            'node_modules/postgres/src/index.js': '''
import {readFileSync} from 'node:fs';
export default function postgres() {
  const data = JSON.parse(readFileSync(process.env.FIXTURE_DB, 'utf8'));
  return {
    async begin(mode, callback) {
      if (mode !== 'read only' || data.unavailable) throw new Error('not readable');
      return callback(async (parts) => {
        const query = parts.join('?');
        if (!query.startsWith('SELECT')) throw new Error('write attempted');
        if (query.includes('FROM pages')) return data.pages;
        if (query.includes('FROM tags')) return data.tags;
        if (query.includes('FROM content_chunks')) return data.chunks;
        if (query === 'SELECT 1') return [{one: 1}];
        throw new Error('unknown query');
      });
    },
    async end() {},
  };
}
''',
        }
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        self.expected = {'compiled_truth': 'Current body', 'timeline': 'Current history',
                         'frontmatter': {'id': '2016309053379772746', 'category': 'tech'},
                         'title': 'Evidence', 'type': 'knowledge-card', 'tags': ['fixture']}
        self.data = {'pages': [{'id': 1, **self.expected}], 'tags': [{'tag': 'fixture'}],
                     'chunks': [
                         {'chunk_index': 0, 'chunk_text': 'Current body', 'chunk_source': 'compiled_truth', 'embedded': True},
                         {'chunk_index': 1, 'chunk_text': 'Current history', 'chunk_source': 'timeline', 'embedded': True},
                     ]}

    def run_verifier(self, content=None, probe=False):
        db = self.root / 'db.json'
        db.write_text(json.dumps(self.data), encoding='utf-8')
        result = subprocess.run(
            [BUN, 'run', str(ROOT / 'scripts/gbrain_verify.ts'), *(['--probe'] if probe else ['--stdin', 'fixture'])],
            input=json.dumps(self.expected) if content is None else content,
            text=True, encoding='utf-8', capture_output=True, timeout=15,
            env=isolated_env(self.root, PATH=os.environ.get('PATH', ''),
                             GBRAIN_DIR=str(self.root), FIXTURE_DB=str(db)))
        return result.returncode, json.loads(result.stdout)['status']

    def test_matching_body_history_metadata_and_chunks_are_verified(self):
        self.assertEqual(self.run_verifier(), (0, 'verified'))

    def test_stale_history_is_not_verified(self):
        self.data['pages'][0]['timeline'] = 'Old history'
        self.assertEqual(self.run_verifier(), (1, 'mismatch'))

    def test_missing_or_stale_chunks_are_not_verified(self):
        for corruption in ('missing', 'stale', 'unembedded'):
            with self.subTest(corruption=corruption):
                chunks = [dict(c) for c in self.data['chunks']]
                if corruption == 'missing':
                    self.data['chunks'].pop()
                elif corruption == 'stale':
                    self.data['chunks'][1]['chunk_text'] = 'Old history'
                else:
                    self.data['chunks'][1]['embedded'] = False
                self.assertEqual(self.run_verifier(), (1, 'mismatch'))
                self.data['chunks'] = chunks

    def test_source_metadata_must_agree(self):
        self.data['pages'][0]['frontmatter'] = {'id': 'rounded-wrong-id', 'category': 'tech'}
        self.assertEqual(self.run_verifier(), (1, 'mismatch'))

    def test_probe_and_failures_have_distinct_protocol_states(self):
        self.assertEqual(self.run_verifier(probe=True), (0, 'ready'))
        self.assertEqual(self.run_verifier(content='invalid json'), (3, 'invalid_card'))
        self.data['unavailable'] = True
        self.assertEqual(self.run_verifier(probe=True), (2, 'unavailable'))


if __name__ == '__main__':
    unittest.main()
