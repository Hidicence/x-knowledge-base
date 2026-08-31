"""一次對話的內容，不能被別的 namespace 讀走。

`/v1/artifacts/{trace_id}` 是唯一一個沒有過 namespace 的讀取端點。其他每一條
——cards、wiki topics、sources、evidence、recall——都會把呼叫者的 namespace
帶進去過濾；這一條只拿 trace_id，然後回傳整個 turn：問題、回答、擷取到的內容。

而 trace_id 不是祕密：complete_turn 與 recall 都會主動把它交給客戶端。所以一個
被釘在 team-b 的 token，只要記得看過的 id，就讀得到 team-a 的對話。

讀不到與不存在要回同一個答案，否則 trace_id 就成了一個可以拿來探測
「這筆存不存在」的工具。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from xkb_memory_service import Store


class ArtifactNamespaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "memory.sqlite")

    def _turn_in(self, namespace: str) -> str:
        session = self.store.open_session({
            "source": "fixture",
            "agent_id": "fixture",
            "namespace": namespace,
            "session_key": f"session-{namespace}",
        })
        started = self.store.start_turn({
            "session_id": session["session_id"],
            "query": "碳盤查的資料要怎麼管理",
        })
        done = self.store.complete_turn(started["turn_id"], {
            "query": "碳盤查的資料要怎麼管理",
            "answer": "來源、版本、審核者、計算方法都要留下。",
            "status": "succeeded",
        })
        return done["trace_id"]

    def test_the_owning_namespace_can_read_it(self) -> None:
        trace = self._turn_in("team-a")
        self.assertIsNotNone(self.store.artifact(trace, "team-a"))

    def test_another_namespace_cannot(self) -> None:
        trace = self._turn_in("team-a")
        self.assertIsNone(
            self.store.artifact(trace, "team-b"),
            "trace_id 是發給客戶端的，不能拿來讀別人的對話",
        )

    def test_a_refusal_looks_like_a_miss(self) -> None:
        """否則 trace_id 就成了「這筆存不存在」的探測工具。"""
        trace = self._turn_in("team-a")
        self.assertEqual(
            self.store.artifact(trace, "team-b"),
            self.store.artifact("trace-that-does-not-exist", "team-b"),
        )


if __name__ == "__main__":
    unittest.main()
