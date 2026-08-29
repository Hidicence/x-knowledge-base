#!/usr/bin/env python3
"""
XKB Pipeline Health Check

檢查五件事：
1. wiki 單一真實來源：兩個路徑是否指向同一位置（symlink 正確）
2. recall 命中的 wiki 來源路徑是否正確
3. recall 端到端是否真的跑得起來（不是只看檔案在不在）
4. recall telemetry 是否還在寫入（太久沒紀錄 = 靜默故障）
5. cron 執行後 summary / vector 是否真的有增量更新

Usage:
    python3 scripts/health_check_pipeline.py
    python3 scripts/health_check_pipeline.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths

WORKSPACE = xkb_paths.WORKSPACE
_SKILL_DIR = xkb_paths.SKILL_DIR
WIKI_DIR = xkb_paths.WIKI_DIR
WIKI_TOPICS_DIR = xkb_paths.WIKI_TOPICS_DIR
BOOKMARKS_DIR = xkb_paths.BOOKMARKS_DIR
INDEX_FILE = xkb_paths.INDEX_FILE
VECTOR_FILE = xkb_paths.VECTOR_FILE
CARDS_DIR = xkb_paths.CARDS_DIR

WORKSPACE_WIKI = WORKSPACE / "wiki"  # 若存在，必須指向 WIKI_DIR

OK = "✅"
WARN = "⚠️ "
FAIL = "❌"


def check_wiki_canonical() -> dict:
    """檢查 workspace/wiki 是否是指向 skill wiki 的 symlink。"""
    result = {"name": "wiki_canonical", "checks": []}

    # 1. skill wiki 是否存在
    if WIKI_DIR.exists():
        result["checks"].append({"ok": True, "msg": f"Skill wiki exists: {WIKI_DIR}"})
    else:
        result["checks"].append({"ok": False, "msg": f"Skill wiki MISSING: {WIKI_DIR}"})
        return result

    # 2. workspace/wiki 是否是 symlink
    if WORKSPACE_WIKI.is_symlink():
        target = Path(os.readlink(WORKSPACE_WIKI))
        if not target.is_absolute():
            target = (WORKSPACE_WIKI.parent / target).resolve()
        canonical = WIKI_DIR.resolve()
        if target.resolve() == canonical:
            result["checks"].append({"ok": True, "msg": f"workspace/wiki → {WIKI_DIR} (symlink correct)"})
        else:
            result["checks"].append({"ok": False, "msg": f"workspace/wiki symlink points to {target}, expected {canonical}"})
    elif WORKSPACE_WIKI.exists():
        # Windows junction / bind mount 不會被 is_symlink() 認出來，但解析後仍是同一個地方
        if WORKSPACE_WIKI.resolve() == WIKI_DIR.resolve():
            result["checks"].append({"ok": True, "msg": f"workspace/wiki → {WIKI_DIR} (link correct)"})
        else:
            result["checks"].append({"ok": False, "msg": "workspace/wiki is a real directory (not symlink) — dual-wiki risk!"})
    else:
        # 這個 symlink 是 VPS 上 OpenClaw 的擺法，不是 XKB 的必要條件。
        # 沒有它就沒有第二份 wiki，也就沒有這一項要防的風險。
        result["checks"].append({"ok": True, "msg": "workspace/wiki absent — no dual-wiki risk"})

    # 3. topic 數量
    topics = list(WIKI_TOPICS_DIR.glob("*.md")) if WIKI_TOPICS_DIR.exists() else []
    result["checks"].append({"ok": len(topics) > 0, "msg": f"Wiki topics: {len(topics)} pages"})

    return result


def check_recall_wiki_source() -> dict:
    """確認 WIKI_TOPICS_DIR 路徑是 skill wiki，而非舊的 workspace wiki。"""
    result = {"name": "recall_wiki_source", "checks": []}

    canonical = WIKI_DIR.resolve()
    actual = WIKI_TOPICS_DIR.parent.resolve()

    if actual == canonical:
        result["checks"].append({"ok": True, "msg": f"Recall reads from canonical wiki: {canonical}"})
    else:
        result["checks"].append({"ok": False, "msg": f"Recall reads from {actual}, expected {canonical}"})

    # 測試一個 sample 查詢能否命中 wiki
    if WIKI_TOPICS_DIR.exists():
        topics = list(WIKI_TOPICS_DIR.glob("*.md"))
        result["checks"].append({"ok": True, "msg": f"Wiki topics accessible: {[t.stem for t in topics]}"})
    else:
        result["checks"].append({"ok": False, "msg": "WIKI_TOPICS_DIR not found — recall wiki layer will return empty"})

    return result


def check_recall_live() -> dict:
    """真的跑一次 recall，而不是只確認檔案在不在。

    2026-05-04 的 script 清理移除了 recall_router 依賴的模組，recall 從此每次都
    崩潰；但當時的檢查只看目錄存不存在，所以整整 12 週沒有人發現。這個檢查是那次
    事故的直接產物：唯一能證明 recall 還活著的方法，就是把它叫起來一次。
    """
    result = {"name": "recall_live", "checks": []}

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import xkb_recall_server as server
    except Exception as e:
        result["checks"].append({"ok": False, "msg": f"cannot import xkb_recall_server: {e}"})
        return result

    if not server.ROUTER_SCRIPT.exists():
        result["checks"].append({"ok": False, "msg": f"router missing: {server.ROUTER_SCRIPT}"})
        return result

    probe = server._run_recall_structured("XKB 的知識管線是怎麼設計的")
    status = probe.get("status", "ok")
    if status == "failed":
        result["checks"].append({"ok": False, "msg": f"recall FAILED end-to-end: {probe.get('error')}"})
    else:
        result["checks"].append({
            "ok": True,
            "msg": f"recall ran end-to-end: {len(probe.get('results') or [])} result(s), "
                   f"confidence {probe.get('confidence')}"
        })

    return result


def check_recall_telemetry() -> dict:
    """Dead man's switch：太久沒有任何一筆 recall 紀錄，本身就是故障訊號。"""
    result = {"name": "recall_telemetry", "checks": []}

    stale_days = float(os.getenv("XKB_RECALL_STALE_DAYS", "7"))
    path = WORKSPACE / "memory" / "x-knowledge-base" / "recall-telemetry.jsonl"

    if not path.exists():
        result["checks"].append({"ok": False, "msg": f"recall telemetry not found: {path}"})
        return result

    age_days = (datetime.now().timestamp() - path.stat().st_mtime) / 86400
    result["checks"].append({
        "ok": age_days <= stale_days,
        "msg": f"last recall telemetry: {age_days:.1f} days ago (threshold {stale_days:.0f}d)"
    })

    # 最後一筆是不是失敗的
    try:
        last = None
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
        if last:
            entry = json.loads(last)
            recalled = entry.get("recalled")
            result["checks"].append({
                "ok": True,
                "msg": f"last entry: recalled={recalled}, results={entry.get('result_count')}, "
                       f"ts={entry.get('ts', '')[:19]}"
            })
    except Exception as e:
        result["checks"].append({"ok": False, "msg": f"telemetry unreadable: {e}"})

    return result


