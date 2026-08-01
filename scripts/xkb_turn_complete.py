#!/usr/bin/env python3
"""Safe XKB turn-complete adapter.

Consumes one completed turn payload and writes an append-only L1 trace artifact
under an explicit runtime directory. It is intentionally not wired into
OpenClaw itself yet: this proves the contract and idempotency before production
hook integration.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from xkb_l1_trace_capture import capture_trace  # noqa: E402


def turn_to_trace_payload(turn: dict[str, Any]) -> dict[str, Any]:
    """Map a completed turn envelope to the stable L1 capture contract."""
    session_id = str(turn.get("session_id") or "").strip()
    episode_id = str(turn.get("episode_id") or "").strip()
    if not session_id and not episode_id:
        raise ValueError("completed turn requires session_id or episode_id")

    content = turn.get("content")
    if content is None:
        # Keep the raw completed turn available for later distillation. This is
        # safer than inventing a summary or silently dropping tool results.
        content = {
            "user": turn.get("user_message"),
            "assistant": turn.get("assistant_message"),
            "tool_calls": turn.get("tool_calls", []),
            "tool_results": turn.get("tool_results", []),
            "artifacts": turn.get("artifacts", []),
            "status": turn.get("status", "completed"),
        }

    raw_refs = list(turn.get("raw_source_refs") or [])
    turn_id = str(turn.get("turn_id") or "").strip()
    if turn_id and turn_id not in raw_refs:
        raw_refs.insert(0, f"turn:{turn_id}")

    return {
        "episode_id": episode_id,
        "session_id": session_id,
        "agent_id": turn.get("agent_id", "openclaw"),
        "namespace": turn.get("namespace", "pan-private"),
        "source_type": "conversation",
        "source_id": turn_id or None,
        "content": content,
        "raw_source_refs": raw_refs,
        "observed_at": turn.get("completed_at") or turn.get("observed_at"),
        "ttl": turn.get("ttl"),
        "metadata": {
            "app_id": turn.get("app_id"),
            "adapter_id": turn.get("adapter_id"),
            "status": turn.get("status", "completed"),
            "usage": turn.get("usage"),
        },
    }


def write_turn_trace(turn: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    trace = capture_trace(turn_to_trace_payload(turn))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{trace['trace_id'].replace(':', '-')}.json"
    encoded = json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != encoded:
            raise RuntimeError(f"trace id collision with different content: {path}")
        created = False
    else:
        path.write_text(encoded, encoding="utf-8")
        created = True
    return {"trace_id": trace["trace_id"], "path": str(path), "created": created, "read_only_production": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a completed turn as an XKB L1 artifact")
    parser.add_argument("input", type=Path, help="JSON turn envelope, or - for stdin")
    parser.add_argument("--out-dir", type=Path, required=True, help="Isolated runtime artifact directory")
    args = parser.parse_args()
    raw = sys.stdin.read() if str(args.input) == "-" else args.input.read_text(encoding="utf-8")
    result = write_turn_trace(json.loads(raw), args.out_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
