from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xkb_memory_service.py"
spec = importlib.util.spec_from_file_location("xkb_memory_service_http", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class XKBMemoryServiceHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = module.Store(Path(self.tmp.name) / "memory.sqlite")
        self.server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
        self.server.store = self.store
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

        session = self.store.open_session({"source": "http-fixture", "session_key": "http-session"})
        self.store.start_turn({"session_id": session["session_id"], "turn_id": "http-turn", "query": "http query"})
        self.completed = self.store.complete_turn(
            "http-turn",
            {"query": "http query", "answer": "user evidence", "content": {"answer": "user evidence"}},
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        request = Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_http_rejects_malformed_json_and_invalid_numeric_types(self) -> None:
        request = Request(self.base + "/v1/recall", data=b"{not-json", headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 400)
        self.assertIn("malformed JSON", json.loads(raised.exception.read())["error"])
        status, body = self.post("/v1/recall", {"query": "http query", "limit": "10"})
        self.assertEqual(status, 400)
        self.assertIn("limit must be an integer", body["error"])

    def test_http_turn_start_returns_400_for_identity_conflicts(self) -> None:
        session = self.store.open_session({"source": "http-fixture", "session_key": "identity-http", "namespace": "private"})
        payload = {"session_id": session["session_id"], "turn_id": "identity-http-turn", "query": "same query"}
        status, body = self.post("/v1/turns/start", payload)
        self.assertEqual(status, 200)
        self.assertFalse(body["resumed"])
        status, body = self.post("/v1/turns/start", {**payload, "query": "different query"})
        self.assertEqual(status, 400)
        self.assertIn("query", body["error"])
        status, body = self.post("/v1/turns/start", {**payload, "namespace": "shared"})
        self.assertEqual(status, 400)
        self.assertIn("namespace", body["error"])

    def test_http_namespace_must_match_session_and_completion(self) -> None:
        session = self.store.open_session({"source": "http-fixture", "session_key": "namespace-http", "namespace": "private"})
        status, body = self.post("/v1/turns/start", {"session_id": session["session_id"], "turn_id": "namespace-http-turn", "query": "q", "namespace": "shared"})
        self.assertEqual(status, 400)
        self.assertIn("namespace", body["error"])
        status, body = self.post("/v1/turns/start", {"session_id": session["session_id"], "turn_id": "namespace-http-turn", "query": "q"})
        self.assertEqual(status, 200)
        status, body = self.post("/v1/turns/namespace-http-turn/complete", {"query": "q", "answer": "a", "namespace": "shared"})
        self.assertEqual(status, 400)
        self.assertIn("namespace", body["error"])

    def test_run_endpoint_dry_run_then_lifecycle_without_promotion(self) -> None:
        job_id = self.completed["distillation_job_id"]
        status, dry = self.post("/v1/pipeline/jobs/run", {"job_ids": [job_id], "dry_run": True})
        self.assertEqual(status, 200)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["selected"], [job_id])
        self.assertEqual(self.store.list_jobs(stage="distill")["jobs"][0]["status"], "queued")

        status, result = self.post("/v1/pipeline/jobs/run", {"job_ids": [job_id]})
        self.assertEqual(status, 200)
        self.assertFalse(result["promotion_performed"])
        self.assertEqual(result["results"][0]["status"], "succeeded")
        self.assertEqual(result["results"][0]["decision"], "HOLD")
        self.assertEqual(self.store.list_jobs(stage="distill")["jobs"][0]["status"], "succeeded")
        candidate = self.store.query_candidates()["candidates"][0]
        self.assertEqual(candidate["status"], "pending")
        self.assertFalse(candidate["analysis"]["promotion_performed"])

    def test_run_endpoint_enforces_worker_allowlist_and_job_selection(self) -> None:
        status, body = self.post("/v1/pipeline/jobs/run", {"worker": "build_vector_index.py"})
        self.assertEqual(status, 400)
        self.assertIn("only xkb_l1_to_candidate", body["error"])
        self.assertEqual(self.store.list_jobs(stage="distill")["jobs"][0]["status"], "queued")

        status, body = self.post("/v1/pipeline/jobs/run", {"job_ids": ["not-a-real-job"]})
        self.assertEqual(status, 200)
        self.assertEqual(body["selected"], [])
        self.assertEqual(body["processed"], 0)
        self.assertEqual(self.store.list_jobs(stage="distill")["jobs"][0]["status"], "queued")

    def test_run_endpoint_rejects_malformed_job_ids(self) -> None:
        status, body = self.post("/v1/pipeline/jobs/run", {"job_ids": "not-a-list"})
        self.assertEqual(status, 400)
        self.assertIn("job_ids must be a list", body["error"])

    def test_stale_recovery_endpoint_requires_confirmation_for_mutation(self) -> None:
        job_id = self.completed["distillation_job_id"]
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='running',updated_at=? WHERE job_id=?", ("2026-01-01T00:00:00+00:00", job_id))
        status, preview = self.post("/v1/pipeline/jobs/recover-stale", {"older_than_seconds": 3600})
        self.assertEqual(status, 200)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["selected"], [job_id])
        status, error = self.post("/v1/pipeline/jobs/recover-stale", {"older_than_seconds": 3600, "dry_run": False})
        self.assertEqual(status, 400)
        self.assertIn("confirm=true", error["error"])
        status, recovered = self.post("/v1/pipeline/jobs/recover-stale", {"older_than_seconds": 3600, "dry_run": False, "confirm": True})
        self.assertEqual(status, 200)
        self.assertEqual(recovered["recovered"], [job_id])
        self.assertEqual(self.store.list_jobs(stage="distill")["jobs"][0]["status"], "queued")

    def test_http_recall_exposes_context_governance_packet(self) -> None:
        status, body = self.post("/v1/context", {"query": "http query", "namespace": "private"})
        self.assertEqual(status, 200)
        self.assertEqual(body["request_namespace"], "private")
        self.assertIn("acl_policy", body)
        self.assertIn("filtered_counts", body)
        self.assertIn("semantic_backend", body)
        self.assertIn("warnings", body)