def check_semantic_index() -> dict:
    """語意索引存不存在、跟得上不跟得上內容。

    向量是離線建的：wiki 或記憶檔改了但沒重建索引，召回會用舊向量算相似度，
    不會報錯，只會撈回過時的段落。二進位格式又沒有自我描述能力，
    keys 與 .bin 不同步時切出來的向量是錯位的——同樣不會拋錯。
    """
    result = {"name": "semantic_index", "checks": []}
    meta_path = Path(os.getenv("XKB_SEMANTIC_INDEX",
                               str(BOOKMARKS_DIR / "semantic_index.json")))
    bin_path = meta_path.with_suffix(".bin")

    if not (meta_path.exists() and bin_path.exists()):
        result["checks"].append({
            "ok": False,
            "msg": "semantic index missing — 召回會退回字串比對（中文命中率大幅下降）",
        })
        return result

    try:
        with meta_path.open(encoding="utf-8") as fh:
            meta = json.load(fh)
        indexed = int(meta.get("count", 0))
        expected_bytes = int(meta.get("bytes", 0))
    except (OSError, ValueError):
        result["checks"].append({"ok": False, "msg": f"semantic index meta unreadable: {meta_path}"})
        return result

    actual_bytes = bin_path.stat().st_size
    result["checks"].append({
        "ok": actual_bytes == expected_bytes,
        "msg": f"index integrity: {indexed} vectors, {actual_bytes} bytes"
               + ("" if actual_bytes == expected_bytes else f" — 應為 {expected_bytes}，keys 與 .bin 不同步"),
    })

    # 內容比索引新 = 索引過時
    newest = 0.0
    for directory in (WIKI_TOPICS_DIR, WORKSPACE / "memory"):
        if not directory.exists():
            continue
        for path in list(directory.glob("*.md"))[:400]:
            newest = max(newest, path.stat().st_mtime)

    if newest:
        lag_hours = (newest - bin_path.stat().st_mtime) / 3600
        max_lag = float(os.getenv("XKB_SEMANTIC_MAX_LAG_HOURS", "26"))
        result["checks"].append({
            "ok": lag_hours <= max_lag,
            "msg": f"index freshness: 內容比索引新 {max(lag_hours, 0):.1f}h (threshold {max_lag:.0f}h)"
                   + ("" if lag_hours <= max_lag else " — 重跑 build_vector_index.py --incremental"),
        })

    return result


