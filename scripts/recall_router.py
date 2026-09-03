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
from concurrent.futures import ThreadPoolExecutor

# 卡片層的上限。原本由 subprocess 的 timeout=20 提供，改成同行程後要自己帶。
ASSOCIATIVE_TIMEOUT_S = 20

# side_hint 的門檻。兩個尺度，兩個值——用同一個數字比對餘弦相似度與關鍵字
# 分數，是這個專案反覆犯的那一類錯。
SIDE_HINT_KEYWORD = 2.0
# 2026-09-01 實測 wiki 語意分數：檢索本身有 0.65 的下限，實際命中落在
# 0.666–0.843。所以 0.5 跟原本的 2.0 一樣是個常數分支，只是常數翻了面——
# 我上次「修好」的是哪一邊永遠成立，不是這個判斷有沒有在判斷。
# 0.75 把「直接命中」（0.84）跟「沾到邊」（0.67–0.72）分開。
SIDE_HINT_SEMANTIC = 0.75

# 關鍵字分數的實際範圍：下限 6（min_score），實測上界約 20。換算成 0–1 時
# 用固定的上界而不是這一批的最大值——用批內最大值的話，一批爛結果裡最好的
# 那一個會被算成滿分，這正是 MMR 正規化當初犯過的錯。
KEYWORD_SCORE_FLOOR = 6.0
KEYWORD_SCORE_CEILING = 20.0


def _as_unit_scale(score: float) -> float:
    """把關鍵字分數換算到 0–1，好跟餘弦相似度放在一起比。"""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return 0.0
    span = KEYWORD_SCORE_CEILING - KEYWORD_SCORE_FLOOR
    return max(0.0, min(1.0, (value - KEYWORD_SCORE_FLOOR) / span))

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

# Imported like any other sibling. It used to be wrapped in a try/except that
# fell back to a no-op, and on 2026-05-04 the module was archived rather than
# deleted — so the import started failing, the fallback swallowed it, and the
# "do not show the same knowledge twice in one conversation" filter was off
# for four months without a single error. A guard that can silently become a
# no-op is not a guard.
from _session_dedup import filter_new as _dedup_filter_new, mark_shown as _dedup_mark_shown

import xkb_paths
import xkb_failures
import xkb_provenance
import xkb_relevance
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


