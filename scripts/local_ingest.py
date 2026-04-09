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
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
WORKSPACE_DIR = Path(os.getenv("OPENCLAW_WORKSPACE",
    os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
CARDS_DIR     = WORKSPACE_DIR / "memory" / "cards"
INDEX_FILE    = BOOKMARKS_DIR / "search_index.json"

# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_API_BASE   = os.getenv("LLM_API_URL", "https://api.minimax.io/anthropic")
LLM_MODEL      = os.getenv("LLM_MODEL", "MiniMax-M2.5")
_USE_ANTHROPIC = "anthropic" in LLM_API_BASE or "minimax" in LLM_API_BASE

CARD_CATEGORIES = [
    "ai-tools", "developer-tools", "workflows", "data",
    "startup", "design", "tech", "learning", "other"
]

MAX_CONTENT_CHARS = 3000   # truncate long files before sending to LLM
SUPPORTED_EXTS    = {".md", ".txt", ".markdown"}

CARD_PROMPT = """\
你是一個知識庫管理員，請根據以下本地文件內容生成一張知識卡片。

檔名: {filename}
內容:
{content}

請輸出以下格式（YAML frontmatter + Markdown）：

---
title: <文件標題，15-40字，繁體中文>
category: <從以下選一：{categories}>
tags: <3-5個標籤，逗號分隔，英文小寫>
source_type: local
---

## 📝 一句話摘要

<這份文件的核心內容，20-40字，繁體中文>

## 📝 English Summary

<Core content in English, 15-30 words>

## 📝 English Summary

<Core content in English, 15-30 words>

## 重點

- <重點1，15-25字>
- <重點2，15-25字>
- <重點3，15-25字>
"""


def load_env_key() -> str:
    cfg_path = Path(os.getenv("OPENCLAW_JSON",
        str(Path.home() / ".openclaw" / "openclaw.json")))
    try:
        cfg = json.loads(cfg_path.read_text())
        env = cfg.get("env", {})
        return (env.get("LLM_API_KEY") or env.get("MINIMAX_API_KEY") or
                os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY") or "")
    except Exception:
        return os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY") or ""


def llm_call(prompt: str, api_key: str) -> str:
    if _USE_ANTHROPIC:
        payload = json.dumps({
            "model": LLM_MODEL,
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            f"{LLM_API_BASE}/v1/messages", data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return next(item["text"] for item in data["content"] if item.get("type") == "text").strip()
    else:
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1200,
            "temperature": 0.3,
        }).encode()
        req = urllib.request.Request(
            LLM_API_BASE, data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()


def load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"version": "1.1", "items": []}


def save_index(data: dict) -> None:
    INDEX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def file_hash(path: Path) -> str:
    """Short hash to detect duplicate ingest of same file."""
    content = path.read_bytes()
    return hashlib.md5(content).hexdigest()[:8]


def card_id_for_file(path: Path) -> str:
    stem = re.sub(r"[^a-zA-Z0-9\-]", "-", path.stem.lower())
    stem = re.sub(r"-+", "-", stem).strip("-")[:40]
    h = file_hash(path)
    return f"local-{stem}-{h}"


def extract_frontmatter(card: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---", card, re.DOTALL)
    if not m:
        return {}
    result = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


def extract_summary(card: str) -> str:
    zh = re.search(r"##\s*📝 一句話摘要\s*\n+(.+?)(\n##|\Z)", card, re.DOTALL)
    en = re.search(r"##\s*📝 English Summary\s*\n+(.+?)(\n##|\Z)", card, re.DOTALL)
    parts = [x.group(1).strip() for x in [zh, en] if x]
    if parts:
        return " | ".join(parts)
    lines = [l.strip() for l in card.splitlines()
             if l.strip() and not l.startswith("#") and not l.startswith("---")]
    return lines[0] if lines else ""


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
    force_category: str | None,
    extra_tags: list[str],
    dry_run: bool,
) -> dict | None:
    card_id = card_id_for_file(path)
    dedup_key = f"local|{card_id}"

    if dedup_key in existing_keys:
        print(f"  [SKIP] 已存在：{path.name}")
        return None

    content = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        print(f"  [SKIP] 空檔案：{path.name}")
        return None

    # Truncate for LLM
    truncated = content[:MAX_CONTENT_CHARS]
    if len(content) > MAX_CONTENT_CHARS:
        truncated += f"\n\n[內容截斷，原始長度 {len(content)} 字元]"

    print(f"  📄 {path.name} ({len(content)} 字元)")

    if dry_run:
        return None

    prompt = CARD_PROMPT.format(
        filename=path.name,
        content=truncated,
        categories=", ".join(CARD_CATEGORIES),
    )
    try:
        card_content = llm_call(prompt, api_key)
    except Exception as e:
        print(f"     ❌ LLM 失敗：{e}")
        return None

    # Inject id
    if "---\n" in card_content and "id:" not in card_content:
        card_content = card_content.replace("---\n", f"---\nid: {card_id}\n", 1)

    # Override category if specified
    if force_category:
        card_content = re.sub(
            r"^category:.*$", f"category: {force_category}",
            card_content, flags=re.MULTILINE
        )

    # Save card
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    card_path = CARDS_DIR / f"{card_id}.md"
    card_path.write_text(card_content, encoding="utf-8")
    print(f"     💾 cards/{card_id}.md")

    # Build index item
    fm = extract_frontmatter(card_content)
    summary = extract_summary(card_content)
    tags_raw = fm.get("tags", "")
    tags = list({t.strip() for t in tags_raw.split(",") if t.strip()})
    tags.extend(extra_tags)
    if force_category:
        fm["category"] = force_category

    return {
        "path": str(card_path),
        "relative_path": f"cards/{card_id}.md",
        "title": fm.get("title", path.stem),
        "category": fm.get("category", "other"),
        "tags": list(set(tags)),
        "summary": summary,
        "source_url": "",
        "source_type": "local",
        "source_file": str(path),
        "searchable": f"{path.name} {fm.get('title','')} {summary} {' '.join(tags)}",
        "mtime": datetime.now(timezone.utc).isoformat(),
        "size": card_path.stat().st_size,
        "enriched": True,
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

    api_key = "" if args.dry_run else load_env_key()
    if not api_key and not args.dry_run:
        print("[ERROR] 找不到 LLM_API_KEY", file=sys.stderr)
        return 1

    input_path = Path(args.path)
    files = collect_files(input_path)
    if not files:
        print("沒有找到可匯入的檔案。")
        return 0

    if args.limit:
        files = files[: args.limit]

    print(f"📂 找到 {len(files)} 個檔案")

    index_data = load_index()
    existing_keys = {
        f"local|{item.get('relative_path','').split('/')[-1].replace('.md','')}"
        for item in index_data.get("items", [])
        if item.get("source_type") == "local"
    }

    new_items: list[dict] = []
    for path in files:
        result = process_file(
            path, api_key, existing_keys,
            args.category, args.tags, args.dry_run
        )
        if result:
            new_items.append(result)

    if new_items and not args.dry_run:
        index_data["items"].extend(new_items)
        save_index(index_data)
        print(f"\n✅ 完成：新增 {len(new_items)} 張知識卡片")
        print("💡 下一步：python3 scripts/sync_cards_to_wiki.py --apply --limit 20")
    elif args.dry_run:
        print(f"\n（dry-run 模式，未寫入任何卡片）")
    else:
        print(f"\n✅ 完成：無新增卡片（全部已存在）")

    return 2 if new_items and not args.dry_run else 0


if __name__ == "__main__":
    raise SystemExit(main())