def check_topic_map() -> dict:
    """卡片→wiki 的對應表是不是真的在對應東西。

    2026-05-04 的 productization cleanup 把真實對應表換成了 repo 裡的範本
    （your-category-slug → your-wiki-topic-slug）。之後 sync_cards_to_wiki
    每次都回報「No mapped cards found」就結束，離開碼 0，看起來完全正常——
    卡片進 wiki 這條路空轉了三個月沒有人發現。

    範本值是可以直接認出來的，認出來就該報錯。
    """
    result = {"name": "topic_map", "checks": []}
    path = WIKI_DIR / "topic-map.json"

    if not path.exists():
        result["checks"].append({"ok": False, "msg": f"topic-map.json not found: {path}"})
        return result

    try:
        with path.open(encoding="utf-8") as fh:
            mapping = (json.load(fh) or {}).get("mapping", {})
    except (OSError, ValueError) as exc:
        result["checks"].append({"ok": False, "msg": f"topic-map unreadable: {exc}"})
        return result

    placeholders = {"your-category-slug", "another-category", "topic-a", "topic-b",
                    "your-wiki-topic-slug"}
    found = [k for k in mapping if k in placeholders]
    if found:
        result["checks"].append({
            "ok": False,
            "msg": f"topic-map 仍是範本（{', '.join(found)}）— 卡片無法進 wiki，這條路等於停擺",
        })
        return result

    # 對應到的主題必須真的存在，否則吸收時會寫到不存在的頁面
    topics = {p.stem for p in WIKI_TOPICS_DIR.glob("*.md")} if WIKI_TOPICS_DIR.exists() else set()
    dangling = sorted({t for v in mapping.values()
                       for t in (v.get("topics", []) if isinstance(v, dict) else [])
                       if t not in topics})
    result["checks"].append({
        "ok": bool(mapping) and not dangling,
        "msg": f"topic-map: {len(mapping)} 個分類對應"
               + ("" if not dangling else f" — 指向不存在的主題: {', '.join(dangling)}"),
    })
    return result


def check_staging_backlog() -> dict:
    """待審候選有沒有在無聲累積。

    2026-04-07 到 07-16 累積了 146 個檔案、470 條候選，被勾選過的只有 1 條——
    對話裡談出來的結論三個半月都沒進知識庫，而且沒有任何地方會提到這件事。
    擷取層有健檢、召回層有健檢，消化層原本什麼都沒有。
    """
    result = {"name": "staging_backlog", "checks": []}
    staging_dir = WIKI_DIR / "_staging"

    if not staging_dir.exists():
        result["checks"].append({"ok": True, "msg": "no staging dir — nothing pending"})
        return result

    max_pending = int(os.getenv("XKB_STAGING_MAX_PENDING", "60"))
    max_age_days = int(os.getenv("XKB_STAGING_MAX_AGE_DAYS", "30"))

    # Read through xkb_review rather than parsing staging again here. The two
    # parsers had already drifted apart on which files they looked at, and a
    # second definition of "pending" is how this repository ends up with two
    # numbers for one question.
    try:
        import xkb_review
        promoted = xkb_review._promoted_ids(
            xkb_review.GOVERNANCE_DIR / "candidate-registry.jsonl")
        outstanding = [
            c for c in xkb_review.load_candidates(classify=False)
            # Keep this check's long-standing scope: top-level staging only,
            # not the pre-2026-07 archive.
            if "/" not in c.source_file
            and c.status == "pending"
            and c.candidate_id not in promoted
        ]
    except Exception as exc:
        result["checks"].append({"ok": False, "msg": f"staging counts unavailable: {exc}"})
        return result

    pending = len(outstanding)
    dates = [c.source_date for c in outstanding if c.source_date != "unknown"]
    oldest: str | None = min(dates) if dates else None

    result["checks"].append({
        "ok": pending <= max_pending,
        "msg": f"pending candidates: {pending} (threshold {max_pending})"
               + ("" if pending <= max_pending else " — 用 xkb_review.py 審核"),
    })

    if oldest:
        age_days = (datetime.now(timezone.utc).date() - datetime.strptime(oldest, "%Y-%m-%d").date()).days
        result["checks"].append({
            "ok": age_days <= max_age_days,
            "msg": f"oldest pending candidate: {oldest} ({age_days}d, threshold {max_age_days}d)",
        })

    return result


