"""Exercise the real MCP process against local and HTTP Knowledge Service cores."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _isolation import isolated_env
import xkb_memory_service as service
import xkb_recall_server as mcp
from xkb_doctor import probe, assess


class RecallTransports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        data = root / "data"
        index = data / "bookmarks" / "search_index.json"
        index.parent.mkdir(parents=True)
        self.items = [
            {"id": "answer", "title": "Aurora deployment", "summary": "Aurora deployment uses blue green releases.", "namespace": "private"},
            {"id": "noise", "title": "Aurora deployment poster", "summary": "A poster with no operational instructions.", "namespace": "private"},
            {"id": "secret", "title": "Aurora deployment", "summary": "Other tenant secret", "namespace": "other"},
        ]
        index.write_text(json.dumps({"items": self.items}), encoding="utf-8")
        self.env = isolated_env(root, XKB_DATA_DIR=str(data),
                                XKB_SERVICE_DB=str(root / "knowledge.sqlite"),
                                XKB_CONFIG=str(root / "absent.json"),
                                XKB_EMBEDDING_TIMEOUT="1", XKB_XBRAIN_TIMEOUT="1")
        self.store = service.Store(root / "knowledge.sqlite")
        self.store.catalog.index_file = index
        self.store.catalog.cards_dir = data / "cards"
        self.store.catalog.wiki_topics_dir = data / "x-knowledge-base" / "wiki" / "topics"
        for name in ("_semantic_search", "_wiki_search"):
            patch = mock.patch.object(self.store.catalog, name, return_value=[])
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(service.xkb_jev, "relevance", return_value=None)
        self.judge = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.dict(os.environ, self.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.server = service.ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        self.server.store = self.store
        self.server.auth = service.AuthPolicy()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def http(self, query="Aurora deployment", limit=10):
        req = Request(self.url + "/v1/recall", method="POST",
                      data=json.dumps({"query": query, "limit": limit}).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def test_local_mcp_and_http_return_the_same_full_packet(self):
        expected = self.http()
        actual = probe("Aurora deployment", env=self.env)
        for key, value in expected.items():
            self.assertEqual(actual[key], value, key)
        self.assertEqual(actual["results"], actual["records"])
        self.assertEqual(actual["formatted_text"], actual["context"])
        self.assertNotIn("secret", [r["id"] for r in actual["records"]])

    def test_http_mcp_uses_the_service_judge_and_preserves_diagnostics(self):
        self.judge.side_effect = lambda query, candidates: {
            key: 0.9 if "blue green" in text else 0.01 for key, text in candidates}
        expected = self.http()
        actual = probe("Aurora deployment", env={**self.env, "XKB_MEMORY_SERVICE_URL": self.url})
        for key, value in expected.items():
            self.assertEqual(actual[key], value, key)
        self.assertEqual([r["id"] for r in actual["records"]], ["answer"])
        self.assertEqual(actual["judge"]["status"], "judged")

    def test_remote_auth_failure_is_not_an_empty_local_success(self):
        self.server.auth = service.AuthPolicy({"tokens": {
            "fixture-token-at-least-16": {"namespace": "private"}}})
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            probe("Aurora deployment", env={**self.env, "XKB_MEMORY_SERVICE_URL": self.url})
        result = probe("Aurora deployment", env={**self.env,
            "XKB_MEMORY_SERVICE_URL": self.url, "XKB_SERVICE_TOKEN": "fixture-token-at-least-16"})
        self.assertEqual(result["count"], 2)
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            probe("Aurora deployment", env={**self.env, "XKB_MEMORY_SERVICE_URL": self.url,
                "XKB_SERVICE_TOKEN": "fixture-token-at-least-16", "XKB_NAMESPACE": "other"})

    def test_remote_unreachable_is_reported_without_local_fallback(self):
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaisesRegex(RuntimeError, "unreachable"):
            probe("Aurora deployment", env={**self.env, "XKB_MEMORY_SERVICE_URL": self.url})

    def test_empty_and_skipped_queries_are_distinguishable(self):
        missing = probe("zirconium", env=self.env)
        skipped = probe("hello", env=self.env)
        self.assertEqual(missing["count"], 0)
        self.assertNotEqual(missing["retrieval_mode"], "skipped")
        self.assertEqual(skipped["retrieval_mode"], "skipped")

    def test_query_that_looks_like_a_cli_flag_is_still_a_query(self):
        packet = probe("--help", env=self.env)
        self.assertEqual(packet["query"], "--help")

    def test_invalid_remote_packet_is_an_error(self):
        with mock.patch.object(self.store, "knowledge_recall", return_value={"records": []}):
            with self.assertRaisesRegex(RuntimeError, "not an XKB"):
                probe("Aurora deployment", env={**self.env, "XKB_MEMORY_SERVICE_URL": self.url})

    def test_local_mcp_reaches_the_judge_endpoint(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Judge(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                answers = {key: {"noul": .9 if "blue green" in value["instructions"] else .01}
                           for key, value in body["questions"].items()}
                data = json.dumps({"answers": answers}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Judge)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {**self.env, "LLM_API_URL": f"http://127.0.0.1:{server.server_port}",
                   "LLM_API_KEY": "fixture-only"}
            packet = probe("Aurora deployment", env=env)
            self.assertEqual([r["id"] for r in packet["records"]], ["answer"])
            self.assertEqual(packet["judge"]["status"], "judged")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_doctor_does_not_claim_fallback_is_full_readiness(self):
        packet = probe("Aurora deployment", env=self.env)
        report = assess(packet)
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "degraded")
        self.assertFalse(assess(packet, require_semantic=True)["ok"])
        self.assertFalse(assess(packet, require_judge=True)["ok"])
        self.assertFalse(assess(packet, expect_ids=["not-present"])["ok"])

    def test_bad_input_never_launches_a_worker(self):
        with mock.patch.object(mcp.subprocess, "run") as run:
            for query, limit in [("", 10), (None, 10), ("query", True), ("query", 51)]:
                self.assertEqual(mcp._run_recall_structured(query, limit)["status"], "failed")
            run.assert_not_called()

    def test_store_transaction_closes_and_rolls_back(self):
        with self.store.connect() as db:
            db.execute("CREATE TABLE fixture (value TEXT)")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with self.store.connect() as db:
                db.execute("INSERT INTO fixture VALUES ('not committed')")
                raise RuntimeError("abort")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM fixture").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