# 過濾要跟檢索用同一個字串。原本檢索用 query（suggested_query），過濾卻用
# 原始 message：英文輸入時 suggested_query 是關鍵字包，於是拿一整句閒聊去
# 評判關鍵字包撈回來的結果，好卡片會被推到 0.55 門檻以下。而且這保證了
# query-vector 快取一定失效——那個快取存在的理由就是「一次召回不要把同一
# 句話送去轉兩次向量」。
def _drop_irrelevant_cards(query: str, results: list[dict]) -> list[dict]:
    """用真實相似度濾掉不相關的卡片。判斷邏輯在 xkb_relevance，不要在這裡另寫一份。"""
    cards = [r for r in results if r.get("source_type") in ("card", "bookmark")]
    if not cards:
        return results
    kept_cards, _, _ = xkb_relevance.filter_irrelevant(
        query, cards, key_of=lambda r: str(r.get("source_file", "")))
    keep = {id(r) for r in kept_cards}
    return [r for r in results
            if r.get("source_type") not in ("card", "bookmark") or id(r) in keep]


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
    """Returns (formatted_text, results_list).

    Calls recall_for_conversation.search() directly. It used to spawn the same
    file as a child process, which cost an interpreter start and turned every
    failure into an unparseable stdout — indistinguishable from "found
    nothing". gbrain's own 1.2 s is paid either way; what this removes is the
    part that was never doing any work.
    """
    try:
        from recall_for_conversation import search as _associative_search
    except ImportError as err:
        # 隔壁模組載不進來是安裝壞掉，不是「查無資料」——要出聲，不要靜靜回空
        msg = f"associative recall unavailable: {err}"
        print(msg, file=sys.stderr)
        return f"（{msg}）", []
    try:
        # subprocess 版本有 timeout=20，改成同行程之後那個界線消失了，而
        # recall_for_conversation 的嵌入路徑本身沒有任何逾時。hook 只給六秒，
        # 所以一個卡住的端點會無限期擋住整條召回。界線要留著。
        # 不能用 with。Executor.__exit__ 會 shutdown(wait=True)，所以逾時
        # 之後那個區塊還是會等到工作結束——實測宣稱 0.5 秒、實際 3.00 秒。
        # 這個界線存在的理由就是不要被卡住的端點拖住六秒的 hook 預算，
        # 而它原本一秒都沒擋到。
        bounded = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xkb-assoc")
        try:
            found = bounded.submit(
                lambda: _associative_search(query, limit=limit)
            ).result(timeout=ASSOCIATIVE_TIMEOUT_S)
            items, mode = found["results"], found["search_mode"]
        finally:
            # wait=False：放棄它，不要等。執行緒是 daemon，所以卡住的請求
            # 也不會讓整個行程留著不走。
            bounded.shutdown(wait=False)
        # 關鍵字分數是 base + 調整×10、下限 6、沒有上界；RRF 是 0–1。
        # 下游的 rank() 用 0.88 當錨點，對 RRF 是對的，對關鍵字分數會讓一張
        # 得 15 分的卡片算出 0.94，壓過所有語意結果（最好也才 0.55）。
        # 分數要帶著自己的尺度，不要讓下游用猜的——這一類錯誤這個專案犯了四次。
        keyword_scale = mode in ("keyword", "keyword_fallback")
        results = [
            {
                "source_type": "card" if str(item.get("relative_path", "")).startswith("cards/") else "bookmark",
                "source_file": item.get("relative_path") or item.get("path", ""),
                "section": xkb_provenance.strip_markers(item.get("title", "")),
                "excerpt": xkb_provenance.strip_markers((item.get("summary") or "")[:200]),
                # 字面命中識別碼的結果，score 已經是 0–1 的固定值，
                # 不要再套關鍵字換算（會把它壓到門檻以下）。
                "score": item.get("score", 0.0) if item.get("match_kind") == "literal"
                         else _as_unit_scale(item.get("score", 0.0)) if keyword_scale
                         else item.get("score", 0.0),
                "match_kind": item.get("match_kind"),
                # score_scale 要帶過來：字面命中的結果跳過 filter_irrelevant 的
                # 改寫路徑，不帶的話 rank() 會退回 source_type=card 的 RRF 錨點
                # (0.88) 而不是餘弦錨點 (0.72)，把它算低 ~9%，反而排在它要
                # 超越的餘弦卡片下面。
                "score_scale": item.get("score_scale"),
                "url": item.get("source_url") or item.get("url", ""),
            }
            for item in items
        ]
        return _format_assoc_chat(results), results
    except Exception as e:
        # 後端壞掉不能長得像「查無資料」。這裡原本還套了一層 except，
        # 把例外變成空陣列，於是下面這行報告從來沒有機會執行。
        xkb_failures.note("associative recall", e)
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
        # 兩個貴的層平行跑。continuity 花 2.7 秒在本機向量運算，卡片層花 1.2 秒
        # 等外部行程，而且互不相依——排隊跑只是因為當初就那樣寫。
        with ThreadPoolExecutor(max_workers=2) as pool:
            assoc_future = pool.submit(run_associative_recall, query, 2)
            raw_results = continuity_recall(query, source="both", top_k=4)
            assoc_text, assoc_results = assoc_future.result()

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
        # （結果在上面就跟 continuity 一起平行取回了。）
        assoc_results = _drop_irrelevant_cards(query, assoc_results)
        assoc_results, _ = _dedup_filter_new(assoc_results)
        if assoc_results:
            result_dicts += assoc_results
            if assoc_text:
                text_parts.append(_format_assoc_chat(assoc_results))

        # Action recall (supplement for execution-planning state)
        action_results = action_recall(query, top_k=3)
        if action_results:
            action_text = format_action_hint(action_results)
            if action_text:
                text_parts.append(action_text)
                result_dicts += [{"source_type": "action", "source_file": r.path,
                                   "section": xkb_provenance.strip_markers(r.name),
                                   "excerpt": xkb_provenance.strip_markers(r.description),
                                   "score": r.score, "url": ""} for r in action_results]

        # 各層分數尺度不同，換算成可比的 unified_score 之後才排序
        result_dicts = xkb_score.rank(result_dicts)

        delivery_mode = "inline_injection" if text_parts else "none"
        formatted_text = "\n\n".join(text_parts) if text_parts else ""
        # 卡片層的結果也進了 result_dicts 與輸出文字，卻沒有被標記為已顯示，
        # 所以同一張卡在整個 session 視窗（四小時）裡每次 hard trigger 都會
        # 再推一次。soft 路徑本來就做對了。
        _dedup_mark_shown(filtered[:3])
        _dedup_mark_shown(assoc_results)
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
        # （run_associative_recall 會打向量/外部呼叫，~1.2s，而 light 幾乎每句都跑）。
        if light:
            assoc_text, assoc_results = "", []
            # 例外：查詢裡有識別碼時，只跑 identifier_recall——它是純字串比對，
            # 不打 embedding，命中太多卡的 token 會被當雜訊丟掉，所以 gpt-4o
            # 這種常見版本號不會產生東西。真正的 tweet ID / repo slug 才會。
            try:
                from recall_for_conversation import (
                    _identifier_tokens as _ident, identifier_recall as _ir,
                    load_index as _li, INDEX_FILE as _IDX)
                if _ident(query):
                    _items = _li(_IDX).get("items", [])
                    # identifier_recall 回的是 recall() 的 dict 形狀（title/summary），
                    # 而下游的 _format_assoc_chat 與 xkb_score.rank 要的是
                    # section/excerpt/source_type——跟 run_associative_recall 內部
                    # 那個 list comp 同一個形狀。這裡照樣映一次。
                    assoc_results = [{
                        "source_type": "card" if "cards/" in str(h.get("relative_path", "")) else "bookmark",
                        "source_file": h.get("relative_path", ""),
                        "section": xkb_provenance.strip_markers(h.get("title", "")),
                        "excerpt": xkb_provenance.strip_markers((h.get("summary") or "")[:200]),
                        "score": h.get("score", 0.0),
                        "match_kind": h.get("match_kind"),
                        "score_scale": h.get("score_scale"),
                        "url": h.get("source_url", ""),
                    } for h in _ir(query, _items, 3)]
                    assoc_results, _ = _dedup_filter_new(assoc_results)
                    if assoc_results:
                        assoc_text = _format_assoc_chat(assoc_results)
            except Exception:
                pass
        else:
            assoc_text, assoc_results = run_associative_recall(query, limit=2)
            assoc_results = _drop_irrelevant_cards(query, assoc_results)
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
        # 標記要等到確定送出去之後。原本在這裡就全部標記，於是分數落在
        # 0.666–0.749 的 wiki 命中會走 expandable 分支、卡片一個字都沒
        # 出現，卻已經被記成「顯示過」而在四小時內被壓掉。
        # 被計算過不等於被讀到。
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
        # 2.0 是關鍵字時代的門檻（wiki 分數當時是 0.40–3.65）。改用語意之後
        # 分數上限是 1.0，所以這個條件永遠不成立：只要有 wiki 命中就一定走
        # expandable_hint，而卡片層算完、過濾完、去重完，然後完全不顯示。
        wiki_is_semantic = any(
            r.source_type.endswith("_semantic") for r in wiki_results_filtered
        )
        wiki_threshold = SIDE_HINT_SEMANTIC if wiki_is_semantic else SIDE_HINT_KEYWORD
        _has_literal = any(x.get("match_kind") == "literal" for x in assoc_results)
        if _has_literal or best_wiki_score >= wiki_threshold or (not has_wiki and parsed.confidence >= 0.6):
            # 字面命中識別碼 => 使用者要的就是這張卡，直接給內容，
            # 不要只回「你知識庫裡有 N 個相關片段」。
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
                # 用過濾後的結果重新排版。assoc_text 是 run_associative_recall
                # 在過濾之前就排好的字串，所以 _drop_irrelevant_cards 與去重
                # 只改了數字和遙測，注入給模型的文字一個字都沒少。
                text_parts.append(_format_side_hint(_format_assoc_chat(assoc_results)))
            else:
                # 有 wiki 命中時原本什麼都不放，卡片就這樣算完、過濾完、
                # 去重完，然後消失。摘要式的提示至少讓它可被取用。
                text_parts.append(_format_expandable(query, len(assoc_results)))
        if contrarian_text:
            text_parts.append(contrarian_text)
        # 只有真的把內容送出去的才算顯示過。_format_expandable 只印一個數量
        # （「你知識庫裡有 2 個相關片段」），卡片的標題與摘要一個字都沒出現；
        # 把它們記成顯示過，下一輪就會被去重濾掉，於是那個數量變成 0，
        # 內容永遠送不出去。原本 `if text_parts:` 在這裡永遠成立，等於沒改。
        _dedup_mark_shown(wiki_result_dicts + contrarian_results)
        if delivery_mode == "side_hint":
            _dedup_mark_shown(assoc_results)
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