def check_governance_actionable() -> dict:
    """Warn only when bounded governance backlog exceeds threshold or is overdue."""
    result = {"name": "governance_actionable", "checks": []}
    try:
        import xkb_review
        counts = xkb_review.governance_health_counts(int(os.getenv("XKB_GOVERNANCE_TTL_DAYS", "30")))
    except Exception as exc:
        result["checks"].append({"ok": False, "msg": f"governance counts unavailable: {exc}"})
        return result
    threshold = int(os.getenv("XKB_GOVERNANCE_MAX_PENDING", os.getenv("XKB_STAGING_MAX_PENDING", "60")))
    actionable = {key: counts.get(key, 0) for key in ("pending", "medium", "low", "proposal", "quarantine", "overdue", "safe_promotion")}
    actionable["ttl"] = actionable["quarantine"]
    result["actionable_counts"] = actionable
    warning = counts["pending"] > threshold or counts["overdue"] > 0
    result["checks"].append({"ok": not warning, "msg": "governance actionable counts: " + json.dumps(actionable, ensure_ascii=False) + (f" — threshold {threshold}" if warning else "")})
    return result


def check_index_freshness() -> dict:
    """檢查 search_index 和 vector_index 的 summary 覆蓋率與更新時間。"""
    result = {"name": "index_freshness", "checks": []}

    # search_index
    if not INDEX_FILE.exists():
        result["checks"].append({"ok": False, "msg": f"search_index.json not found: {INDEX_FILE}"})
        return result

    raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    items = raw.get("items", [])
    total = len(items)
    has_summary = sum(1 for i in items if (i.get("summary") or "").strip())
    enriched = sum(1 for i in items if i.get("enriched"))
    coverage = round(has_summary / total * 100) if total else 0

    result["checks"].append({
        "ok": coverage >= 70,
        "msg": f"search_index summary coverage: {has_summary}/{total} ({coverage}%) | enriched: {enriched}"
    })

    # 最後修改時間
    mtime = INDEX_FILE.stat().st_mtime
    age_hours = (datetime.now().timestamp() - mtime) / 3600
    result["checks"].append({
        "ok": age_hours < 26,
        "msg": f"search_index last updated: {age_hours:.1f}h ago"
    })

    # vector_index：檢查新鮮度就好，不要把 62MB 讀進來數個數。
    # 健檢是每天自動跑的，成本要跟「確認狀態」相稱，不是重做一次工作。
    # 原本 json.loads 整份檔案在 VPS 上要 7 秒（冷快取時更久），
    # 而它換來的只是一個向量數字——那個數字在 semantic_index.json 的 meta 裡就有。
    if not VECTOR_FILE.exists():
        # 語意召回讀的是 semantic_index.bin，不是這一份。
        # 這份只是重建索引時的中間產物，缺了不影響召回。
        result["checks"].append({
            "ok": True,
            "msg": "vector_index.json absent — 召回讀 semantic_index，此檔僅為重建用中間產物",
        })
    else:
        size_mb = VECTOR_FILE.stat().st_size / 1e6
        v_age_hours = (datetime.now().timestamp() - VECTOR_FILE.stat().st_mtime) / 3600
        result["checks"].append({
            "ok": size_mb > 0.1 and v_age_hours < 26,
            "msg": f"vector_index: {size_mb:.0f}MB, last updated {v_age_hours:.1f}h ago"
        })

    # cards vs index coverage
    if CARDS_DIR.exists():
        card_count = len(list(CARDS_DIR.glob("*.md")))
        result["checks"].append({
            "ok": enriched >= card_count * 0.9,
            "msg": f"Cards in memory/cards/: {card_count} | enriched in index: {enriched}"
        })

    return result


def fmt_checks(section: dict) -> str:
    lines = [f"\n── {section['name']} ──"]
    for c in section["checks"]:
        icon = OK if c["ok"] else FAIL
        lines.append(f"  {icon}  {c['msg']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sections = [
        check_wiki_canonical(),
        check_recall_wiki_source(),
        check_recall_live(),
        check_recall_telemetry(),
        check_semantic_index(),
        check_topic_map(),
        check_staging_backlog(),
        check_governance_actionable(),
        check_index_freshness(),
    ]

    if args.json:
        print(json.dumps(sections, ensure_ascii=False, indent=2))
        # 離開碼要反映健康狀態，--json 也一樣：排程是靠離開碼判斷要不要告警的
        return 0 if all(c["ok"] for s in sections for c in s["checks"]) else 1

    print("🔍  XKB Pipeline Health Check")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    all_ok = True
    for s in sections:
        print(fmt_checks(s))
        if any(not c["ok"] for c in s["checks"]):
            all_ok = False

    print()
    if all_ok:
        print(f"{OK}  All checks passed.")
    else:
        print(f"{FAIL}  Some checks failed — review above.")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
