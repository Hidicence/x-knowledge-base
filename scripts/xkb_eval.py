#!/usr/bin/env python3
"""Run labelled recall cases through the real MCP transport.

The default suite is isolated, synthetic and offline. --live requires a custom
case file and uses the current library/providers; it may incur provider costs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
import time

from xkb_doctor import assess, probe

DEFAULT_CASES = Path(__file__).resolve().parent.parent / "evals" / "recall-cases.json"


def fixture_env(root: Path, fixtures: dict) -> dict[str, str]:
    # Do not inherit credentials, runtime files, paths or remote endpoints.
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
        "TEMP", "TMP", "PATH", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}}
    data = root / "data"
    index = data / "bookmarks" / "search_index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"items": fixtures.get("cards", [])}, ensure_ascii=True), encoding="utf-8")
    wiki = data / "x-knowledge-base" / "wiki" / "topics"
    wiki.mkdir(parents=True)
    for item in fixtures.get("wiki", []):
        if not item["id"].replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid fixture wiki id")
        (wiki / (item["id"] + ".md")).write_text(item["content"], encoding="utf-8")
    env.update({"HOME": str(root), "USERPROFILE": str(root), "XKB_DATA_DIR": str(data),
                "XKB_CONFIG": str(root / "absent.json"),
                "XKB_SERVICE_DB": str(root / "knowledge.sqlite"),
                "XKB_EMBEDDING_TIMEOUT": "1", "XKB_XBRAIN_TIMEOUT": "1"})
    return env


def score_case(case: dict, packet: dict) -> dict:
    actual = [str(r.get("id") or r.get("trace_id") or "") for r in packet["records"]]
    expected = set(case["expected_ids"])
    allowed = set(case.get("allowed_ids", case["expected_ids"]))
    missing = sorted(expected - set(actual))
    unexpected = sorted(set(actual) - allowed)
    errors = []
    if missing:
        errors.append("missing: " + ", ".join(missing))
    if unexpected:
        errors.append("unexpected: " + ", ".join(unexpected))
    if case.get("retrieval_mode") and packet["retrieval_mode"] != case["retrieval_mode"]:
        errors.append("wrong retrieval mode")
    if len(actual) != len(set(actual)):
        errors.append("duplicate evidence")
    return {"id": case["id"], "ok": not errors, "errors": errors,
            "record_ids": actual,
            "recall_at_k": len(expected & set(actual)) / len(expected) if expected else None,
            "precision_at_k": len(allowed & set(actual)) / len(actual) if actual else None,
            "no_answer": not expected, "false_positive_count": len(unexpected),
            "retrieval_mode": packet["retrieval_mode"],
            "judge_status": packet.get("judge", {}).get("status", "not_attempted")}


def run_cases(cases: list[dict], env: dict | None = None, *, require_semantic: bool = False,
              require_judge: bool = False) -> dict:
    rows = []
    for case in cases:
        started = time.monotonic()
        try:
            # A case namespace overrides only for this child, not future cases.
            from runtime_config import runtime_env
            child_env = dict(runtime_env() if env is None else env)
            if "namespace" in case:
                child_env["XKB_NAMESPACE"] = case["namespace"]
            packet = probe(case["query"], env=child_env, limit=case.get("limit", 10))
            row = score_case(case, packet)
            readiness = assess(packet, require_semantic=require_semantic, require_judge=require_judge)
            row["errors"].extend(readiness["problems"])
            row["ok"] = not row["errors"]
        except Exception as exc:
            row = {"id": case["id"], "ok": False, "errors": [str(exc)],
                   "retrieval_mode": "failed", "judge_status": "not_attempted",
                   "recall_at_k": 0.0 if case["expected_ids"] else None,
                   "precision_at_k": None, "no_answer": not case["expected_ids"]}
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        rows.append(row)
    latencies = sorted(r["elapsed_ms"] for r in rows)
    recalls = [r["recall_at_k"] for r in rows if r.get("recall_at_k") is not None]
    precisions = [r["precision_at_k"] for r in rows if r.get("precision_at_k") is not None]
    negatives = [r for r in rows if r.get("no_answer")]
    return {"ok": all(r["ok"] for r in rows), "cases": len(rows),
            "passed": sum(r["ok"] for r in rows),
            "mean_recall_at_k": statistics.mean(recalls) if recalls else None,
            "mean_precision_at_k": statistics.mean(precisions) if precisions else None,
            "no_answer_passed": sum(r["ok"] for r in negatives), "no_answer_cases": len(negatives),
            "latency_ms": {"p50": statistics.median(latencies),
                           "p95": latencies[math.ceil(len(latencies) * .95) - 1]},
            "retrieval_modes": dict(Counter(r["retrieval_mode"] for r in rows)),
            "judge_statuses": dict(Counter(r["judge_status"] for r in rows)), "results": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--require-semantic", action="store_true")
    parser.add_argument("--require-judge", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.live and args.cases is None:
        parser.error("--live requires --cases with labels for your own library")
    try:
        suite = json.loads((args.cases or DEFAULT_CASES).read_text(encoding="utf-8"))
        cases = suite["cases"]
        if (not isinstance(cases, list) or not cases
                or len({c["id"] for c in cases}) != len(cases)):
            raise ValueError("cases must be a non-empty list with unique ids")
        for case in cases:
            if (not isinstance(case["query"], str) or not case["query"].strip()
                    or not isinstance(case["expected_ids"], list)
                    or not all(isinstance(x, str) for x in case["expected_ids"])):
                raise ValueError("each case needs a query and expected_ids list")
        if args.live:
            report = run_cases(cases, require_semantic=args.require_semantic, require_judge=args.require_judge)
        else:
            if "fixtures" not in suite:
                raise ValueError("offline mode needs fixtures; use --live for an existing library")
            with tempfile.TemporaryDirectory(prefix="xkb-eval-") as tmp:
                report = run_cases(cases, fixture_env(Path(tmp), suite["fixtures"]),
                                   require_semantic=args.require_semantic, require_judge=args.require_judge)
        report["scope"] = "live library" if args.live else "synthetic offline keyword/ACL/transport regression; not a semantic quality benchmark"
    except Exception as exc:
        report = {"ok": False, "error": str(exc)}
    output = json.dumps(report, ensure_ascii=True, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    import xkb_usage
    xkb_usage.record(__file__)
    raise SystemExit(main())