class XKBMemoryServiceAuthTests(unittest.TestCase):
    """Once a token exists, identity must come from it and not from the body."""

    TOKEN = "test-token-0123456789abcdef"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = module.Store(Path(self.tmp.name) / "memory.sqlite")
        self.server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
        self.server.store = self.store
        self.server.auth = module.AuthPolicy({
            "tokens": {
                self.TOKEN: {"namespace": "team-a", "scopes": ["memory:read", "memory:write"]},
                "readonly-token-0123456789ab": {"namespace": "team-a", "scopes": ["memory:read"]},
            }
        })
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def post(self, path: str, payload: dict, token: str | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(self.base + path, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_health_stays_open_and_advertises_that_auth_is_on(self) -> None:
        with urlopen(self.base + "/v1/health", timeout=3) as response:
            body = json.loads(response.read())
        self.assertTrue(body["ok"])
        self.assertTrue(body["auth_required"])

    def test_anonymous_is_refused_once_a_token_is_configured(self) -> None:
        status, error = self.post("/v1/recall", {"query": "anything"})
        self.assertEqual(status, 401)
        self.assertIn("token", error["error"])

    def test_invalid_token_is_refused(self) -> None:
        status, _ = self.post("/v1/recall", {"query": "anything"}, token="wrong-token-0123456789abcd")
        self.assertEqual(status, 401)

    def test_token_namespace_cannot_be_overridden_by_the_body(self) -> None:
        """The whole point: claiming another namespace must fail, not silently work."""
        status, error = self.post("/v1/recall", {"query": "anything", "namespace": "team-b"}, token=self.TOKEN)
        self.assertEqual(status, 403)
        status, body = self.post("/v1/recall", {"query": "anything"}, token=self.TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body["request_namespace"], "team-a")

    def test_scopes_separate_read_from_write(self) -> None:
        status, _ = self.post("/v1/sessions/open", {"source": "x", "session_key": "k"}, token="readonly-token-0123456789ab")
        self.assertEqual(status, 403)
        status, _ = self.post("/v1/recall", {"query": "anything"}, token="readonly-token-0123456789ab")
        self.assertEqual(status, 200)

    def test_written_session_is_pinned_to_the_token_namespace(self) -> None:
        status, session = self.post("/v1/sessions/open", {"source": "x", "session_key": "k", "namespace": "team-b"}, token=self.TOKEN)
        self.assertEqual(status, 403)
        status, session = self.post("/v1/sessions/open", {"source": "x", "session_key": "k"}, token=self.TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(session["namespace"], "team-a")


class AuthPolicyTests(unittest.TestCase):
    def test_anonymous_only_while_no_token_exists(self) -> None:
        self.assertTrue(module.AuthPolicy({}).allow_anonymous)
        policy = module.AuthPolicy({"tokens": {"a-long-enough-token-value": {"namespace": "n"}}})
        self.assertFalse(policy.allow_anonymous)
        self.assertTrue(policy.enabled)

    def test_config_errors_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            module.AuthPolicy({"tokens": {"short": {"namespace": "n"}}})
        with self.assertRaises(ValueError):
            module.AuthPolicy({"tokens": {"a-long-enough-token-value": {}}})

    def test_unreadable_policy_file_is_an_error_not_open_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "auth.json"
            bad.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                module.AuthPolicy.load(bad)
            # A missing file is the documented "no auth configured" default.
            self.assertFalse(module.AuthPolicy.load(Path(tmp) / "absent.json").enabled)


if __name__ == "__main__":
    unittest.main()
