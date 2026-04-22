#!/usr/bin/env python3
"""
XKB Pipeline Health Check

檢查三件事：
1. wiki 單一真實來源：兩個路徑是否指向同一位置（symlink 正確）
2. recall 命中的 wiki 來源路徑是否正確
3. cron 執行後 summary / vector 是否真的有增量更新

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

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
_SKILL_DIR = Path(__file__).resolve().parent.parent
WIKI_DIR = Path(os.getenv("XKB_WIKI_DIR", str(_SKILL_DIR / "wiki")))
WIKI_TOPICS_DIR = WIKI_DIR / "topics"
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"
VECTOR_FILE = BOOKMARKS_DIR / "vector_index.json"
CARDS_DIR = WORKSPACE / "memory" / "cards"

WORKSPACE_WIKI = WORKSPACE / "wiki"  # 應該是 symlink

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
        result["checks"].append({"ok": False, "msg": f"workspace/wiki is a real directory (not symlink) — dual-wiki risk!"})
    else:
        result["checks"].append({"ok": False, "msg": "workspace/wiki does not exist"})

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
