#!/usr/bin/env python3
"""
Recall Router — Active Recall Layer Phase 1.1

統一入口：使用者訊息 → 分類 → routing → 執行 → structured output + telemetry

輸出 schema (JSON):
{
  "trigger_class": "hard|soft|suppress",
  "state": "continuity|brainstorming|strategy|execution|suppress",
  "delivery_mode": "inline_injection|side_hint|expandable_hint|none",
  "results": [
    {
      "source_type": "memory|wiki|card|bookmark",
      "source_file": "...",
      "section": "...",
      "excerpt": "...",
      "score": 0.0
    }
  ],
  "confidence": 0.0,
  "formatted_text": "...",
  "query": "..."
}

Usage:
  python3 recall_router.py "XKB 下一步是什麼"
  python3 recall_router.py "AI SEO 值不值得做" --format side_hint
  python3 recall_router.py "query" --json
  python3 recall_router.py "query" --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add scripts dir to path for sibling imports
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from conversation_state_parser import parse as parse_state, ParseResult
from continuity_recall import recall as continuity_recall, recall_from_wiki, format_chat as format_continuity_chat
from contrarian_recall import recall as contrarian_recall, format_hint as format_contrarian_hint
from action_recall import recall as action_recall, format_hint as format_action_hint

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
SCRIPTS = WORKSPACE / "skills" / "x-knowledge-base" / "scripts"
TELEMETRY_PATH = WORKSPACE / "memory" / "x-knowledge-base" / "recall-telemetry.jsonl"

# ── Thresholds ─────────────────────────────────────────────────────────────────
MIN_SCORE_HARD = 0.3
MIN_SCORE_SOFT = 0.4
MIN_EXCERPT_LEN = 15

# ── Telemetry ──────────────────────────────────────────────────────────────────

def _write_telemetry(record: dict) -> None:
    """Append a single telemetry record to JSONL log (fire-and-forget)."""
    try:
        TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TELEMETRY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Telemetry never breaks the main flow


def _build_telemetry(
    message: str,
    parsed: ParseResult,
    result_count: int,
    delivery_mode: str,
    duration_ms: int,
) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message_preview": message[:80],
        "trigger_class": parsed.trigger_class,
        "state": parsed.state,
        "confidence": round(parsed.confidence, 3),
        "query": parsed.suggested_query,
        "recalled": result_count > 0,
        "result_count": result_count,
        "delivery_mode": delivery_mode,
        "duration_ms": duration_ms,
        "matched_rules": parsed.matched_rules[:2],
    }


# ── Value filter ───────────────────────────────────────────────────────────────

def _filter_results(results: list, min_score: float) -> list:
    return [
        r for r in results
        if r.score >= min_score and len(getattr(r, "excerpt", "") or "") >= MIN_EXCERPT_LEN
    ]


def _results_to_dicts(results: list) -> list[dict]:
    out = []
    for r in results:
        out.append({
            "source_type": getattr(r, "source_type", "unknown"),
            "source_file": getattr(r, "source_file", ""),
            "section": getattr(r, "section", ""),
            "excerpt": getattr(r, "excerpt", "")[:200],
            "score": getattr(r, "score", 0.0),
            "url": getattr(r, "url", ""),
        })
    return out


# ── Associative recall via recall_for_conversation.py ─────────────────────────

def run_associative_recall(query: str, limit: int = 2) -> tuple[str, list[dict]]:
    """Returns (formatted_text, results_list)."""
    script = SCRIPTS / "recall_for_conversation.py"
    if not script.exists():
        return "", []
    try:
        # Get chat format output
        result_chat = subprocess.run(
            [sys.executable, str(script), query, "--format", "chat", "--limit", str(limit)],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "OPENCLAW_WORKSPACE": str(WORKSPACE)},
        )
        chat_text = result_chat.stdout.strip()

        # Get JSON output for structured results
        result_json = subprocess.run(
            [sys.executable, str(script), query, "--json", "--limit", str(limit)],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "OPENCLAW_WORKSPACE": str(WORKSPACE)},
        )
        try:
            raw_results = json.loads(result_json.stdout)
            results = []
            for item in (raw_results if isinstance(raw_results, list) else []):
                results.append({
                    "source_type": "bookmark",
                    "source_file": item.get("path", ""),
                    "section": item.get("title", ""),
                    "excerpt": (item.get("summary") or "")[:200],
                    "score": item.get("score", 0.0),
                    "url": item.get("url", item.get("source_url", "")),
                })
        except Exception:
            # Fallback: no structured results but we have the text
            results = [{"source_type": "bookmark", "source_file": "", "section": "", "excerpt": chat_text[:200], "score": 0.5, "url": ""}] if chat_text else []

        return chat_text, results
    except Exception as e:
        return f"（associative recall error: {e}）", []


# ── Delivery formatters ────────────────────────────────────────────────────────

def _format_inline(continuity_text: str) -> str:
    return continuity_text

def _format_side_hint(assoc_text: str) -> str:
    return assoc_text

def _format_expandable(query: str, count: int) -> str:
    return f"（你知識庫裡有 {count} 個相關片段，主題：{query[:30]}，要我拉進來嗎？）"


# ── Router ─────────────────────────────────────────────────────────────────────

def route(message: str, dry_run: bool = False) -> dict[str, Any]:
    """
    Main routing logic.
    Returns structured dict with full schema.
    """
    import time
    t0 = time.monotonic()

    # Step 1: Parse
    parsed: ParseResult = parse_state(message)

    if parsed.trigger_class == "suppress":
        duration_ms = int((time.monotonic() - t0) * 1000)
        _write_telemetry(_build_telemetry(message, parsed, 0, "none", duration_ms))
        return {
            "trigger_class": "suppress",
            "state": parsed.state,
            "delivery_mode": "none",
            "results": [],
            "confidence": parsed.confidence,
            "formatted_text": "",
            "query": "",
        }

    query = parsed.suggested_query or message[:60]

    if dry_run:
        return {
            "trigger_class": parsed.trigger_class,
            "state": parsed.state,
            "delivery_mode": "TBD (dry-run)",
            "results": [],
            "confidence": parsed.confidence,
            "formatted_text": "[dry-run: recall not executed]",
            "query": query,
            "debug": {"matched_rules": parsed.matched_rules},
        }

    # Step 2: Execute recall
    if parsed.trigger_class == "hard":
        # Continuity recall (primary)
        raw_results = continuity_recall(query, source="both", top_k=4)
        filtered = _filter_results(raw_results, MIN_SCORE_HARD)
        result_dicts = _results_to_dicts(filtered[:3])

        text_parts: list[str] = []
        if filtered:
            text_parts.append(_format_inline(format_continuity_chat(filtered[:3])))

        # Action recall (supplement for execution-planning state)
        action_results = action_recall(query, top_k=3)
        if action_results:
            action_text = format_action_hint(action_results)
            if action_text:
                text_parts.append(action_text)
                result_dicts += [{"source_type": "action", "source_file": r.path,
                                   "section": r.name, "excerpt": r.description,
                                   "score": r.score, "url": ""} for r in action_results]

        delivery_mode = "inline_injection" if text_parts else "none"
        formatted_text = "\n\n".join(text_parts) if text_parts else ""
        duration_ms = int((time.monotonic() - t0) * 1000)
        _write_telemetry(_build_telemetry(message, parsed, len(result_dicts), delivery_mode, duration_ms))

        return {
            "trigger_class": "hard",
            "state": parsed.state,
            "delivery_mode": delivery_mode,
            "results": result_dicts,
            "confidence": parsed.confidence,
            "formatted_text": formatted_text,
            "query": query,
        }

    else:  # soft
        # Wiki recall (highest priority — synthesized knowledge)
        wiki_results = recall_from_wiki(query, top_k=2)
        wiki_results_filtered = [r for r in wiki_results if r.score >= 0.4]
        wiki_text = format_continuity_chat(wiki_results_filtered) if wiki_results_filtered else ""
        wiki_result_dicts = [{"source_type": "wiki", "source_file": r.source_file,
                               "section": r.section, "excerpt": r.excerpt,
                               "score": r.score, "url": r.url} for r in wiki_results_filtered]

        # Associative recall (bookmark/card supplement)
        assoc_text, assoc_results = run_associative_recall(query, limit=2)

        # Contrarian recall (supplement — max 1 result, only on high-confidence soft)
        contrarian_text = ""
        contrarian_results = []
        if parsed.confidence >= 0.55:
            c_raw = contrarian_recall(query, top_k=1)
            if c_raw:
                contrarian_text = format_contrarian_hint(c_raw)
                contrarian_results = [{"source_type": "contrarian", "source_file": r.source_file,
                                       "section": r.section, "excerpt": r.excerpt,
                                       "score": r.score, "url": ""} for r in c_raw]

        all_results = wiki_result_dicts + assoc_results + contrarian_results
        has_wiki = bool(wiki_text)
        has_assoc = bool(assoc_text and len(assoc_text) >= 20)
        has_content = has_wiki or has_assoc

        if not has_content and not contrarian_text:
            duration_ms = int((time.monotonic() - t0) * 1000)
            _write_telemetry(_build_telemetry(message, parsed, 0, "none", duration_ms))
            return {
                "trigger_class": "soft",
                "state": parsed.state,
                "delivery_mode": "none",
                "results": [],
                "confidence": parsed.confidence,
                "formatted_text": "",
                "query": query,
            }

        # Delivery mode based on content quality, not trigger confidence
        best_wiki_score = max((r.score for r in wiki_results_filtered), default=0.0)
        if best_wiki_score >= 2.0 or (not has_wiki and parsed.confidence >= 0.6):
            delivery_mode = "side_hint"
        else:
            delivery_mode = "expandable_hint"

        text_parts = []
        # Wiki first (highest authority)
        if has_wiki:
            text_parts.append(wiki_text)
        # Bookmark supplement
        if has_assoc:
            if delivery_mode == "side_hint":
                text_parts.append(_format_side_hint(assoc_text))
            elif not has_wiki:
                text_parts.append(_format_expandable(query, len(assoc_results) or 1))
        if contrarian_text:
            text_parts.append(contrarian_text)
        formatted_text = "\n\n".join(text_parts)

        duration_ms = int((time.monotonic() - t0) * 1000)
        _write_telemetry(_build_telemetry(message, parsed, len(all_results), delivery_mode, duration_ms))

        return {
            "trigger_class": "soft",
            "state": parsed.state,
            "delivery_mode": delivery_mode,
            "results": all_results,
            "confidence": parsed.confidence,
            "formatted_text": formatted_text,
            "query": query,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Recall Router — Active Recall Layer")
    parser.add_argument("message", nargs="?", help="User message")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Output full structured result as JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show routing decision without executing recall")
    parser.add_argument("--format", choices=["chat", "full"], default="chat")
    args = parser.parse_args()

    message = args.message or sys.stdin.read().strip()
    if not message:
        print("Usage: recall_router.py <message>")
        return 1

    result = route(message, dry_run=args.dry_run)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.format == "full":
        print(f"trigger : {result['trigger_class']}")
        print(f"state   : {result['state']}")
        print(f"query   : {result['query']}")
        print(f"delivery: {result['delivery_mode']}")
        print(f"results : {len(result['results'])}")
        print(f"conf    : {result['confidence']:.2f}")
        print()

    output = result.get("formatted_text", "")
    if output:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
