"""Saved cards must survive index failure, and automation must report that failure."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from _isolation import isolated_env, needs_posix_exec

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import local_ingest
import fetch_github_repos as github
import fetch_youtube_playlist as youtube
import xkb_frontmatter
import xkb_index

CARD = """---
id: sample
title: Sample evidence
type: knowledge-card
source_type: local-paper
category: research
tags: [original]
---
# Sample evidence

## 7. 雙語摘要
ZH: 範例知識
EN: Sample knowledge
"""


class IngestCompletion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cards = self.root / "cards"
        self.bookmarks = self.root / "bookmarks"
        self.source = self.root / "PMC12345.txt"
        self.source.write_text("Some source evidence.", encoding="utf-8")
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.stdout))
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))
        for module in (local_ingest, github, youtube):
            self.stack.enter_context(mock.patch.object(module, "INDEX_FILE", self.bookmarks / "search_index.json"))
            self.stack.enter_context(mock.patch.object(module, "_gbrain_put"))
            self.stack.enter_context(mock.patch.object(module, "find_related_context", return_value="related evidence"))
            self.stack.enter_context(mock.patch.object(module, "classify_content", return_value={"category": "research"}))
        self.stack.enter_context(mock.patch.object(local_ingest, "CARDS_DIR", self.cards))
        self.stack.enter_context(mock.patch.object(github, "CARDS_DIR", self.cards))
        self.stack.enter_context(mock.patch.object(github, "GITHUB_RAW_DIR", self.bookmarks / "github"))
        self.stack.enter_context(mock.patch.object(youtube, "YOUTUBE_DIR", self.bookmarks / "youtube"))
        self.local_llm = self.stack.enter_context(mock.patch.object(local_ingest, "llm_call", return_value=CARD))
        self.stack.enter_context(mock.patch.object(github, "_llm_call", return_value=CARD))
        self.stack.enter_context(mock.patch.object(github, "load_env_key", return_value="test-only"))
        self.stack.enter_context(mock.patch.object(github, "get_readme_preview", return_value="Readme evidence"))
        self.stack.enter_context(mock.patch.object(github, "gh_api", return_value=[{
            "full_name": "sample/repo", "html_url": "https://github.com/sample/repo",
            "description": "Example repository", "language": "Python",
        }]))
        self.stack.enter_context(mock.patch.object(youtube, "get_playlist_videos", return_value=[
            {"id": "sample-video", "title": "Sample video", "duration": 120},
        ]))
        self.stack.enter_context(mock.patch.object(youtube, "download_subtitles", return_value=("Evidence", "en")))
        self.stack.enter_context(mock.patch.object(youtube, "generate_card", return_value=CARD))

    def test_all_ingesters_preserve_cards_and_fail_when_index_fails(self):
        cases = [
            (local_ingest, [str(self.source)], self.cards / f"{local_ingest.card_id_for_file(self.source)}.md", 2),
            (github, ["--forks"], self.cards / "github_fork-sample-repo.md", 2),
            (youtube, ["--playlist", "https://example.test/playlist"], self.bookmarks / "youtube/sample-video.md", 0),
        ]
        for module, args, card, success_code in cases:
            for rebuilt in (False, True):
                with self.subTest(module=module.__name__, rebuilt=rebuilt):
                    self.stdout.seek(0)
                    self.stdout.truncate()
                    self.stderr.seek(0)
                    self.stderr.truncate()
                    with mock.patch.object(sys, "argv", [module.__name__, *args]), \
                         mock.patch.object(xkb_index, "rebuild", return_value=rebuilt) as rebuild:
                        code = module.main()
                    self.assertEqual(code, success_code if rebuilt else 1)
                    rebuild.assert_called_once_with()
                    self.assertIn("Sample evidence", card.read_text(encoding="utf-8"))
                    if not rebuilt:
                        self.assertIn("card(s) saved", self.stderr.getvalue())
                        self.assertIn("xkb_index.py --rebuild", self.stderr.getvalue())
                        self.assertNotIn("✅ 完成", self.stdout.getvalue())

    def test_long_local_dry_run_does_not_call_models_or_write_cards(self):
        self.source.write_text("Long evidence. " * 2000, encoding="utf-8")
        with mock.patch.object(sys, "argv", ["local_ingest", str(self.source), "--dry-run"]), \
             mock.patch.object(local_ingest, "condense_long_content") as condense, \
             mock.patch.object(xkb_index, "rebuild") as rebuild:
            self.assertEqual(local_ingest.main(), 0)
        condense.assert_not_called()
        self.local_llm.assert_not_called()
        rebuild.assert_not_called()
        self.assertFalse(self.cards.exists())

    def test_local_shared_prompt_retains_source_and_persists_overrides(self):
        result = local_ingest.process_file(self.source, "", set(), [], "learning", ["personal"], False)
        prompt = self.local_llm.call_args.args[0]
        self.assertIn(self.source.name, prompt)
        self.assertIn("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12345/", prompt)
        self.assertIn("source_type: local-paper", prompt)
        self.assertIn("Media Evidence", prompt)
        self.assertIn("related evidence", prompt)
        content = next(self.cards.iterdir()).read_text(encoding="utf-8")
        self.assertEqual(xkb_frontmatter.get(content, "category"), "learning")
        self.assertEqual(xkb_frontmatter.get(content, "tags"), '[original, "personal"]')
        self.assertEqual(result["summary"], "範例知識 | Sample knowledge")

    def test_appending_tags_preserves_empty_fields_and_quoted_commas(self):
        for tags, expected in (
            ("", '["personal"]'),
            ('["research, methods", original]', '["research, methods", original, "personal"]'),
            ("['research, methods', original]", "['research, methods', original, \"personal\"]"),
        ):
            with self.subTest(tags=tags):
                self.local_llm.return_value = CARD.replace("tags: [original]", f"tags: {tags}\nsensitivity: public")
                local_ingest.process_file(self.source, "", set(), [], "learning", ["personal"], False)
                content = next(self.cards.iterdir()).read_text(encoding="utf-8")
                self.assertEqual(xkb_frontmatter.get(content, "tags"), expected)
                self.assertEqual(xkb_frontmatter.get(content, "sensitivity"), "public")


@needs_posix_exec
class IngestShellContracts(unittest.TestCase):
    def test_sync_wrappers_propagate_failure_and_accept_new_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            trace = root / "calls"
            fake_python = bin_dir / "python3"
            fake_python.write_text(
                '#!/bin/bash\necho "$1" >> "$CALL_TRACE"\n'
                'case "$1" in *fetch_*) exit "$FETCH_STATUS" ;; esac\nexit 0\n',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            # A vector file makes the YouTube wrapper attempt the next stage on success.
            vector = root / "memory/bookmarks/vector_index.json"
            vector.parent.mkdir(parents=True)
            vector.write_text("{}", encoding="utf-8")
            for script, status, expected, vector_expected in (
                ("run_github_sync.sh", 2, 0, True),
                ("run_github_sync.sh", 0, 0, False),
                ("run_github_sync.sh", 1, 1, False),
                ("run_youtube_sync.sh", 0, 0, True),
                ("run_youtube_sync.sh", 1, 1, False),
            ):
                with self.subTest(script=script, status=status):
                    trace.write_text("", encoding="utf-8")
                    env = isolated_env(root, PATH=f"{bin_dir}:{os.environ['PATH']}",
                        OPENCLAW_WORKSPACE=str(root), FETCH_STATUS=str(status), CALL_TRACE=str(trace),
                        XKB_GITHUB_LOCK=str(root / "github.lock"), XKB_GITHUB_LOG=str(root / "github.log"),
                        XKB_YOUTUBE_LOG=str(root / "youtube.log"))
                    run = subprocess.run(["bash", str(ROOT / "scripts" / script)], env=env,
                        capture_output=True, text=True, encoding="utf-8", timeout=30)
                    self.assertEqual(run.returncode, expected, run.stderr)
                    self.assertEqual("build_vector_index.py" in trace.read_text(encoding="utf-8"), vector_expected)

    def test_repair_and_search_use_configured_data_without_regenerating_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "custom data"
            card = data / "cards/sample.md"
            card.parent.mkdir(parents=True)
            card.write_text(CARD, encoding="utf-8")
            before = card.stat().st_mtime_ns
            config = root / "config.json"
            config.write_text(json.dumps({"data_dir": str(data)}), encoding="utf-8")
            env = isolated_env(root, PATH=os.environ["PATH"], XKB_CONFIG=str(config), PYTHONIOENCODING="utf-8")
            repair = subprocess.run([sys.executable, str(ROOT / "scripts/xkb_index.py"), "--rebuild"],
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(repair.returncode, 0, repair.stderr)
            index = data / "bookmarks/search_index.json"
            self.assertEqual(json.loads(index.read_text(encoding="utf-8"))["count"], 1)
            self.assertEqual(card.stat().st_mtime_ns, before)
            # Also prove the search wrapper can build its missing index at this path.
            index.unlink()
            search = subprocess.run(["bash", str(ROOT / "scripts/search_bookmarks.sh"), "Sample", "evidence"],
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(search.returncode, 0, search.stderr)
            self.assertIn("Sample evidence", search.stdout)
            self.assertTrue(index.exists())


if __name__ == "__main__":
    unittest.main()
