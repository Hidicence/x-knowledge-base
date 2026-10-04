#!/usr/bin/env python3
"""Verify MCP initialization, tool discovery and a real knowledge recall."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from runtime_config import runtime_env
from xkb_recall import validate_packet, recall_quality

SERVER = Path(__file__).resolve().parent / "xkb_recall_server.py"


def probe(query: str, *, env: dict | None = None, limit: int = 10) -> dict:
    """Use the real stdio server, not an import of its implementation."""
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "xkb-doctor", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "xkb_recall", "arguments": {"message": query, "limit": limit}}},
    ]
    child_env = dict(runtime_env() if env is None else env)
    child_env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    result = subprocess.run([sys.executable, "-u", str(SERVER)],
                            input="".join(json.dumps(r) + "\n" for r in requests),
                            text=True, encoding="utf-8", capture_output=True,
                            env=child_env, timeout=55)
    if result.returncode:
        raise RuntimeError(f"MCP process exited {result.returncode}")
    replies = {r["id"]: r for r in map(json.loads, result.stdout.splitlines()) if "id" in r}
    if set(replies) != {1, 2, 3} or any("error" in r for r in replies.values()):
        raise RuntimeError("MCP initialize/list/call did not all succeed")
    if replies[1]["result"].get("serverInfo", {}).get("name") != "xkb-recall":
        raise RuntimeError("unexpected MCP server identity")
    if "xkb_recall" not in [t["name"] for t in replies[2]["result"].get("tools", [])]:
        raise RuntimeError("MCP did not advertise xkb_recall")
    call = replies[3]["result"]
    for item in call.get("content", []):
        text = item.get("text", "")
        if text.startswith("[xkb_recall_meta] "):
            packet = json.loads(text[len("[xkb_recall_meta] "):])
            if call.get("isError") or packet.get("status") != "ok":
                raise RuntimeError(packet.get("error") or "MCP recall failed")
            return validate_packet(packet)
    raise RuntimeError("MCP recall omitted its structured metadata")


def assess(packet: dict, *, expect_ids: list[str] = (), require_semantic: bool = False,
           require_judge: bool = False) -> dict:
    ids = [str(r.get("id") or r.get("trace_id") or "") for r in packet["records"]]
    problems = []
    missing = sorted(set(expect_ids) - set(ids))
    if missing:
        problems.append("expected evidence missing: " + ", ".join(missing))
    semantic_used = packet.get("semantic_backend", {}).get("used", packet["retrieval_mode"] in {"xbrain_hybrid", "wiki_semantic"})
    if require_semantic and not semantic_used:
        problems.append("semantic retrieval was not used")
    judge = packet.get("judge", {"status": "not_attempted"})
    quality = recall_quality(packet)
    if require_judge and not quality["judge_complete"]:
        problems.append("relevance judge incomplete: " + judge.get("status", "not_attempted"))
    warnings = list(packet.get("warnings", []))
    degraded = quality["status"] == "degraded"
    if quality["warning"] and quality["warning"] not in warnings:
        warnings.append(quality["warning"])
    if packet["count"] == 0 and packet["retrieval_mode"] != "skipped":
        warnings.append("connection works, but this query returned no evidence; use --expect-id to verify a known item")
    return {"ok": not problems, "status": "failed" if problems else "degraded" if degraded else "ready",
            "checks": {"initialize": True, "tools_list": True,
            "tools_call": True}, "connection": packet.get("connection"),
            "retrieval_mode": packet["retrieval_mode"], "judge": judge, "backends": packet.get("backends", {}),
            "count": packet["count"], "record_ids": ids, "warnings": warnings, "quality": quality,
            "problems": problems,
            "scope": "fresh MCP subprocess; does not prove an already-open agent refreshed its tools"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="A question about a known item in your library")
    parser.add_argument("--expect-id", action="append", default=[])
    parser.add_argument("--require-semantic", action="store_true")
    parser.add_argument("--require-judge", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    try:
        report = assess(probe(args.query), expect_ids=args.expect_id,
                        require_semantic=args.require_semantic, require_judge=args.require_judge)
    except Exception as exc:
        report = {"ok": False, "status": "failed", "problems": [str(exc)]}
    report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    # ASCII escapes also work on Windows terminals without UTF-8 configured.
    print(json.dumps(report, ensure_ascii=True, indent=None if args.json else 2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    import xkb_usage
    xkb_usage.record(__file__)
    raise SystemExit(main())
