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
from xkb_evidence import identity_key, record_id

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
    surface = case.get("surface", "records")
    if surface not in {"records", "delivery"}:
        raise ValueError("surface must be records or delivery")
    if surface == "delivery" and not isinstance(packet.get("delivery"), dict):
        raise ValueError("service did not return a delivery decision")
    records = packet["delivery"]["records"] if surface == "delivery" else packet["records"]
    record_ids = [record_id(r) for r in records]
    evidence_keys = [r.get("evidence_key") or identity_key(r) for r in records]
    label_unit = case.get("label_unit", "document")
    if label_unit not in {"document", "evidence"}:
        raise ValueError("label_unit must be document or evidence")
    actual = set(evidence_keys if label_unit == "evidence" else record_ids)
    expected = set(case["expected_ids"])
    alternatives = set(case.get("expected_any_ids", []))
    allowed = set(case.get("allowed_ids", expected | alternatives))
    missing = sorted(expected - actual)
    unexpected = sorted(actual - allowed)
    errors = []
    if surface == "delivery" and packet["delivery"].get("status") != "ready":
        errors.append("proactive delivery judgement incomplete")
    if len(records) > case.get("max_delivered", len(records)):
        errors.append("delivery budget exceeded")
    if missing:
        errors.append("missing: " + ", ".join(missing))
    if alternatives and not alternatives.intersection(actual):
        errors.append("missed required evidence group")
    if unexpected:
        errors.append("unexpected: " + ", ".join(unexpected))
    if case.get("expected_delivery") == "none" and records:
        errors.append("unnecessary interruption")
    if case.get("expected_delivery") == "evidence" and not records:
        errors.append("missed conversational need")
    if case.get("retrieval_mode") and packet["retrieval_mode"] != case["retrieval_mode"]:
        errors.append("wrong retrieval mode")
    identified = [key for key in evidence_keys if key]
    if len(identified) != len(set(identified)):
        errors.append("duplicate evidence")
    if len(identified) != len(evidence_keys):
        errors.append("unidentified evidence")
    return {"id": case["id"], "ok": not errors, "errors": errors, "surface": surface,
            "record_ids": record_ids, "evidence_keys": evidence_keys, "label_unit": label_unit,
            "recall_at_k": ((len(expected & actual) + bool(alternatives & actual)) /
                            (len(expected) + bool(alternatives))) if expected or alternatives else None,
            "precision_at_k": len(allowed & actual) / len(actual) if actual else None,
            "no_answer": not expected and not alternatives, "false_positive_count": len(unexpected),
            "retrieval_mode": packet["retrieval_mode"],
            "judge_status": packet.get("judge", {}).get("status", "not_attempted"),
            "expected_delivery": case.get("expected_delivery"),
            "need": case.get("need", ""), "conversation_messages": packet.get("conversation_messages", 0)}


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
            packet = probe(case["query"], env=child_env, limit=case.get("limit", 10),
                           **({"conversation": case["conversation"]} if "conversation" in case else {}))
            row = score_case(case, packet)
            readiness = assess(packet, require_semantic=require_semantic, require_judge=require_judge)
            row["errors"].extend(readiness["problems"])
            row["ok"] = not row["errors"]
        except Exception as exc:
            row = {"id": case["id"], "ok": False, "errors": [str(exc)],
                   "retrieval_mode": "failed", "judge_status": "not_attempted",
                   "recall_at_k": 0.0 if case["expected_ids"] or case.get("expected_any_ids") else None,
                   "precision_at_k": None, "no_answer": not case["expected_ids"] and not case.get("expected_any_ids")}
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        rows.append(row)
    return summarize(rows)


def summarize(rows: list[dict]) -> dict:
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


