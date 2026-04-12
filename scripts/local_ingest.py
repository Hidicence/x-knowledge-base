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
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
WORKSPACE_DIR = Path(os.getenv("OPENCLAW_WORKSPACE",
    os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
CARDS_DIR     = WORKSPACE_DIR / "memory" / "cards"
INDEX_FILE    = BOOKMARKS_DIR / "search_index.json"

# ── Unified LLM helper ────────────────────────────────────────────────────────
_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR / "scripts"))
from _llm import call as _llm_backend

CARD_CATEGORIES = [
    "ai-tools", "developer-tools", "workflows", "data",
    "startup", "design", "tech", "learning", "research", "other"
]

MAX_CONTENT_CHARS = 4000   # truncate long files before sending to LLM
SUPPORTED_EXTS    = {".md", ".txt", ".markdown"}

SYSTEM_PROMPT = """\
You are a knowledge card generator for a personal learning base. \
Given the content of an academic paper or local document, output one structured knowledge card in Traditional Chinese.

Strict rules:
- Leave sections empty with "無" if uncertain — never hallucinate
- Use only information from the provided content
- Do NOT use the reader's personal name in any section

Quality principles: conservative > hallucination, understanding > summary, structured > verbose"""

CARD_PROMPT = """\
以下是一篇論文或本地文件的內容，請生成一張 9-section 知識卡片。

檔名: {filename}
來源: {source_url}
分類: {category}

內容:
{content}

{related_section}
請輸出以下格式（YAML frontmatter + Markdown）：

---
id: {card_id}
type: knowledge-card
source_type: local-paper
source_url: {source_url}
category: {category}
tags: [tag1, tag2, tag3]
sensitivity: public
confidence: medium
---

# <論文標題，保留英文原名或簡短繁體中文翻譯>

## 1. 核心問題與結論
- **提問**：這篇論文試圖解答什麼問題？（一句話）
- **結論**：作者給出的答案是什麼？（一句話）
- **可信度說明**：這個結論有沒有數據/實驗/引用支撐？

## 2. Claim 等級
- **等級**：[Attested | Scholarship | Inference]
  - Attested：原文直接引用、有具體數據或實驗結果
  - Scholarship：作者/領域的分析觀點，有明確來源依據
  - Inference：LLM 推論、尚未驗證的假設
- **主要主張**：（一句話說明核心主張）
- **依據**：（為什麼是這個等級？）

## 3. 關鍵論點
- 論點一
- 論點二
- 論點三

## 4. False Friends（如有）
這篇涉及哪些看起來像普通詞彙但有特定技術含義的術語？
- term: （術語名稱）
  common_misunderstanding: （多數人誤以為是...）
  actual_meaning: （在此領域/文章中實際指的是...）
如果沒有：無

## 5. 驚訝點
讀者讀完這篇後，可能感到意外或需要重新思考的是什麼？
（如果沒有明顯驚訝點，填「無」）

## 6. 與現有知識的關係
{related_cards_placeholder}

## 7. 雙語摘要（搜尋索引用）
ZH: <20-40字繁體中文摘要，說明核心發現>
EN: <15-30 word English summary of the core finding>

## 8. 對使用者的價值
- 可追蹤的研究方向
- 可執行的應用場景
- 與現有工作流程的關聯

## 9. 原始來源
- 來源: {source_url}
- Links: (list DOI or other URLs found in content)
"""


def load_env_key() -> str:
    return ""  # auth handled by _llm.py via openclaw CLI


def llm_call(prompt: str, api_key: str = "", system: str | None = None) -> str:
    return _llm_backend(system or "", prompt)


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
    # New format: ## 7. 雙語摘要 with ZH:/EN: lines
    bilingual = re.search(r"##\s+7\.\s*雙語摘要[^\n]*\n(.+?)(?=\n##|\Z)", card, re.DOTALL)
    if bilingual:
        block = bilingual.group(1)
        zh_m = re.search(r"^ZH:\s*(.+)$", block, re.MULTILINE)
        en_m = re.search(r"^EN:\s*(.+)$", block, re.MULTILINE)
        parts = [m.group(1).strip() for m in [zh_m, en_m] if m and m.group(1).strip()]
        if parts:
            return " | ".join(parts)
    # Legacy fallback
    zh = re.search(r"##\s*📝 一句話摘要\s*\n+(.+?)(\n##|\Z)", card, re.DOTALL)
    en = re.search(r"##\s*📝 English Summary\s*\n+(.+?)(\n##|\Z)", card, re.DOTALL)
    parts = [x.group(1).strip() for x in [zh, en] if x]
    if parts:
        return " | ".join(parts)
    lines = [l.strip() for l in card.splitlines()
             if l.strip() and not l.startswith("#") and not l.startswith("---")]
    return lines[0] if lines else ""


