"""Exercise the real MCP process against local and HTTP Knowledge Service cores."""
from __future__ import annotations

import json
import contextlib
import io
import os
import subprocess
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
from xkb_evidence import identity_key, identity_source
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
        def unavailable(query, **kwargs):
            kwargs["diagnostics"].update(available=False, attempted=False, used=False, status="unavailable")
            return []
        patch = mock.patch.object(service, "xbrain_query", side_effect=unavailable)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch("continuity_recall.recall_semantic", return_value=None)
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

    def http(self, query="Aurora deployment", limit=10, options=None):
        req = Request(self.url + "/v1/recall", method="POST",
                      data=json.dumps({"query": query, "limit": limit, "options": options}).encode(),
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

    def test_keyword_prefers_complete_content_but_preserves_partial_fallback(self):
        self.items = [
            {"id": "complete", "title": "Atlas recovery", "summary": "Restore rehearsal"},
            {"id": "partial", "title": "Atlas notes", "summary": "General notes"},
            {"id": "hidden", "title": "Atlas recovery procedure", "namespace": "other"},
        ]
        self.store.catalog.index_file.write_text(json.dumps({"items": self.items}), encoding="utf-8")
        def ids(query):
            return {r["id"] for r in self.http(query, options={"semantic": False})["records"]}
        self.assertEqual(ids("Atlas recovery"), {"complete"})
        self.assertEqual(ids("Atlas repair"), {"complete", "partial"})
        # A full match in another namespace must not suppress allowed partials.
        self.assertEqual(ids("Atlas recovery procedure"), {"complete", "partial"})
        self.assertEqual(ids("Atlas and recovery"), {"complete"})

    def test_keyword_metadata_is_not_searchable_content(self):
        wiki = self.store.catalog.wiki_topics_dir
        wiki.mkdir(parents=True)
        (wiki / "metadataonly.md").write_text(
            "---\nnamespace: private\nvisibility: private\nsource_url: https://metadataonly.test\n"
            "title: Rescue manual\n---\n# Restore guide\nRehearse backups.", encoding="utf-8")
        self.store.catalog.index_file.write_text(json.dumps({"items": [
            {"id": "metadataonly", "title": "Rescue manual", "tags": ["rehearsal"],
             "namespace": "private", "source_url": "https://metadataonly.test"}]}), encoding="utf-8")
        for query in ("metadataonly", "private", "namespace"):
            with self.subTest(query=query):
                self.assertEqual(self.http(query, options={"semantic": False})["records"], [])
        self.assertEqual(self.http("Rescue manual", options={"semantic": False})["count"], 2)
        self.assertEqual(self.http("rehearsal", options={"semantic": False})["count"], 1)

    def test_keyword_retains_natural_language_cjk_partial_matches(self):
        self.store.catalog.index_file.write_text(json.dumps({"items": [
            {"id": "video", "title": "產品廣告影片", "summary": "分鏡製作流程"}]}), encoding="utf-8")
        result = self.http("我想做一支產品廣告影片", options={"semantic": False})
        self.assertEqual([r["id"] for r in result["records"]], ["video"])

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

    def test_punctuated_acknowledgement_skips_local_and_remote_mcp(self):
        with mock.patch.object(self.store.catalog, "search", side_effect=AssertionError("ack searched")):
            for env in (self.env, {**self.env, "XKB_MEMORY_SERVICE_URL": self.url}):
                packet = probe("好，收到", env=env)
                self.assertEqual(packet["retrieval_mode"], "skipped")
                self.assertEqual(packet["skip_reason"], "acknowledgement")
                self.assertEqual(packet["records"], [])
                self.assertFalse(packet["semantic_retrieval_attempted"])

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
        with mock.patch("xkb_recall.subprocess.run") as run:
            for query, limit in [("", 10), (None, 10), ("query", True), ("query", 51)]:
                self.assertEqual(mcp._run_recall_structured(query, limit)["status"], "failed")
            run.assert_not_called()

    def usage(self, namespace="private"):
        with self.store.connect() as db:
            return {identity_source(r["record_id"]): dict(r) for r in db.execute(
                "SELECT * FROM recall_usage WHERE namespace=?", (namespace,))}

    def test_usage_distinguishes_verdicts_from_returned_after_limit(self):
        self.judge.side_effect = lambda query, candidates: {key: .9 for key, _ in candidates}
        candidates = self.store.catalog.search("Aurora deployment", 10)
        with mock.patch.object(self.store.catalog, "search", return_value=candidates):
            packet = self.http(limit=1)
        rows = self.usage()
        self.assertEqual(set(rows), {"answer", "noise"})
        for key, row in rows.items():
            self.assertEqual(row["judged_count"], 1)
            self.assertEqual(row["relevant_count"], 1)
            self.assertEqual(row["returned_count"], int(key == packet["records"][0]["id"]))
        self.assertEqual(self.store.demoted_ids(after=1), set())

    def test_usage_is_namespace_scoped_and_does_not_reinterpret_legacy(self):
        self.store.record_usage([("answer", .9, True), ("noise", .1, False)] * 6)
        self.assertEqual(self.store.demoted_ids(), set())
        items = [{"id": "noise", "judge": .01}]
        for _ in range(5):
            self.store.record_recall_usage("private", items, [], {"status": "judged"})
        self.assertEqual(self.store.demoted_ids(), {identity_key({"id": "noise"})})
        self.assertEqual(self.store.demoted_ids(namespace="other"), set())
        self.store.record_recall_usage("other", [{"id": "noise", "judge": .9}], [], {"status": "judged"})
        self.assertEqual(self.store.demoted_ids(), {identity_key({"id": "noise"})})
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT injected_count FROM knowledge_usage WHERE record_id='answer'").fetchone()[0], 6)

    def test_unknown_verdicts_and_duplicate_legs_do_not_manufacture_rejections(self):
        items = [{"id": "same", "judge": .01}, {"id": "same", "judge": None}]
        for status in ("judged", "unavailable", "off", "error"):
            self.store.record_recall_usage("private", items, items[:1], {"status": status})
        row = self.usage()["same"]
        self.assertEqual((row["considered_count"], row["returned_count"], row["judged_count"]), (4, 4, 0))
        items[1]["judge"] = .9
        self.store.record_recall_usage("private", items, [], {"status": "judged"})
        row = self.usage()["same"]
        self.assertEqual((row["considered_count"], row["judged_count"], row["relevant_count"]), (5, 1, 1))

    def test_rejections_are_recorded_and_revival_is_immediate(self):
        # Just below the floor: display rounding must not turn this positive.
        self.judge.side_effect = lambda query, candidates: {key: .0999 for key, _ in candidates}
        for _ in range(5):
            self.assertEqual(self.http()["records"], [])
        self.assertEqual(self.store.demoted_ids(), {identity_key({"id": key}) for key in ("answer", "noise")})
        self.assertTrue(all(r["returned_count"] == 0 for r in self.usage().values()))
        self.judge.side_effect = lambda query, candidates: {key: .9 for key, _ in candidates}
        packet = self.http()
        self.assertFalse(any(r.get("demoted") for r in packet["records"]))
        self.assertEqual(self.store.demoted_ids(), set())

    def test_usage_write_failure_does_not_break_recall(self):
        with mock.patch.object(self.store, "record_recall_usage", side_effect=sqlite3.OperationalError("fixture")):
            self.assertEqual(self.http()["count"], 2)

    def test_router_cli_uses_shared_packet_and_reports_remote_failure(self):
        env = {**self.env, "XKB_MEMORY_SERVICE_URL": self.url}
        command = [sys.executable, str(ROOT / "scripts" / "recall_router.py"), "Aurora deployment", "--json"]
        result = subprocess.run(command, env=env, capture_output=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertEqual(packet["results"], self.http()["records"])
        self.assertEqual(packet["connection"]["transport"], "http")
        self.server.auth = service.AuthPolicy({"tokens": {"fixture-token-at-least-16": {"namespace": "private"}}})
        result = subprocess.run(command, env=env, capture_output=True, encoding="utf-8", timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTP 401", json.loads(result.stdout)["error"])

    def test_ask_uses_remote_evidence_including_conversations_without_local_embedding_key(self):
        import xkb_ask
        session = self.store.open_session({"namespace": "private", "session_key": "fixture-ask"})
        turn = self.store.start_turn({"session_id": session["session_id"], "query": "Aurora deployment"})
        self.store.complete_turn(turn["turn_id"], {"query": "Aurora deployment", "answer": "Aurora deployment uses a canary first."})
        env_file = Path(self.tmp.name) / "remote.env"
        env_file.write_text(f"XKB_MEMORY_SERVICE_URL={self.url}\n", encoding="utf-8")
        with mock.patch.object(sys, "argv", ["xkb_ask.py", "Aurora deployment", "--json", "--env-file", str(env_file)]), \
                mock.patch.object(xkb_ask, "llm_call", return_value="fixture answer") as llm, \
                contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(xkb_ask.main(), 0)
        output = json.loads(stdout.getvalue())
        self.assertIn("canary first", llm.call_args.args[0])
        self.assertEqual(output["recall"]["connection"]["transport"], "http")
        self.assertTrue(any(r["record_type"] == "conversation_trace" and r["source"] for r in output["evidence_refs"]))
        self.assertNotIn("XKB_ENV_FILE", os.environ)
        self.server.auth = service.AuthPolicy({"tokens": {"fixture-token-at-least-16": {"namespace": "private"}}})
        with mock.patch.object(sys, "argv", ["xkb_ask.py", "Aurora deployment", "--env-file", str(env_file)]), \
                mock.patch.object(xkb_ask, "llm_call") as llm, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(xkb_ask.main(), 2)
            llm.assert_not_called()

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

    def test_layer_options_are_enforced_by_http_and_the_local_worker(self):
        from xkb_recall import run_configured
        topic_dir = self.store.catalog.wiki_topics_dir
        topic_dir.mkdir(parents=True)
        (topic_dir / "aurora.md").write_text("# Aurora deployment\nAurora deployment guide", encoding="utf-8")
        options = {"cards": False, "semantic": False, "conversations": False, "max_wiki": 1}
        packet = self.http(options=options)
        self.assertEqual([r["record_type"] for r in packet["records"]], ["wiki_topic"])
        self.assertEqual(packet["retrieval_mode"], "keyword")
        self.assertEqual(packet["backends"]["cards"]["status"], "disabled")
        self.assertFalse(packet["backends"]["conversation"]["attempted"])
        for endpoint in ("", self.url):
            with mock.patch.dict(os.environ, {"XKB_MEMORY_SERVICE_URL": endpoint}):
                actual = run_configured("Aurora deployment", options=options)
            self.assertEqual(actual["records"], packet["records"])
            self.assertEqual(actual["options"], packet["options"])
        without_wiki = self.http(options={"wiki": False, "semantic": False, "max_cards": 1})
        self.assertEqual([r["record_type"] for r in without_wiki["records"]], ["knowledge_card"])

    def test_invalid_or_unacknowledged_options_never_silently_change_search(self):
        from urllib.error import HTTPError
        from xkb_recall import run_configured
        for options in ({"wiki": "false"}, {"max_cards": -1}, {"max_wiki": True}, {"unknown": True}):
            with self.assertRaises(HTTPError) as exc:
                self.http(options=options)
            self.assertEqual(exc.exception.code, 400)
        packet = self.http()
        packet.pop("options")
        with mock.patch.object(self.store, "knowledge_recall", return_value=packet), \
                mock.patch.dict(os.environ, {"XKB_MEMORY_SERVICE_URL": self.url}):
            with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
                run_configured("Aurora deployment", options={"wiki": False})

    def test_wiki_only_results_do_not_claim_the_card_backend_worked(self):
        from types import SimpleNamespace
        hit = SimpleNamespace(source_file="wiki/topics/a.md", section="A", excerpt="Aurora deployment guide",
                              source_type="wiki_semantic", score=.8, url="")
        with mock.patch("continuity_recall.recall_semantic", return_value=[hit]) as wiki:
            packet = self.http()
            self.assertEqual(wiki.call_args.kwargs["top_k"], 5)
            self.http(options={"max_wiki": 1})
            self.assertEqual(wiki.call_args.kwargs["top_k"], 1)
        self.assertEqual(packet["retrieval_mode"], "wiki_semantic")
        self.assertFalse(packet["backends"]["cards"]["used"])
        self.assertTrue(packet["backends"]["wiki"]["used"])
        self.assertEqual(packet["records"][0]["section"], "A")
        self.assertEqual(assess(packet, require_semantic=True)["status"], "degraded")

    def test_empty_failed_and_disabled_sources_are_distinct_and_reset(self):
        from xkb_memory_service import KnowledgeCatalog
        with mock.patch("continuity_recall.recall_semantic", return_value=[]):
            self.assertEqual(self.http()["backends"]["wiki"]["status"], "empty")
        with mock.patch("continuity_recall.recall_semantic", side_effect=RuntimeError("fixture")):
            self.assertEqual(self.http()["backends"]["wiki"]["status"], "error")
        packet = self.http(options={"semantic": False})
        self.assertEqual(packet["backends"]["wiki"]["status"], "disabled")
        with mock.patch.object(self.store, "recall", side_effect=sqlite3.OperationalError("fixture")):
            packet = self.http()
        self.assertEqual(packet["backends"]["conversation"]["status"], "error")
        self.assertEqual(packet["count"], 2)


if __name__ == "__main__":
    unittest.main()
