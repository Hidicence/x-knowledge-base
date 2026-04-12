#!/usr/bin/env python3
"""
Sync enriched cards from memory/cards/ back into search_index.json.

For each enriched card found in memory/cards/:
- Update path to point to memory/cards/[id].md
- Keep relative_path stable and truly relative
- Extract and update summary from "## 1. 核心摘要" section
- Extract and update tags from frontmatter
- Extract and update title from "# Title" line
- Set enriched: true
- Only add orphan cards when they have valid, stable metadata

Usage:
    python3 scripts/sync_enriched_index.py
    python3 scripts/sync_enriched_index.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(WORKSPACE / "memory" / "cards")))
INDEX_FILE = Path(os.getenv("INDEX_FILE", str(BOOKMARKS_DIR / "search_index.json")))


def _extract_summary(text: str) -> str:
    # ── New format: ## 7. 雙語摘要（搜尋索引用）with ZH:/EN: labels ──────────
    bilingual = re.search(r"##\s+7\.\s*雙語摘要[^\n]*\n(.+?)(?=\n##|\Z)", text, re.DOTALL)
    if bilingual:
        block = bilingual.group(1)
        zh_m = re.search(r"^(?:ZH|中文)[：:]\s*(.+)$", block, re.MULTILINE)
        en_m = re.search(r"^(?:EN|英文)[：:]\s*(.+)$", block, re.MULTILINE)
        parts = [m.group(1).strip() for m in [zh_m, en_m] if m and m.group(1).strip()]
        if parts:
            return " | ".join(parts)

    # ── Legacy formats (old worker cards, github/local ingest cards) ──────────
    patterns = [
        r"##\s+1\.\s*核心摘要\s*\n(.+?)(?=\n##|\Z)",
        r"##\s+1\.\s*English Summary\s*\n(.+?)(?=\n##|\Z)",
        r"##\s*📝\s*English Summary\s*\n(.+?)(?=\n##|\Z)",
    ]
    parts = []
    for pat in patterns:
        m = re.search(pat, text, re.DOTALL)
        if m:
            lines = [l.strip().lstrip("-").strip() for l in m.group(1).strip().splitlines() if l.strip()]
            parts.append(" ".join(lines)[:300])
    return " | ".join(parts) if parts else ""


def _extract_tags(text: str) -> list[str]:
    m = re.search(r"^tags:\s*\[(.+?)\]", text, re.MULTILINE)
    if m:
        return [t.strip().strip('"').strip("'") for t in m.group(1).split(",") if t.strip()]
    return []


def _extract_title(text: str) -> str:
    m = re.search(r"^# (.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _extract_frontmatter_value(text: str, key: str) -> str:
    m = re.search(r"^" + re.escape(key) + r":\s*[\"']?([^\"'\n]+)[\"']?\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _extract_status_id(value: str) -> str:
    if not value:
        return ""
    m = re.search(r"/status/(\d{15,20})", value)
    return m.group(1) if m else ""


def _looks_like_url(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _placeholder_text(value: str) -> bool:
    v = (value or "").strip().lower()
    return v in {"", "待整理", "待補充", "tbd", "todo", "n/a", "untitled", "(untitled)"}


def _relative_from_workspace(path: Path) -> str:
    return str(path.relative_to(WORKSPACE))


def _resolve_item_id(item: dict) -> str:
    source_url = item.get("source_url", "")
    source_id = _extract_status_id(source_url)
    if source_id:
        return source_id

    for key in ("relative_path", "path"):
        stem = Path(item.get(key, "")).stem
        if re.fullmatch(r"\d{15,20}", stem):
            return stem
        m = re.match(r"^(\d{15,20})", stem)
        if m:
            return m.group(1)
    return ""


def _resolve_card_id(text: str, stem: str) -> str:
    if re.fullmatch(r"\d{15,20}", stem):
        return stem
    m = re.match(r"^(\d{15,20})", stem)
    if m:
        return m.group(1)
    for key in ("id", "tweet_id", "source_url"):
        value = _extract_frontmatter_value(text, key)
        resolved = _extract_status_id(value) if key == "source_url" else (value if re.fullmatch(r"\d{15,20}", value) else "")
        if resolved:
            return resolved
    m = re.search(r"/status/(\d{15,20})", text)
    if m:
        return m.group(1)
    return ""


def _is_valid_orphan_card(text: str, card_id: str, source_url: str, title: str, summary: str) -> bool:
    _cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    if not _cleaned.startswith("---"):
        return False
    if _extract_frontmatter_value(text, "type") != "x-knowledge-card":
        return False
    source_type = _extract_frontmatter_value(text, "source_type")
    if source_type not in {"x-bookmark", "youtube"}:
        return False
    if not _looks_like_url(source_url):
        return False
    if source_type == "x-bookmark" and not _extract_status_id(source_url):
        return False
    if source_type == "youtube" and not card_id:
        return False
    if _placeholder_text(title) and _placeholder_text(summary):
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not CARDS_DIR.exists():
        print(f"❌ Cards dir not found: {CARDS_DIR}")
        sys.exit(1)
    if not INDEX_FILE.exists():
        print(f"❌ Index not found: {INDEX_FILE}")
        sys.exit(1)

    raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    is_dict = isinstance(raw, dict)

    id_to_idx: dict[str, int] = {}
    for idx, item in enumerate(items):
        item_id = _resolve_item_id(item)
        if item_id:
            id_to_idx[item_id] = idx

    card_files = sorted(CARDS_DIR.glob("*.md"))
    print(f"📁 {len(card_files)} enriched cards  |  {len(id_to_idx)} indexed items")

    updated = 0
    not_found_ids: list[str] = []

    for card_path in card_files:
        raw_card_id = card_path.stem
        text = card_path.read_text(encoding="utf-8", errors="ignore")
        resolved_id = _resolve_card_id(text, raw_card_id)
        if not resolved_id or resolved_id not in id_to_idx:
            not_found_ids.append(raw_card_id)
            continue

        idx = id_to_idx[resolved_id]
        item = items[idx]

        summary = _extract_summary(text)
        tags = _extract_tags(text)
        title = _extract_title(text)
        category = _extract_frontmatter_value(text, "category")
        new_path = str(card_path)
        new_relative_path = _relative_from_workspace(card_path)

        changes: dict = {}
        if item.get("path") != new_path:
            changes["path"] = new_path
        if item.get("relative_path") != new_relative_path:
            changes["relative_path"] = new_relative_path
        if summary and item.get("summary") != summary:
            changes["summary"] = summary
        if tags and item.get("tags") != tags:
            changes["tags"] = tags
        if title and item.get("title") != title:
            changes["title"] = title
        if category and item.get("category") != category:
            changes["category"] = category
        if not item.get("enriched"):
            changes["enriched"] = True
        # Clear stale excluded flag if item now has proper title and summary
        if item.get("excluded") and title and summary and not title.startswith("Tweet "):
            changes["excluded"] = False
            changes.pop("exclude_reasons", None)
            item.pop("exclude_reasons", None)

        if changes:
            if not args.dry_run:
                item.update(changes)
            updated += 1
            if args.dry_run:
                print(f"  [dry-run] {raw_card_id}: {list(changes.keys())}")

    print(f"✅ Updated: {updated}  |  Not in index: {len(not_found_ids)}")
    if not_found_ids:
        print(f"   (first 5 not found: {not_found_ids[:5]})")

    added = 0
    for raw_card_id in not_found_ids:
        card_path = CARDS_DIR / f"{raw_card_id}.md"
        if not card_path.exists():
            continue
        text = card_path.read_text(encoding="utf-8", errors="ignore")
        title = _extract_title(text)
        summary = _extract_summary(text)
        tags = _extract_tags(text)
        source_url = _extract_frontmatter_value(text, "source_url")
        category = _extract_frontmatter_value(text, "category")
        source_type = _extract_frontmatter_value(text, "source_type")
        resolved_id = _resolve_card_id(text, raw_card_id) or raw_card_id

        if not _is_valid_orphan_card(text, resolved_id, source_url, title, summary):
            continue

        card_abs = str(card_path)
        card_rel = _relative_from_workspace(card_path)
        new_entry = {
            "id": resolved_id,
            "path": card_abs,
            "relative_path": card_rel,
            "title": title,
            "summary": summary,
            "tags": tags,
            "source_url": source_url,
            "category": category,
            "source": source_type,
            "enriched": True,
        }
        if not args.dry_run:
            items.append(new_entry)
        added += 1
        if args.dry_run:
            print(f"  [dry-run] ADD {resolved_id}: {title[:60]}")

    if added:
        print(f"➕ Added {added} new index entries from orphaned cards")

    if not args.dry_run and (updated > 0 or added > 0):
        if is_dict:
            raw["items"] = items
        else:
            raw = items
        INDEX_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 Saved → {INDEX_FILE}")
        print()
        print("Next: rebuild vector index to pick up enriched summaries")
        print("  python3 scripts/build_vector_index.py --incremental")


if __name__ == "__main__":
    main()
