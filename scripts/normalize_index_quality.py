#!/usr/bin/env python3
"""
Normalize / exclude low-quality search index entries without touching source markdown.

Phase 1 rules:
- exclude entries with invalid source URLs
- exclude entries with date-slug titles + low-signal summaries
- exclude entries with tweet/numeric titles + low-signal summaries
- preserve all data by marking entries instead of deleting source files

Usage:
    python3 scripts/normalize_index_quality.py
    python3 scripts/normalize_index_quality.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths
import xkb_frontmatter


def _card_path(item: dict) -> Path | None:
    """索引列指向的那個檔案。找不到就回 None——標記不上要說得出來。"""
    raw = (item.get("path") or "").strip()
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute() and candidate.exists():
            return candidate
    rel = (item.get("relative_path") or "").strip()
    if not rel:
        return None
    for base in (xkb_paths.WORKSPACE, xkb_paths.BOOKMARKS_DIR):
        candidate = base / rel
        if candidate.exists():
            return candidate
    return None

WORKSPACE = xkb_paths.WORKSPACE
BOOKMARKS_DIR = xkb_paths.BOOKMARKS_DIR
INDEX_FILE = xkb_paths.INDEX_FILE

LOW_SIGNAL_SUMMARIES = {"", "（待整理）", "待整理", "todo", "tbd", "n/a"}


def clean_summary(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"^一句話摘要\s*", "", text)
    text = re.sub(r"^[-•]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_valid_source_url(url: str) -> bool:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    x_status = re.search(r"https?://(?:x|twitter)\.com/[^\s/]+/status/(\d{15,20})(?:\b|/|\?)", url)
    x_i_status = re.search(r"https?://x\.com/i/status/(\d{15,20})(?:\b|/|\?)", url)
    if ("x.com" in url or "twitter.com" in url) and not (x_status or x_i_status):
        return False
    return True


# 從網路抓來的東西才該有網址。本機擷取的出處是一個檔案路徑，那不是品質問題，
# 那就是它的出處——2026-09-10 實測，這條規則正要把兩張本機匯入的好卡片排除掉：
# 「every-app/open-seo — Semrush/Ahrefs 開源替代方案」與那本 Obsidian 的書。
WEB_SOURCED_TYPES = {"x-bookmark", "github_star", "github_fork", "youtube"}


def exclusion_reasons(item: dict[str, Any]) -> list[str]:
    title = (item.get("title") or "").strip()
    summary = clean_summary(item.get("summary") or "")
    source_url = (item.get("source_url") or "").strip()
    source_type = (item.get("source_type") or "").strip()

    reasons: list[str] = []

    if (source_url and source_type in WEB_SOURCED_TYPES
            and not is_valid_source_url(source_url)):
        reasons.append("invalid_source_url")

    if re.match(r"^\d{4}-\d{2}-\d{2}-", title) and summary in LOW_SIGNAL_SUMMARIES:
        reasons.append("date_slug_low_signal")

    if re.fullmatch(r"\d{15,20}", title) and summary in LOW_SIGNAL_SUMMARIES:
        reasons.append("numeric_title_low_signal")

    if re.fullmatch(r"tweet\s+\d{15,20}", title.lower()) and summary in LOW_SIGNAL_SUMMARIES:
        reasons.append("tweet_numeric_low_signal")

    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize index quality")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    is_dict = isinstance(raw, dict)

    changed = 0
    excluded = 0
    examples: list[tuple[str, str, list[str]]] = []
    unmarkable: list[str] = []

    for item in items:
        reasons = exclusion_reasons(item)
        rel_path = item.get("relative_path") or item.get("path") or ""
        current_excluded = bool(item.get("excluded"))
        current_reasons = item.get("exclude_reasons") or []

        if reasons:
            merged = sorted(set(current_reasons) | set(reasons))
            # 標在檔案上，不是索引列上。索引是衍生物，重建一次就把這個決定
            # 洗掉——實測正式索引 1680 筆裡帶 excluded 的是 0 筆。
            card = _card_path(item)
            if card is None:
                unmarkable.append(rel_path)
            elif xkb_frontmatter.mark_excluded(
                    card, "; ".join(merged), dry_run=args.dry_run):
                changed += 1
            excluded += 1
            if len(examples) < 20:
                examples.append((rel_path, item.get("title", ""), merged))

    print(f"總項目數：{len(items)}")
    print(f"標記 excluded：{excluded}")
    print(f"實際變更：{changed}")
    print("\n範例：")
    for rel_path, title, reasons in examples:
        print(f"- {rel_path}")
        print(f"  title: {title}")
        print(f"  reasons: {', '.join(reasons)}")

    if unmarkable:
        # 標記不上跟標記了不一樣。安靜跳過的話這批會永遠標不掉，而輸出
        # 看起來一切正常。
        print(f"標記不上（檔案不存在或沒有 frontmatter）：{len(unmarkable)}")
        for rel in unmarkable[:5]:
            print(f"  - {rel}")

    # 這支腳本不再寫索引。決定寫在卡片檔案上，索引由 build_search_index.sh
    # 從檔案重算——那是唯一寫索引的地方。
    if not args.dry_run and changed > 0:
        print(f"已標記 {changed} 張卡片。索引重建後生效：")
        print("  bash scripts/build_search_index.sh --incremental")

    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