def write_receipt(path: Path, payload: dict) -> None:
    """Replace a completed checkpoint atomically; interrupted calls stay pending."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        pending = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    try:
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def run_intervention_cases(cases: list[dict], evidence: dict, output: Path) -> dict:
    """Explicit paid decision evaluation, with no retrieval or production writes.

    Checkpoint before and after every call. An interrupted run remains pending;
    callers must inspect it and choose a new output instead of blindly repeating.
    """
    import xkb_delivery
    from xkb_recall import conversation_messages
    rows = []
    def checkpoint(pending=None):
        write_receipt(output, {"status": "running", "pending_case": pending,
                               "cases": cases, "evidence": evidence, "results": rows})
    # Exclusive creation avoids races and accidental paid reruns.
    with output.open("x", encoding="utf-8") as stream:
        json.dump({"status": "pending", "cases": cases, "evidence": evidence}, stream, ensure_ascii=False)
    for case in cases:
        checkpoint(case["id"])
        started = time.monotonic()
        recent = conversation_messages(case.get("conversation"))
        packet = {"query": case["query"], "effective_conversation": recent,
                  "records": [{"id": key, "summary": evidence[key], "judge": .9,
                               "namespace": "fixture"} for key in case["candidate_ids"]],
                  "judge": {"status": "judged"}, "retrieval_mode": "fixture"}
        packet["delivery"] = xkb_delivery.select(packet["records"], packet["judge"],
                                                recent, query=case["query"])
        row = score_case({**case, "surface": "delivery"}, packet)
        row.update(elapsed_ms=round((time.monotonic()-started)*1000), packet=packet)
        rows.append(row)
        checkpoint()
    return {**summarize(rows), "status": "completed", "input": {"cases": cases, "evidence": evidence}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--intervention", action="store_true", help="Judge fixed evidence; requires --live and a new --output receipt")
    parser.add_argument("--require-semantic", action="store_true")
    parser.add_argument("--require-judge", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.live and args.cases is None:
        parser.error("--live requires --cases with labels for your own library")
    if args.intervention and (not args.live or args.output is None):
        parser.error("--intervention requires --live and --output; provider calls incur cost")
    if args.intervention and (args.require_semantic or args.require_judge):
        parser.error("--require-semantic/--require-judge apply to retrieval, not fixed-evidence intervention evaluation")
    if args.intervention and args.output.exists():
        parser.error("intervention output already exists; inspect the previous run before choosing a new receipt")
    write_output = not args.intervention
    try:
        suite = json.loads((args.cases or DEFAULT_CASES).read_text(encoding="utf-8"))
        cases = suite["cases"]
        if (not isinstance(cases, list) or not cases
                or len({c["id"] for c in cases}) != len(cases)):
            raise ValueError("cases must be a non-empty list with unique ids")
        for case in cases:
            if args.intervention:
                case.setdefault("expected_ids", [])
                case.setdefault("expected_delivery", "evidence" if case["expected_ids"] or case.get("expected_any_ids") else "none")
            if (not isinstance(case["query"], str) or not case["query"].strip()
                    or not isinstance(case["expected_ids"], list)
                    or not all(isinstance(x, str) for x in case["expected_ids"])):
                raise ValueError("each case needs a query and expected_ids list")
            from xkb_recall import conversation_messages
            if "conversation" in case:
                conversation_messages(case["conversation"])
            if case.get("expected_delivery") not in {None, "none", "evidence"}:
                raise ValueError("expected_delivery must be none or evidence")
            alternatives = case.get("expected_any_ids", [])
            if not isinstance(alternatives, list) or not all(isinstance(x, str) for x in alternatives):
                raise ValueError("expected_any_ids must be a list of equivalent evidence ids")
            if case.get("label_unit", "document") not in {"document", "evidence"}:
                raise ValueError("label_unit must be document or evidence")
            allowed = case.get("allowed_ids", case["expected_ids"] + alternatives)
            if (not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed)
                    or not set(case["expected_ids"] + alternatives).issubset(allowed)):
                raise ValueError("allowed_ids must be a list containing all expected_ids")
            if ("max_delivered" in case and (type(case["max_delivered"]) is not int or case["max_delivered"] < 0)):
                raise ValueError("max_delivered must be a non-negative integer")
        if args.intervention:
            evidence = suite.get("evidence")
            if not isinstance(evidence, dict) or not evidence or not all(isinstance(v, str) and v.strip() for v in evidence.values()):
                raise ValueError("intervention suite requires a non-empty evidence text map")
            for case in cases:
                candidates = case.get("candidate_ids")
                if (not isinstance(candidates, list) or not candidates or len(set(candidates)) != len(candidates)
                        or any(key not in evidence for key in candidates)
                        or not set(case["expected_ids"] + case.get("expected_any_ids", [])).issubset(candidates)):
                    raise ValueError("candidate_ids must uniquely identify evidence and contain the expected labels")
            report = run_intervention_cases(cases, evidence, args.output)
            write_output = True
        elif args.live:
            report = run_cases(cases, require_semantic=args.require_semantic, require_judge=args.require_judge)
        else:
            if "fixtures" not in suite:
                raise ValueError("offline mode needs fixtures; use --live for an existing library")
            with tempfile.TemporaryDirectory(prefix="xkb-eval-") as tmp:
                report = run_cases(cases, fixture_env(Path(tmp), suite["fixtures"]),
                                   require_semantic=args.require_semantic, require_judge=args.require_judge)
        report["scope"] = ("live intervention judge with synthetic fixed evidence; not retrieval or downstream answer quality"
                           if args.intervention else "live library" if args.live else
                           "synthetic offline keyword/ACL/transport regression; not a semantic quality benchmark")
    except Exception as exc:
        report = {"ok": False, "error": str(exc)}
    output = json.dumps(report, ensure_ascii=True, indent=2)
    if args.output and write_output:
        if args.intervention:
            write_receipt(args.output, report)
        else:
            args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    import xkb_usage
    xkb_usage.record(__file__)
    raise SystemExit(main())
