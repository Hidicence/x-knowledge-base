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
from continuity_recall import (recall as continuity_recall, recall_from_wiki,
                               format_chat as format_continuity_chat,
                               card_similarities, CARD_MIN_SIMILARITY)
from contrarian_recall import recall as contrarian_recall, format_hint as format_contrarian_hint
from action_recall import recall as action_recall, format_hint as format_action_hint

try:
    from _session_dedup import filter_new as _dedup_filter_new, mark_shown as _dedup_mark_shown
    _DEDUP_AVAILABLE = True
except ImportError:
    _DEDUP_AVAILABLE = False
    def _dedup_filter_new(results):  # type: ignore[misc]
        return results, []
    def _dedup_mark_shown(_results) -> None:  # type: ignore[misc]
        pass

import xkb_paths
import xkb_score

WORKSPACE = xkb_paths.WORKSPACE
# 隔壁的腳本，不是「workspace 底下某個猜出來的位置」
SCRIPTS = xkb_paths.SCRIPTS_DIR
TELEMETRY_PATH = xkb_paths.TELEMETRY_PATH

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


def _drop_irrelevant_cards(query: str, results: list[dict]) -> list[dict]:
    """用真實相似度濾掉不相關的卡片。

    gbrain 回的 score 是 RRF 排名分數：它說的是「這張排第幾」，
    不是「這張多相關」。名次第一永遠約 0.88，即使問的主題整個知識庫都沒有。
    這裡把候選的真實餘弦相似度算出來，低於門檻就丟掉。

    無法判斷時（沒有卡片索引、拿不到 embedding）原樣放行——
    寧可多給幾筆讓 agent 自己判斷，也不要因為索引沒建好就把所有卡片濾光。
    """
    cards = [r for r in results if r.get("source_type") in ("card", "bookmark")]
    if not cards:
        return results

    scores = card_similarities(query, [str(r.get("source_file", "")) for r in cards])
    if scores is None:
        return results

    kept: list[dict] = []
    for r in results:
        if r.get("source_type") not in ("card", "bookmark"):
            kept.append(r)
            continue
        similarity = scores.get(str(r.get("source_file", "")))
        if similarity is None or similarity >= CARD_MIN_SIMILARITY:
            if similarity is not None:
                # 用真實相似度取代排名分數，這樣跨層排序才有意義
                r["score"] = round(similarity, 3)
            kept.append(r)
    return kept


def _format_assoc_chat(results: list[dict]) -> str:
    if not results:
        return ""
    lines = ["相關卡片／書籤："]
    for r in results:
        title = r.get("section") or r.get("source_file", "")
        lines.append(f"- {title}：{r.get('excerpt', '')}")
        if r.get("url"):
            lines.append(f"  原文：{r['url']}")
    return "\n".join(lines)


