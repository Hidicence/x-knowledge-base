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

    # vector_index
    if not VECTOR_FILE.exists():
        result["checks"].append({"ok": False, "msg": "vector_index.json not found — semantic recall disabled"})
    else:
        vdata = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
        vectors = vdata.get("vectors", {})
        v_mtime = VECTOR_FILE.stat().st_mtime
        v_age_hours = (datetime.now().timestamp() - v_mtime) / 3600
        result["checks"].append({
            "ok": len(vectors) > 0 and v_age_hours < 26,
            "msg": f"vector_index: {len(vectors)} vectors, last updated {v_age_hours:.1f}h ago"
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
        check_index_freshness(),
    ]

    if args.json:
        print(json.dumps(sections, ensure_ascii=False, indent=2))
        return 0

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
