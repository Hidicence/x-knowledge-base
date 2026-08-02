#!/usr/bin/env python3
"""Capture one conversation/work trace as a read-only L1 artifact.

This is the smallest Stage 3 bridge: callers must provide the episode/session
identity; the tool never derives identity from filenames and never promotes or
writes production memory. The output is an appendable JSON artifact suitable
for later summary/candidate jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "xkb-l1-trace.v1"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def capture_trace(payload: dict[str, Any], *, captured_at: str | None = None) -> dict[str, Any]:
    """Return a deterministic, provenance-rich L1 trace.

    Required identity is deliberately explicit. A caller may use either an
    episode_id or session_id, but at least one must be present.
    """
    episode_id = _text(payload.get("episode_id")).strip()
    session_id = _text(payload.get("session_id")).strip()
    if not episode_id and not session_id:
        raise ValueError("episode_id or session_id is required; identity is never inferred")

    source_type = payload.get("source_type", "conversation")
    if source_type not in {"conversation", "tool_run", "external_source"}:
        raise ValueError("source_type must be conversation, tool_run, or external_source")

    observed_at = _text(payload.get("observed_at")).strip() or (
        captured_at or datetime.now(timezone.utc).isoformat()
    )
    content = _text(payload.get("content")).strip()
    if not content:
        raise ValueError("content is required")

    raw_refs = payload.get("raw_source_refs", [])
    if not isinstance(raw_refs, list) or not all(isinstance(x, str) and x for x in raw_refs):
        raise ValueError("raw_source_refs must be a list of non-empty strings")

    identity = {
        "episode_id": episode_id or None,
        "session_id": session_id or None,
        "agent_id": _text(payload.get("agent_id")).strip() or "unknown",
        # Must match the service's ACL default ("private"): a trace written
        # under a different default would never satisfy the namespace check.
        "namespace": _text(payload.get("namespace")).strip() or "private",
    }
    stable_input = {
        "identity": identity,
        "source_type": source_type,
        "source_id": _text(payload.get("source_id")).strip() or None,
        "content": content,
        "raw_source_refs": raw_refs,
        "observed_at": observed_at,
    }
    trace_hash = hashlib.sha256(
        json.dumps(stable_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return {
        "schema": SCHEMA,
        "trace_id": f"trace:{trace_hash[:24]}",
        "memory_layer": "L1",
        "status": "observed",
        "source_type": source_type,
        "source_id": stable_input["source_id"],
        "agent_id": identity["agent_id"],
        "namespace": identity["namespace"],
        "episode_id": identity["episode_id"],
        "session_id": identity["session_id"],
        "content": content,
        "raw_source_refs": raw_refs,
        "observed_at": observed_at,
        "ttl": payload.get("ttl"),
        "metadata": payload.get("metadata", {}),
        "provenance_sha256": trace_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a read-only XKB L1 trace artifact")
    parser.add_argument("input", type=Path, help="JSON input file, or - for stdin")
    parser.add_argument("--out", type=Path, required=True, help="Output JSON artifact path")
    args = parser.parse_args()
    raw = sys.stdin.read() if str(args.input) == "-" else args.input.read_text(encoding="utf-8")
    trace = capture_trace(json.loads(raw))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"trace_id": trace["trace_id"], "out": str(args.out), "read_only": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