def pmc_url_from_filename(filename: str) -> str:
    """Extract PMC ID from filename and return NCBI URL."""
    m = re.match(r"(PMC\d+)", filename, re.IGNORECASE)
    if m:
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{m.group(1)}/"
    return ""


def find_related_context(content: str, existing_items: list[dict], top_k: int = 3) -> str:
    """Keyword search against existing index to find related cards for section 6."""
    stopwords = {"的", "了", "是", "在", "有", "和", "與", "就", "也", "都", "這", "那",
                 "this", "that", "with", "from", "have", "will", "for", "and", "the", "a"}
    raw_tokens = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", content[:1000].lower())
    query_tokens: set[str] = set()
    for t in raw_tokens:
        if re.match(r"[\u4e00-\u9fff]", t):
            for i in range(len(t) - 1):
                query_tokens.add(t[i:i+2])
        else:
            query_tokens.add(t)
    query_tokens -= stopwords
    if not query_tokens:
        return "（無相關既有卡片）"

    scored = []
    for item in existing_items:
        combined = " ".join([
            (item.get("title") or "").lower(),
            (item.get("summary") or "").lower(),
            " ".join(item.get("tags") or []).lower(),
        ])
        score = sum(1 for t in query_tokens if t in combined)
        if score > 0:
            scored.append((item, score))
    scored.sort(key=lambda x: x[1], reverse=True)

    if not scored:
        return "（無相關既有卡片）"

    lines = []
    for item, _ in scored[:top_k]:
        lines.append(f"- **{item.get('title', '(untitled)')}**：{(item.get('summary') or '')[:80]}")
    return "\n".join(lines)


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

    truncated = content[:MAX_CONTENT_CHARS]
    if len(content) > MAX_CONTENT_CHARS:
        truncated += f"\n\n[內容截斷，原始長度 {len(content)} 字元]"

    print(f"  📄 {path.name} ({len(content)} 字元)")

    if dry_run:
        return None

    source_url = pmc_url_from_filename(path.name)
    category = force_category or "research"
    related_ctx = find_related_context(content, existing_items)
    related_section = f"相關既有卡片（供 Section 6 參考）：\n{related_ctx}\n" if related_ctx else ""

    prompt = CARD_PROMPT.format(
        filename=path.name,
        content=truncated,
        card_id=card_id,
        source_url=source_url or str(path),
        category=category,
        related_section=related_section,
        related_cards_placeholder=related_ctx,
    )
    try:
        card_content = llm_call(prompt, api_key, system=SYSTEM_PROMPT)
    except Exception as e:
        print(f"     ❌ LLM 失敗：{e}")
        return None

    # Ensure id in frontmatter
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

    # Parse tags (supports both "a, b, c" and "[a, b, c]" formats)
    tags_raw = fm.get("tags", "").strip("[]")
    tags = list({t.strip() for t in tags_raw.split(",") if t.strip()})
    tags.extend(extra_tags)
    if force_category:
        fm["category"] = force_category

    return {
        "path": str(card_path),
        "relative_path": f"cards/{card_id}.md",
        "title": fm.get("title", path.stem),
        "category": fm.get("category", category),
        "tags": list(set(tags)),
        "summary": summary,
        "source_url": source_url,
        "source_type": "local-paper",
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

    api_key = ""  # auth handled by _llm.py via openclaw CLI

    input_path = Path(args.path)
    files = collect_files(input_path)
    if not files:
        print("沒有找到可匯入的檔案。")
        return 0

    if args.limit:
        files = files[: args.limit]

    print(f"📂 找到 {len(files)} 個檔案")

    index_data = load_index()
    existing_items = index_data.get("items", [])
    existing_keys = {
        f"local|{item.get('relative_path','').split('/')[-1].replace('.md','')}"
        for item in existing_items
        if item.get("source_type") in ("local", "local-paper")
    }

    new_items: list[dict] = []
    for path in files:
        result = process_file(
            path, api_key, existing_keys, existing_items,
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
