#!/usr/bin/env python3
"""
migrate_schema.py — XKB schema migration (Layer 1 + Layer 2)

Layer 1: search_index.json
  - Backfill source_type (infer from path/filename)
  - Backfill enriched: true for items with summary

Layer 2: card frontmatter
  - Strip <think>...</think> prefix from card content
  - Normalize type: x-knowledge-card → knowledge-card
  - Add source_type if missing
  - Add sensitivity: public if missing

Usage:
    python3 migrate_schema.py --dry-run
    python3 migrate_schema.py
"""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path

WORKSPACE = Path(os.getenv(
    "OPENCLAW_WORKSPACE",
    os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))
))
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths
import xkb_index

CARDS_DIR = xkb_paths.CARDS_DIR
INDEX_FILE = xkb_paths.INDEX_FILE


def infer_source_type(stem: str) -> str:
    if stem.startswith("local-pmc") or (stem.startswith("local-") and not stem.startswith("local-paper")):
        return "local-paper"
    if stem.startswith("github_fork-"):
        return "github_fork"
    if stem.startswith("github_star-"):
        return "github_star"
    if stem.startswith("youtube-"):
        return "youtube"
    if stem.startswith("legacy-"):
        return "x-bookmark"
    if re.fullmatch(r"\d{15,20}", stem):
        return "x-bookmark"
    return "local"


def strip_think(text: str) -> str:
    """Remove <think>...</think> block from the start of a card."""
    return re.sub(r"^\s*<think>[\s\S]*?</think>\s*", "", text).lstrip()


def fix_frontmatter(text: str, stem: str) -> tuple[str, list[str]]:
    """Normalize frontmatter. Returns (new_text, list_of_changes)."""
    changes = []

    # Strip <think> prefix first
    if re.match(r"\s*<think>", text):
        text = strip_think(text)
        changes.append("stripped <think> block")

    # Check frontmatter exists
    m = re.match(r"^---\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
    if not m:
        # No valid frontmatter — can't fix automatically, skip
        return text, ["SKIP: no valid frontmatter after stripping"]

    fm_block = m.group(1)
    body = m.group(2)

    # Normalize type field
    if "type: x-knowledge-card" in fm_block:
        fm_block = fm_block.replace("type: x-knowledge-card", "type: knowledge-card")
        changes.append("type: x-knowledge-card → knowledge-card")

    # Add source_type if missing
    if not re.search(r"^source_type:", fm_block, re.MULTILINE):
        st = infer_source_type(stem)
        # Try to insert after 'type:' line
        fm_block = re.sub(
            r"(^type:.*$)",
            lambda m2: m2.group(1) + f"\nsource_type: {st}",
            fm_block, count=1, flags=re.MULTILINE
        )
        changes.append(f"added source_type: {st}")

    # Add sensitivity if missing
    if not re.search(r"^sensitivity:", fm_block, re.MULTILINE):
        fm_block = fm_block.rstrip() + "\nsensitivity: public"
        changes.append("added sensitivity: public")

    new_text = f"---\n{fm_block}\n---\n{body}"
    return new_text, changes


def migrate_cards(dry_run: bool) -> dict:
    stats = {"fixed": 0, "skipped": 0, "changes": {}}
    for card_path in xkb_paths.card_files():
        stem = card_path.stem
        text = card_path.read_text(encoding="utf-8", errors="ignore")
        new_text, changes = fix_frontmatter(text, stem)

        real_changes = [c for c in changes if not c.startswith("SKIP")]
        if not real_changes:
            continue

        stats["changes"][card_path.name] = real_changes
        if not dry_run:
            card_path.write_text(new_text, encoding="utf-8")
        stats["fixed"] += 1

    return stats


def migrate_index(dry_run: bool) -> dict:
    if not INDEX_FILE.exists():
        return {"error": "index not found"}

    data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    items = data.get("items", [])
    changed = 0

    for item in items:
        item_changed = False

        # Infer source_type from relative_path or path
        if not item.get("source_type"):
            rp = item.get("relative_path", item.get("path", ""))
            stem = Path(rp).stem
            st = infer_source_type(stem)
            item["source_type"] = st
            item_changed = True

        # Set enriched based on whether summary exists
        if "enriched" not in item:
            item["enriched"] = bool(item.get("summary", "").strip())
            item_changed = True

        if item_changed:
            changed += 1

    # Layer 1 原本補的 source_type / enriched，builder 現在直接從檔案解析，
    # 所以這裡不需要也不應該自己寫索引。重建交給 main() 在修完卡片之後做一次：
    # migrate_index 只動記憶體、不碰檔案，而增量重建只重解析 mtime 變過的檔案
    # ——在這裡呼叫 rebuild() 是個什麼都不會發生的動作，而輸出照樣印 updated: N。

    return {"total": len(items), "updated": changed}


def main():
    parser = argparse.ArgumentParser(description="XKB schema migration")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"=== XKB Schema Migration [{mode}] ===\n")

    print("Layer 1: search_index.json")
    idx_result = migrate_index(args.dry_run)
    print(f"  total items : {idx_result.get('total', 0)}")
    print(f"  updated     : {idx_result.get('updated', 0)}")

    print("\nLayer 2: card frontmatter")
    card_result = migrate_cards(args.dry_run)
    print(f"  cards fixed : {card_result['fixed']}")

    # Show sample of changes
    changes = card_result["changes"]
    sample = list(changes.items())[:10]
    for name, ch in sample:
        print(f"  {name}: {', '.join(ch)}")
    if len(changes) > 10:
        print(f"  ... and {len(changes) - 10} more")

    if args.dry_run:
        print("\n[dry-run] Nothing was written. Run without --dry-run to apply.")
        return 0

    # 修完卡片才重建，而且要整份重建。
    #
    # 遷移改的是 frontmatter，而增量重建靠 mtime/size 判斷要不要重新解析——
    # 照理說改過的檔案會被認出來，但 Layer 1 完全沒碰檔案，所以原本在
    # migrate_index 裡呼叫的那次增量重建是個什麼都不會發生的動作，而輸出
    # 照樣印 updated: N。順序也是錯的：那次重建發生在卡片被修好之前。
    if card_result["fixed"] or idx_result.get("updated"):
        print("\nRebuilding the search index from the migrated files...")
        if not xkb_index.rebuild(incremental=False):
            print("  ⚠️  索引重建失敗——遷移已寫進卡片，但索引還是舊的。")
            return 1

    print("\nDone. Run build_vector_index.py --incremental to refresh embeddings.")
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
