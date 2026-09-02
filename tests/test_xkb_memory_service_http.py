from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
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

_wiki_patch = None


def setUpModule() -> None:
    """同 test_xkb_memory_service：不要讓測試載入真實知識庫或打 embedding API。"""
    global _wiki_patch
    from unittest import mock
    _wiki_patch = mock.patch.object(module.KnowledgeCatalog, "_wiki_search", return_value=[])
    _wiki_patch.start()


def tearDownModule() -> None:
    if _wiki_patch is not None:
        _wiki_patch.stop()


class XKBMemoryServiceHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        # 這是 HTTP 契約測試，不是語意後端測試。機器上沒有 gbrain / bun 時，
        # 每個請求會卡滿子行程的 timeout 才靜靜回空陣列——原本那是 30 秒，
        # 於是整個檔案看起來像「HTTP 服務壞了」。
        self._timeout_patch = mock.patch.dict(os.environ, {"XKB_EMBEDDING_TIMEOUT": "1", "XKB_XBRAIN_TIMEOUT": "1"})
        self._timeout_patch.start()
        self.addCleanup(self._timeout_patch.stop)
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
        # join 不設上限。原本 timeout=2，時間到就往下刪暫存目錄——如果那條
        # 執行緒還在處理請求，它用的 sqlite 檔會在腳下被移走，同一個類別的
        # 下一個測試就對著一個還在關閉的伺服器跑。這就是它在 discover 底下
        # 偶爾出錯、單獨跑卻永遠通過的原因。shutdown() 已經呼叫過，join 會回來。
        self.thread.join()
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
        # 這是 HTTP 契約測試，不是語意後端測試。機器上沒有 gbrain / bun 時，
        # 每個請求會卡滿子行程的 timeout 才靜靜回空陣列——原本那是 30 秒，
        # 於是整個檔案看起來像「HTTP 服務壞了」。
        self._timeout_patch = mock.patch.dict(os.environ, {"XKB_EMBEDDING_TIMEOUT": "1", "XKB_XBRAIN_TIMEOUT": "1"})
        self._timeout_patch.start()
        self.addCleanup(self._timeout_patch.stop)
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
        # join 不設上限。原本 timeout=2，時間到就往下刪暫存目錄——如果那條
        # 執行緒還在處理請求，它用的 sqlite 檔會在腳下被移走，同一個類別的
        # 下一個測試就對著一個還在關閉的伺服器跑。這就是它在 discover 底下
        # 偶爾出錯、單獨跑卻永遠通過的原因。shutdown() 已經呼叫過，join 會回來。
        self.thread.join()
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