def run_associative_recall(query: str, limit: int = 2) -> tuple[str, list[dict]]:
    """Returns (formatted_text, results_list)."""
    script = SCRIPTS / "recall_for_conversation.py"
    if not script.exists():
        # 隔壁檔案不見了是安裝壞掉，不是「查無資料」——要出聲，不要靜靜回空
        msg = f"associative recall unavailable: {script} not found"
        print(msg, file=sys.stderr)
        return f"（{msg}）", []
    try:
        _sub_env = xkb_paths.subprocess_env({"OPENCLAW_WORKSPACE": str(WORKSPACE)})

        # 只跑一次。原本跑兩次（一次要 chat 文字、一次要 JSON），而這支每次都要做
        # 一輪語意搜尋，等於整個召回的成本平白翻倍。文字從 JSON 自己組就好。
        result_json = subprocess.run(
            [sys.executable, str(script), query, "--json", "--limit", str(limit)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            env=_sub_env,
        )
        chat_text = ""
        try:
            raw = json.loads(result_json.stdout)
            # recall_for_conversation.py 回的是 {"query", "wiki_hits", "results", ...}，
            # 不是一個 list。舊寫法用 isinstance(list) 判斷，永遠不成立，
            # 於是所有書籤/卡片結果都被靜靜丟掉。
            if isinstance(raw, dict):
                items = raw.get("results") or []
            elif isinstance(raw, list):
                items = raw
            else:
                items = []
            results = []
            for item in items:
                results.append({
                    "source_type": "card" if str(item.get("relative_path", "")).startswith("cards/") else "bookmark",
                    "source_file": item.get("relative_path") or item.get("path", ""),
                    "section": item.get("title", ""),
                    "excerpt": (item.get("summary") or "")[:200],
                    "score": item.get("score", 0.0),
                    "url": item.get("source_url") or item.get("url", ""),
                })
            chat_text = _format_assoc_chat(results)
        except Exception:
            results = []

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
        filtered, _ = _dedup_filter_new(filtered)
        result_dicts = _results_to_dicts(filtered[:3])

        text_parts: list[str] = []
        if filtered:
            text_parts.append(_format_inline(format_continuity_chat(filtered[:3])))

        # 卡片與書籤。
        # hard trigger 是「我們之前怎麼…」這種明確的回想要求，而使用者存最多東西的地方
        # 就是卡片（wiki 只有十幾個 topic，卡片上千張）。原本這條路徑只查 wiki 與記憶檔，
        # 等於問「之前怎麼做的」反而查不到主要的知識來源。
        assoc_text, assoc_results = run_associative_recall(query, limit=2)
        assoc_results = _drop_irrelevant_cards(message, assoc_results)
        assoc_results, _ = _dedup_filter_new(assoc_results)
        if assoc_results:
            result_dicts += assoc_results
            if assoc_text:
                text_parts.append(assoc_text)

        # Action recall (supplement for execution-planning state)
        action_results = action_recall(query, top_k=3)
        if action_results:
            action_text = format_action_hint(action_results)
            if action_text:
                text_parts.append(action_text)
                result_dicts += [{"source_type": "action", "source_file": r.path,
                                   "section": r.name, "excerpt": r.description,
                                   "score": r.score, "url": ""} for r in action_results]

        # 各層分數尺度不同，換算成可比的 unified_score 之後才排序
        result_dicts = xkb_score.rank(result_dicts)

        delivery_mode = "inline_injection" if text_parts else "none"
        formatted_text = "\n\n".join(text_parts) if text_parts else ""
        _dedup_mark_shown(filtered[:3])
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
        # Light scan：沒有任何規則命中，只是「順手看一眼」。
        # 只掃 wiki（~20ms），不跑語意搜尋（1~2 秒），而且分數門檻拉高——
        # 沒有明確訊號時，寧可安靜，也不要拿低分結果插嘴。
        light = parsed.confidence < 0.4
        min_wiki_score = 0.5 if light else 0.4

        # Wiki recall (highest priority — synthesized knowledge)
        # light scan 不做語意：它跑在幾乎每一句話上，每句多打一次 embedding API
        # 既慢又花錢。有規則命中（真的在問東西）才值得那一次呼叫。
        wiki_results = recall_from_wiki(query, top_k=2, semantic=not light)
        wiki_results_filtered = [r for r in wiki_results if r.score >= min_wiki_score]
        wiki_results_filtered, _ = _dedup_filter_new(wiki_results_filtered)
        wiki_text = format_continuity_chat(wiki_results_filtered) if wiki_results_filtered else ""
        wiki_result_dicts = [{"source_type": r.source_type, "source_file": r.source_file,
                               "section": r.section, "excerpt": r.excerpt,
                               "score": r.score, "url": r.url} for r in wiki_results_filtered]

        # Associative recall (bookmark/card supplement) — light scan 不跑，太貴
        if light:
            assoc_text, assoc_results = "", []
        else:
            assoc_text, assoc_results = run_associative_recall(query, limit=2)
            assoc_results = _drop_irrelevant_cards(message, assoc_results)
            assoc_results, _ = _dedup_filter_new(assoc_results)

        # Contrarian recall (supplement — max 1 result, only on high-confidence soft)
        contrarian_text = ""
        contrarian_results = []
        if parsed.confidence >= 0.55:
            c_raw = contrarian_recall(query, top_k=1)
            c_raw, _ = _dedup_filter_new(c_raw)
            if c_raw:
                contrarian_text = format_contrarian_hint(c_raw)
                contrarian_results = [{"source_type": "contrarian", "source_file": r.source_file,
                                       "section": r.section, "excerpt": r.excerpt,
                                       "score": r.score, "url": ""} for r in c_raw]

        # 各層分數尺度不同，換算成可比的 unified_score 之後才排序
        all_results = xkb_score.rank(wiki_result_dicts + assoc_results + contrarian_results)
        _dedup_mark_shown(all_results)
        has_wiki = bool(wiki_text)
        # has_assoc must check dedup-filtered results, not raw assoc_text
        # (avoids showing "有 N 個相關片段" when everything was dedup'd)
        has_assoc = bool(assoc_results) or bool(assoc_text and len(assoc_text) >= 20 and wiki_result_dicts)
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
        # Bookmark supplement — only if there are actual (non-dedup'd) results
        if has_assoc and assoc_results:
            if delivery_mode == "side_hint":
                text_parts.append(_format_side_hint(assoc_text))
            elif not has_wiki:
                text_parts.append(_format_expandable(query, len(assoc_results)))
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
