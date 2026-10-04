#!/usr/bin/env python3
"""
local_ingest.py

將本地 markdown / txt 檔案（或整個目錄）轉成 XKB 知識卡片，
加入 search_index.json，供後續 sync_cards_to_wiki 使用。

這是 quickstart 和 demo mode 的共用 ingest 底層。

Usage:
    python3 scripts/local_ingest.py path/to/notes/        # 整個目錄
    python3 scripts/local_ingest.py path/to/file.md       # 單一檔案
    python3 scripts/local_ingest.py notes/ --dry-run      # 預覽不寫入
    python3 scripts/local_ingest.py notes/ --tag personal --category learning
    python3 scripts/local_ingest.py notes/ --limit 20     # 最多 20 個檔案

Exit codes:
    0 — success, 0 new cards
    1 — error
    2 — success, >= 1 new cards added
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gbrain_publish import PublicationBatch
import xkb_frontmatter
from _card_prompt import gbrain_put as _gbrain_put
from _card_prompt import (
    build_prompt, condense_long_content, extract_summary, find_related_context,
    llm_call,
)
from xkb_frontmatter import parse as extract_frontmatter
from category_classifier import classify_content, apply_category
import xkb_paths

# ── Paths ──────────────────────────────────────────────────────────────────────
CARDS_DIR = xkb_paths.CARDS_DIR
INDEX_FILE = xkb_paths.INDEX_FILE

SUPPORTED_EXTS = {".md", ".txt", ".markdown"}


def load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"version": "1.1", "items": []}


def file_hash(path: Path) -> str:
    """Short hash to detect duplicate ingest of same file."""
    content = path.read_bytes()
    return hashlib.md5(content).hexdigest()[:8]


def card_id_for_file(path: Path) -> str:
    stem = re.sub(r"[^a-zA-Z0-9\-]", "-", path.stem.lower())
    stem = re.sub(r"-+", "-", stem).strip("-")[:40]
    h = file_hash(path)
    return f"local-{stem}-{h}"


def pmc_url_from_filename(filename: str) -> str:
    """Extract PMC ID from filename and return NCBI URL."""
    m = re.match(r"(PMC\d+)", filename, re.IGNORECASE)
    if m:
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{m.group(1)}/"
    return ""


def collect_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() in SUPPORTED_EXTS:
            return [input_path]
        print(f"[WARN] 不支援的檔案類型：{input_path.suffix}", file=sys.stderr)
        return []
    if input_path.is_dir():
        files = []
        for ext in SUPPORTED_EXTS:
            files.extend(input_path.rglob(f"*{ext}"))
        return sorted(files)
    print(f"[ERROR] 路徑不存在：{input_path}", file=sys.stderr)
    return []


def process_file(
    path: Path,
    api_key: str,
    existing_keys: set,
    existing_items: list[dict],
    force_category: str | None,
    extra_tags: list[str],
    dry_run: bool,
    publication=None,
) -> dict | None:
    card_id = card_id_for_file(path)
    dedup_key = f"local|{card_id}"

    saved = CARDS_DIR / f"{card_id}.md"
    if saved.exists() and not dry_run:
        if publication is not None:
            publication.existing(saved, card_id)
            return None
        _gbrain_put(saved, card_id)
        text = saved.read_text(encoding="utf-8")
        return {"title": extract_frontmatter(text).get("title", path.stem),
                "tags": [], "summary": extract_summary(text)}
    if dedup_key in existing_keys:
        print(f"  [SKIP] 已存在：{path.name}")
        return None

    content = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        print(f"  [SKIP] 空檔案：{path.name}")
        return None

    print(f"  {path.name} ({len(content)} characters)")
    if dry_run:
        return None

    truncated = condense_long_content(content, verbose=True)

    source_url = pmc_url_from_filename(path.name)
    category = force_category or classify_content(
        content, source_type="local-paper", current_category="research"
    )["category"]
    related_ctx = find_related_context(content, existing_items)
    prompt = build_prompt(
        content=f"Filename: {path.name}\n\n{truncated}",
        card_id=card_id,
        source_type="local-paper",
        source_url=source_url or str(path),
        category=category,
        related_context=related_ctx,
    )
    try:
        card_content = llm_call(prompt, api_key)
    except Exception as e:
        print(f"     ❌ LLM 失敗：{e}")
        return None

    # Ensure id in frontmatter
    if "---\n" in card_content and "id:" not in card_content:
        card_content = card_content.replace("---\n", f"---\nid: {card_id}\n", 1)

    card_content = apply_category(card_content, category)
    if extra_tags:
        # Preserve the original YAML elements, including quoted commas. Only
        # encode the new values; splitting/re-encoding would change existing tags.
        tags_raw = (xkb_frontmatter.get(card_content, "tags") or "").strip()
        if tags_raw.startswith("[") and tags_raw.endswith("]"):
            tags_raw = tags_raw[1:-1].strip()
        added = ", ".join(json.dumps(tag, ensure_ascii=False) for tag in dict.fromkeys(extra_tags))
        separator = ", " if tags_raw else ""
        card_content = xkb_frontmatter.set_field(
            card_content, "tags", f"[{tags_raw}{separator}{added}]"
        )

    # Save card
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    card_path = CARDS_DIR / f"{card_id}.md"
    card_path.write_text(card_content, encoding="utf-8")
    if publication is None:
        _gbrain_put(card_path, card_id)
    else:
        publication.publish(card_path, card_id, generated=True)
    print(f"     💾 cards/{card_id}.md")

    # Context for subsequent cards; the search index is derived from the files.
    fm = extract_frontmatter(card_content)
    return {
        "title": fm.get("title", path.stem),
        "tags": [t.strip().strip("\"'") for t in fm.get("tags", "").strip("[]").split(",") if t.strip()],
        "summary": extract_summary(card_content),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest local markdown/txt files into XKB knowledge cards")
    parser.add_argument("path", help="檔案或目錄路徑")
    parser.add_argument("--dry-run",   action="store_true", help="只列出檔案，不生成卡片")
    parser.add_argument("--limit",     type=int, default=0, help="最多處理 N 個檔案（0=全部）")
    parser.add_argument("--category",  help="覆蓋 LLM 分類（強制指定）")
    parser.add_argument("--tag",       dest="tags", action="append", default=[],
                        help="額外標籤（可多次使用）")
    args = parser.parse_args()

    api_key = ""  # auth handled by _llm.py via openclaw CLI

    input_path = Path(args.path)
    files = collect_files(input_path)
    if not files:
        print("沒有找到可匯入的檔案。")
        return 0


    print(f"📂 找到 {len(files)} 個檔案")

    index_data = load_index()
    existing_items = index_data.get("items", [])
    existing_keys = {
        f"local|{item.get('relative_path','').split('/')[-1].replace('.md','')}"
        for item in existing_items
        if item.get("source_type") in ("local", "local-paper")
    }

    batch = PublicationBatch(_gbrain_put)
    if not args.dry_run and not batch.ready():
        return 1
    attempted = 0
    new_items: list[dict] = []
    for path in files:
        if batch.blocked:
            break
        saved = CARDS_DIR / f"{card_id_for_file(path)}.md"
        if not args.dry_run and batch.existing(saved, card_id_for_file(path)):
            continue
        if args.limit and attempted >= args.limit:
            continue
        if f"local|{card_id_for_file(path)}" in existing_keys:
            continue
        attempted += 1
        result = process_file(
            path, api_key, existing_keys, existing_items,
            args.category, args.tags, args.dry_run, batch
        )
        if result:
            new_items.append(result)
            existing_items.append(result)
            existing_keys.add(f"local|{card_id_for_file(path)}")

    if not args.dry_run and not batch.finish():
        return 1
    if new_items and not args.dry_run:
        print(f"\n✅ 完成：新增 {len(new_items)} 張知識卡片")
        print("💡 下一步：python3 scripts/sync_cards_to_wiki.py --apply --limit 20")
    elif args.dry_run:
        print(f"\n（dry-run 模式，未寫入任何卡片）")
    else:
        print(f"\n✅ 完成：無新增卡片（全部已存在）")

    return 2 if new_items and not args.dry_run else 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
