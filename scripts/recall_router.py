#!/usr/bin/env python3
"""
Recall Router — Active Recall Layer Phase 1

統一入口：使用者訊息 → 分類 → routing → 執行 → 格式化輸出

Usage:
  python3 recall_router.py "XKB 下一步是什麼"
  python3 recall_router.py "AI SEO 值不值得做" --format side_hint
  python3 recall_router.py "query" --json
  python3 recall_router.py "query" --dry-run   # 只顯示 routing 決策，不執行 recall

整合方式：
  # Claude Code UserPromptSubmit hook → stdout 作為 additionalContext
  # OpenClaw MCP tool → 直接呼叫此腳本
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Add scripts dir to path for sibling imports
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from conversation_state_parser import parse as parse_state, ParseResult
from continuity_recall import recall as continuity_recall, format_chat as format_continuity_chat

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
SCRIPTS = WORKSPACE / "skills" / "x-knowledge-base" / "scripts"

# ── Value filter ──────────────────────────────────────────────────────────────
MIN_SCORE_HARD = 0.3
MIN_SCORE_SOFT = 0.4
MIN_EXCERPT_LEN = 15


def _filter_results(results: list, min_score: float) -> list:
    return [
        r for r in results
        if r.score >= min_score and len(getattr(r, "excerpt", "") or "") >= MIN_EXCERPT_LEN
    ]


# ── Associative recall via recall_for_conversation.py ────────────────────────

def run_associative_recall(query: str, limit: int = 2) -> str:
    """Call recall_for_conversation.py as subprocess, return --format chat output."""
    script = SCRIPTS / "recall_for_conversation.py"
    if not script.exists():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(script), query, "--format", "chat", "--limit", str(limit)],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "OPENCLAW_WORKSPACE": str(WORKSPACE)},
        )
        return result.stdout.strip()
    except Exception as e:
        return f"（associative recall error: {e}）"


# ── Delivery formatters ───────────────────────────────────────────────────────

def deliver_inline_injection(continuity_text: str) -> str:
    """Hard trigger: inline injection."""
    if not continuity_text:
        return ""
    return continuity_text


def deliver_side_hint(associative_text: str) -> str:
    """Soft trigger with high-score results: side hint."""
    if not associative_text:
        return ""
    return associative_text


def deliver_expandable_hint(query: str, count: int) -> str:
    """Soft trigger with lower-score results."""
    return f"（你知識庫裡有 {count} 個相關片段，主題：{query[:30]}，要我拉進來嗎？）"


# ── Router ─────────────────────────────────────────────────────────────────────

def route(message: str, dry_run: bool = False) -> dict:
    """
    Main routing logic.
    Returns dict: {trigger_class, state, query, delivery_mode, output}
    """
    # Step 1: Parse conversation state
    parsed: ParseResult = parse_state(message)

    if parsed.trigger_class == "suppress":
        return {
            "trigger_class": "suppress",
            "state": parsed.state,
            "query": "",
            "delivery_mode": "none",
            "output": "",
            "debug": {"confidence": parsed.confidence, "rules": parsed.matched_rules},
        }

    query = parsed.suggested_query or message[:60]

    if dry_run:
        return {
            "trigger_class": parsed.trigger_class,
            "state": parsed.state,
            "query": query,
            "delivery_mode": "TBD (dry-run)",
            "output": "[dry-run: recall not executed]",
            "debug": {"confidence": parsed.confidence, "rules": parsed.matched_rules},
        }

    # Step 2: Execute recall based on trigger class
    if parsed.trigger_class == "hard":
        # Continuity recall: MEMORY.md + wiki/topics
        from continuity_recall import recall as _cont_recall, format_chat as _fmt
        raw_results = _cont_recall(query, source="both", top_k=4)
        filtered = _filter_results(raw_results, MIN_SCORE_HARD)

        if not filtered:
            return {
                "trigger_class": "hard",
                "state": parsed.state,
                "query": query,
                "delivery_mode": "none",
                "output": "",
                "debug": {"confidence": parsed.confidence, "total_candidates": len(raw_results)},
            }

        output = deliver_inline_injection(_fmt(filtered[:3]))
        return {
            "trigger_class": "hard",
            "state": parsed.state,
            "query": query,
            "delivery_mode": "inline_injection",
            "output": output,
            "debug": {
                "confidence": parsed.confidence,
                "results_count": len(filtered),
                "top_score": filtered[0].score if filtered else 0,
            },
        }

    else:  # soft trigger
        # Associative recall: cards + bookmarks via recall_for_conversation.py
        assoc_output = run_associative_recall(query, limit=2)

        if not assoc_output or len(assoc_output) < 20:
            return {
                "trigger_class": "soft",
                "state": parsed.state,
                "query": query,
                "delivery_mode": "none",
                "output": "",
                "debug": {"confidence": parsed.confidence, "reason": "no results"},
            }

        # Determine delivery mode based on confidence
        if parsed.confidence >= 0.6:
            output = deliver_side_hint(assoc_output)
            mode = "side_hint"
        else:
            # Count results (rough: count bullet points)
            count = assoc_output.count("- ") or 1
            output = deliver_expandable_hint(query, count)
            mode = "expandable_hint"

        return {
            "trigger_class": "soft",
            "state": parsed.state,
            "query": query,
            "delivery_mode": mode,
            "output": output,
            "debug": {"confidence": parsed.confidence},
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recall Router — Active Recall Layer entry point"
    )
    parser.add_argument("message", nargs="?", help="User message")
    parser.add_argument("--json", action="store_true", help="Output full routing result as JSON")
    parser.add_argument("--dry-run", action="store_true", help="Show routing decision without executing recall")
    parser.add_argument("--format", choices=["chat", "full"], default="chat",
                        help="Output format when not using --json")
    args = parser.parse_args()

    message = args.message or sys.stdin.read().strip()
    if not message:
        print("Usage: recall_router.py <message>")
        return 1

    result = route(message, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.format == "full":
        print(f"trigger : {result['trigger_class']}")
        print(f"state   : {result['state']}")
        print(f"query   : {result['query']}")
        print(f"delivery: {result['delivery_mode']}")
        if result.get("debug"):
            print(f"debug   : {result['debug']}")
        print()

    output = result.get("output", "")
    if output:
        print(output)
    # If no output, stay silent (don't print anything — clean for hook injection)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
