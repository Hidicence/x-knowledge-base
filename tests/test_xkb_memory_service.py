from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xkb_memory_service.py"
spec = importlib.util.spec_from_file_location("xkb_memory_service", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Store = module.Store


class XKBMemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "memory.sqlite")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_session_turn_complete_recall_and_idempotency(self) -> None:
        session = self.store.open_session(
            {
                "source": "fixture",
                "agent_id": "fixture",
                "namespace": "private",
                "session_key": "session-1",
            }
        )
        resumed = self.store.open_session(
            {
                "source": "fixture",
                "agent_id": "fixture",
                "namespace": "private",
                "session_key": "session-1",
            }
        )
        self.assertFalse(session["resumed"])
        self.assertTrue(resumed["resumed"])
        turn = self.store.start_turn(
            {
                "session_id": session["session_id"],
                "turn_id": "turn-1",
                "query": "shared memory query",
            }
        )
        self.assertFalse(turn["resumed"])
        completed = self.store.complete_turn(
            "turn-1",
            {
                "session_id": session["session_id"],
                "query": "shared memory query",
                "answer": "shared memory answer",
                "status": "succeeded",
                "content": {"answer": "shared memory answer"},
            },
        )
        duplicate = self.store.complete_turn(
            "turn-1",
            {
                "session_id": session["session_id"],
                "query": "shared memory query",
                "answer": "shared memory answer",
                "status": "succeeded",
                "content": {"answer": "shared memory answer"},
            },
        )
        self.assertTrue(completed["stored"])
        self.assertEqual(completed["retrieval"]["query"], "shared memory query")
        self.assertTrue(completed["candidate_id"].startswith("candidate:"))
        with self.store.connect() as db:
            persisted = db.execute("SELECT status, retrieval_json, trace_id FROM turns WHERE turn_id=?", ("turn-1",)).fetchone()
        self.assertEqual(persisted["status"], "succeeded")
        self.assertEqual(persisted["trace_id"], completed["trace_id"])
        self.assertEqual(__import__("json").loads(persisted["retrieval_json"])["query"], "shared memory query")
        self.assertTrue(completed["distillation_job_id"].startswith("distill:"))
        self.assertTrue(self.store.list_jobs(stage="distill", status="queued")["count"] == 1)
        candidates = self.store.query_candidates(status="pending")
        self.assertEqual(candidates["count"], 1)
        self.assertEqual(candidates["candidates"][0]["source_trace_ids"], [completed["trace_id"]])
        self.assertTrue(duplicate["deduplicated"])
        recall = self.store.recall("shared memory")
        self.assertEqual(recall["count"], 1)
        self.assertIn("shared memory answer", recall["context"])

        artifact = self.store.artifact(completed["trace_id"])
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact["retrieval"]["query"], "shared memory query")

    def test_input_validation_rejects_empty_query_bad_limits_and_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "query is required"):
            self.store.knowledge_recall("  ")
        with self.assertRaisesRegex(ValueError, "limit must be an integer"):
            self.store.knowledge_recall("query", limit="10")
        with self.assertRaisesRegex(ValueError, "between 1 and 50"):
            self.store.knowledge_recall("query", limit=0)
        session = self.store.open_session({"source": "fixture", "session_key": "validation"})
        with self.assertRaisesRegex(ValueError, "namespace is required"):
            self.store.open_session({"source": "fixture", "session_key": "bad", "namespace": ""})
        self.store.start_turn({"session_id": session["session_id"], "turn_id": "validation-turn", "query": "query"})
        with self.assertRaisesRegex(ValueError, "status must be"):
            self.store.complete_turn("validation-turn", {"query": "query", "answer": "answer", "status": "running"})

    def test_cancelled_turn_is_not_stored(self) -> None:
        session = self.store.open_session({"source": "fixture", "session_key": "session-2"})
        self.store.start_turn({"session_id": session["session_id"], "turn_id": "turn-2", "query": "cancel me"})
        result = self.store.complete_turn("turn-2", {"status": "cancelled"})
        self.assertFalse(result["stored"])
        self.assertEqual(self.store.recall("cancel me")["count"], 0)

    def test_unified_recall_includes_external_knowledge_records(self) -> None:
        original = self.store.catalog

        class FixtureCatalog:
            def search(self, query, limit=10, namespace="private"):
                return {
                    "records": [{
                        "record_type": "knowledge_card",
                        "id": "fixture-card",
                        "title": "Fixture knowledge",
                        "summary": "shared knowledge evidence",
                        "score": 3,
                    }]
                }

        self.store.catalog = FixtureCatalog()
        try:
            result = self.store.knowledge_recall("shared knowledge")
        finally:
            self.store.catalog = original
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["records"][0]["record_type"], "knowledge_card")
        self.assertIn("Fixture knowledge", result["context"])

    def test_catalog_fails_closed_across_namespaces_and_exposes_metadata(self) -> None:
        catalog = module.KnowledgeCatalog()
        root = Path(self.tmp.name)
        catalog.cards_dir = root / "cards"
        catalog.wiki_topics_dir = root / "topics"
        catalog.index_file = root / "index.json"
        catalog.cards_dir.mkdir()
        catalog.wiki_topics_dir.mkdir()
        (catalog.cards_dir / "secret.md").write_text("---\nnamespace: team-a\nsensitivity: internal\n---\nsecret", encoding="utf-8")
        (catalog.index_file).write_text('{"items":[{"id":"secret","title":"Secret"}]}', encoding="utf-8")
        self.assertIsNone(catalog.card("secret", "team-b"))
        visible = catalog.card("secret", "team-a")
        self.assertEqual(visible["namespace"], "team-a")
        self.assertEqual(catalog.search("secret", namespace="team-b")["count"], 0)

    def test_semantic_backend_is_used_and_declared(self) -> None:
        original = module.xbrain_query

        def fixture_query(query, *, limit, no_expand, semantic):
            self.assertEqual(query, "semantic fixture")
            self.assertTrue(semantic)
            return [{
                "slug": "fixture-semantic-card",
                "title": "Semantic fixture",
                "chunk_text": "vector evidence",
                "source_url": "https://example.test/source",
                "type": "knowledge-card",
                "score": 0.91,
            }]

        module.xbrain_query = fixture_query
        try:
            result = self.store.catalog.search("semantic fixture", 3)
        finally:
            module.xbrain_query = original
        self.assertEqual(result["retrieval_mode"], "xbrain_hybrid")
        self.assertEqual(result["records"][0]["retrieval"], "xbrain_hybrid")
        self.assertEqual(result["records"][0]["score"], 0.91)

    def test_recall_packet_explains_acl_namespace_filtering_and_backend(self) -> None:
        original = module.xbrain_query

        def fixture_query(query, *, limit, no_expand, semantic):
            return [
                {"slug": "allowed", "title": "Allowed", "chunk_text": "visible", "namespace": "team-a"},
                {"slug": "hidden", "title": "Hidden", "chunk_text": "secret", "namespace": "team-b"},
            ]

        module.xbrain_query = fixture_query
        try:
            result = self.store.knowledge_recall("acl packet", namespace="team-a")
        finally:
            module.xbrain_query = original
        self.assertEqual(result["request_namespace"], "team-a")
        self.assertEqual(result["acl_policy"]["decision"], "allow_public_or_matching_namespace")
        self.assertEqual(result["filtered_counts"]["semantic"], 1)
        self.assertIn("records_filtered_by_acl", result["warnings"])
        self.assertEqual(result["semantic_backend"]["status"], "used")
        # The breakdown must say which layer was filtered, otherwise "nothing
        # came back" and "the wiki layer was filtered out" look identical.
        self.assertEqual(result["filtered_counts"]["total"], 1)
        self.assertEqual(result["filtered_counts"]["by_layer"]["semantic"], 1)
        self.assertEqual(result["filtered_counts"]["by_layer"]["conversation"], 0)

    def test_acl_filter_counts_are_attributed_per_layer(self) -> None:
        """A quiet result must say which layer the ACL removed."""
        module = importlib.import_module("xkb_memory_service")
        original = module.xbrain_query
        module.xbrain_query = None  # force the keyword path, where layers are known
        try:
            result = self.store.knowledge_recall("anything", namespace="team-a")
        finally:
            module.xbrain_query = original
        counts = result["filtered_counts"]
        self.assertEqual(result["retrieval_mode"], "keyword_fallback")
        self.assertEqual(set(counts["by_layer"]), {"card", "wiki", "semantic", "conversation"})
        self.assertEqual(counts["total"], sum(counts["by_layer"].values()))
        # No semantic backend ran, so nothing can be attributed to it.
        self.assertEqual(counts["by_layer"]["semantic"], 0)
        # Legacy channel keys stay consistent with the layer breakdown.
        self.assertEqual(counts["keyword"], counts["by_layer"]["card"] + counts["by_layer"]["wiki"])

    def test_turn_start_persists_retrieval_packet_and_source_ids(self) -> None:
        original = self.store.knowledge_recall
        packet = {
            "schema": "xkb-knowledge-service.v1",
            "query": "turn query",
            "records": [{"id": "knowledge-1", "record_type": "knowledge_chunk", "score": 0.8}],
            "context": "historical evidence",
            "retrieval_mode": "xbrain_hybrid",
            "semantic_retrieval_attempted": True,
            "warnings": [],
        }
        self.store.knowledge_recall = lambda query, limit=10, namespace="private": packet
        try:
            session = self.store.open_session({"source": "fixture", "session_key": "retrieval-session"})
            started = self.store.start_turn({"session_id": session["session_id"], "turn_id": "retrieval-turn", "query": "turn query"})
            resumed = self.store.start_turn({"session_id": session["session_id"], "turn_id": "retrieval-turn", "query": "turn query"})
        finally:
            self.store.knowledge_recall = original
        self.assertEqual(started["source_memory_ids"], ["knowledge-1"])
        self.assertEqual(started["retrieval"]["retrieval_mode"], "xbrain_hybrid")
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["retrieval"]["query"], "turn query")

    def test_turn_start_rejects_idempotency_conflicts(self) -> None:
        first = self.store.open_session({"source": "fixture", "session_key": "identity-1", "namespace": "private"})
        other = self.store.open_session({"source": "fixture", "session_key": "identity-2", "namespace": "private"})
        self.store.start_turn({"session_id": first["session_id"], "turn_id": "identity-turn", "query": "original"})
        with self.assertRaisesRegex(ValueError, "session_id"):
            self.store.start_turn({"session_id": other["session_id"], "turn_id": "identity-turn", "query": "original"})
        with self.assertRaisesRegex(ValueError, "query"):
            self.store.start_turn({"session_id": first["session_id"], "turn_id": "identity-turn", "query": "changed"})
        with self.assertRaisesRegex(ValueError, "namespace"):
            self.store.start_turn({"session_id": first["session_id"], "turn_id": "identity-turn", "query": "original", "namespace": "shared"})

    def test_recall_is_scoped_to_session_namespace_and_start_requires_matching_namespace(self) -> None:
        private = self.store.open_session({"source": "fixture", "session_key": "private", "namespace": "private"})
        shared = self.store.open_session({"source": "fixture", "session_key": "shared", "namespace": "shared"})
        self.store.start_turn({"session_id": private["session_id"], "turn_id": "private-turn", "query": "same namespace secret"})
        self.store.complete_turn("private-turn", {"query": "same namespace secret", "answer": "private answer"})
        self.store.start_turn({"session_id": shared["session_id"], "turn_id": "shared-turn", "query": "same namespace secret"})
        self.store.complete_turn("shared-turn", {"query": "same namespace secret", "answer": "shared answer"})
        self.assertEqual([m["answer"] for m in self.store.recall("same namespace", namespace="private")["memories"]], ["private answer"])
        self.assertEqual([m["answer"] for m in self.store.recall("same namespace", namespace="shared")["memories"]], ["shared answer"])
        with self.assertRaisesRegex(ValueError, "namespace conflicts with session"):
            self.store.start_turn({"session_id": private["session_id"], "turn_id": "bad-namespace", "query": "q", "namespace": "shared"})

    def test_turn_start_rejects_unknown_session(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown session_id"):
            self.store.start_turn({"session_id": "missing", "turn_id": "missing-turn", "query": "q"})

    def test_service_run_l1_worker_updates_job_and_keeps_candidate_pending(self) -> None:
        session = self.store.open_session({"source": "fixture", "session_key": "worker-session"})
        self.store.start_turn({"session_id": session["session_id"], "turn_id": "worker-turn", "query": "worker query"})
        completed = self.store.complete_turn("worker-turn", {"query": "worker query", "answer": "user preference evidence", "content": {"answer": "user preference evidence"}})
        dry = self.store.run_l1_to_candidate([completed["distillation_job_id"]], dry_run=True)
        self.assertEqual(dry["selected"], [completed["distillation_job_id"]])
        self.assertTrue(dry["dry_run"])
        result = self.store.run_l1_to_candidate([completed["distillation_job_id"]])
        self.assertFalse(result["promotion_performed"])
        self.assertEqual(result["results"][0]["status"], "succeeded")
        self.assertEqual(self.store.query_candidates()["candidates"][0]["status"], "pending")
        self.assertEqual(self.store.list_jobs(stage="distill")["jobs"][0]["status"], "succeeded")

    def test_pipeline_snapshot_is_read_only_and_lists_known_stages(self) -> None:
        snapshot = self.store.catalog.pipeline_snapshot(days=7)
        self.assertTrue(snapshot["read_only"])
        self.assertEqual(snapshot["control_plane"], "observed_status_only")
        stages = {item["stage"] for item in snapshot["stages"]}
        self.assertEqual(
            stages,
            {"ingest", "card_generation", "index", "distill", "promotion", "publish"},
        )
        self.assertTrue(all(item["control"] == "read_only" for item in snapshot["stages"]))
        self.assertIn("summary", snapshot)

    def test_job_event_is_persisted_and_filterable_without_running_worker(self) -> None:
        created = self.store.record_job_event({
            "job_id": "job-1",
            "stage": "index",
            "worker": "build_vector_index.py",
            "status": "running",
            "input_ref": "cards:batch-1",
            "metadata": {"count": 3},
        })
        self.assertTrue(created["stored"])
        self.assertTrue(created["observed_only"])
        completed = self.store.record_job_event({
            "job_id": "job-1",
            "stage": "index",
            "worker": "build_vector_index.py",
            "status": "succeeded",
            "output_ref": "vector-index:batch-1",
        })
        self.assertEqual(completed["status"], "succeeded")
        result = self.store.list_jobs(stage="index", status="succeeded")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["jobs"][0]["output_ref"], "vector-index:batch-1")
        self.assertFalse(self.store.list_jobs(stage="distill")["count"])

    def test_stale_recovery_is_dry_run_by_default_and_scoped_audited_and_requeueable(self) -> None:
        timestamp = "2026-01-01T00:00:00+00:00"
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO jobs(job_id,stage,worker,status,started_at,retryable,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("stale-1", "distill", "xkb_l1_to_candidate", "running", timestamp, 0, "{}", timestamp, timestamp),
            )
            db.execute(
                "INSERT INTO jobs(job_id,stage,worker,status,started_at,retryable,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("other-1", "index", "build_vector_index.py", "running", timestamp, 0, "{}", timestamp, timestamp),
            )
        preview = self.store.recover_stale_jobs(3600)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["selected"], ["stale-1"])
        self.assertEqual(self.store.list_jobs(status="running")["count"], 2)
        with self.assertRaises(ValueError):
            self.store.recover_stale_jobs(3600, dry_run=False)
        recovered = self.store.recover_stale_jobs(3600, dry_run=False, confirm=True)
        self.assertEqual(recovered["recovered"], ["stale-1"])
        job = self.store.list_jobs(stage="distill")["jobs"][0]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["metadata"]["stale_recovery"]["from_status"], "running")
        self.assertEqual(self.store.list_jobs(stage="index")["jobs"][0]["status"], "running")


if __name__ == "__main__":
    unittest.main()
