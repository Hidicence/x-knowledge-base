from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xkb_l1_to_candidate.py"
spec = importlib.util.spec_from_file_location("xkb_l1_to_candidate", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Store = module.Store


class L1ToCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db.sqlite")
        session = self.store.open_session({"source": "fixture", "session_key": "s"})
        self.turn = self.store.start_turn({"session_id": session["session_id"], "turn_id": "t", "query": "q"})

    def tearDown(self):
        self.tmp.cleanup()

    def complete(self, answer):
        return self.store.complete_turn("t", {"query": "q", "answer": answer, "content": {"answer": answer}})

    def test_filters_system_noise_and_preserves_provenance(self):
        completed = self.complete("SELF_REVIEW_SENT cron jobs executed")
        result = module.process_job(self.store, completed["distillation_job_id"])
        self.assertEqual(result["decision"], "REJECT_SYSTEM_ECHO")
        candidate = self.store.query_candidates()["candidates"][0]
        self.assertEqual(candidate["status"], "rejected")
        self.assertEqual(candidate["analysis"]["source_trace_ids"], [completed["trace_id"]])
        self.assertFalse(candidate["analysis"]["llm_used"])
        job = self.store.list_jobs(stage="distill")["jobs"][0]
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["metadata"]["candidate_ids"], [completed["candidate_id"]])

    def test_rerun_is_idempotent_and_non_promoting(self):
        completed = self.complete("A useful user preference")
        job_id = completed["distillation_job_id"]
        first = module.process_job(self.store, job_id)
        second = module.process_job(self.store, job_id)
        self.assertEqual(first["analysis"]["analysis_fingerprint"], second["analysis"]["analysis_fingerprint"])
        candidate = self.store.query_candidates()["candidates"][0]
        self.assertEqual(candidate["status"], "pending")
        self.assertEqual(candidate["analysis"]["decision"], "HOLD")
        self.assertFalse(candidate["analysis"]["promotion_performed"])

    def test_missing_input_is_recorded_as_retryable_failure(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO jobs(job_id,stage,worker,status,input_ref,output_ref,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("bad", "distill", module.WORKER, "queued", "missing", "missing", module.now(), module.now()))
        try:
            module.process_job(self.store, "bad")
        except Exception as exc:
            result = module.fail_job(self.store, "bad", exc)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.store.list_jobs(stage="distill")["jobs"][0]["status"], "failed")
        self.assertTrue(self.store.list_jobs(stage="distill")["jobs"][0]["retryable"])


if __name__ == "__main__":
    unittest.main()
