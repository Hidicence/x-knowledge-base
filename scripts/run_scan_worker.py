#!/usr/bin/env python3
"""
Scan-mode bookmark enrichment worker.

Instead of reading from tiege-queue.json, directly scans memory/bookmarks/
for files that don't yet have a corresponding enriched card in memory/cards/.

Handles:
  - Named files (01-clawpal.md) that have tweet_id inside frontmatter
  - Files with truncated/non-standard tweet IDs
  - Any bookmark without a matching memory/cards/ entry

Usage:
    python3 scripts/run_scan_worker.py --limit 20
    python3 scripts/run_scan_worker.py --dry-run
    python3 scripts/run_scan_worker.py --limit 50 --worker pipeline
    python3 scripts/run_scan_worker.py --category 01-openclaw-workflows --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(WORKSPACE / "memory" / "cards")))

# ── LLM config (OpenAI-compatible, configurable via env vars) ──────────────────
# Default: MiniMax. Override with LLM_API_URL + LLM_API_KEY + LLM_MODEL
LLM_API_URL = os.getenv("LLM_API_URL", "https://api.minimaxi.chat/v1/chat/completions")
LLM_MODEL   = os.getenv("LLM_MODEL",   "MiniMax-M2.5")


def _get_api_key() -> str:
    for env_key in ("LLM_API_KEY", "MINIMAX_API_KEY"):
        key = os.environ.get(env_key, "")
        if key:
            return key
    config_path = Path(os.environ.get("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            env = config.get("env", {})
            return env.get("LLM_API_KEY") or env.get("MINIMAX_API_KEY") or ""
        except Exception:
            pass
    return ""


SYSTEM_PROMPT = """You are a knowledge card generator for a personal learning base. Given the raw content of a single X/Twitter bookmark and optionally a list of related existing cards, output one structured knowledge card in Traditional Chinese.

Strict rules:
- Process ONLY this one bookmark
- Leave sections empty with "無" if uncertain — never hallucinate
- If content is a login page, 404, or homepage noise, output exactly: SKIPPED
- Do NOT use the reader's personal name in any section

Output format (Markdown):
---
id: {id}
type: x-knowledge-card
source_type: x-bookmark
source_url: {source_url}
author: (infer from content, leave blank if unsure)
created_at: (infer from content, leave blank if unsure)
category: {category}
tags: [tag1, tag2, tag3]
confidence: medium
---

# <title>

## 1. 核心問題與結論
- **提問**：這篇試圖解答什麼問題？（一句話）
- **結論**：作者給出的答案是什麼？（一句話）
- **可信度說明**：這個結論有沒有數據/實驗/引用支撐，還是只是個人意見？

## 2. Claim 等級
- **等級**：[Attested | Scholarship | Inference]
  - Attested：原文直接引用、有具體數據或實驗結果
  - Scholarship：作者/領域的分析觀點，有明確來源依據
  - Inference：LLM 推論、個人猜測、尚未驗證的假設
- **主要主張**：（一句話說明被標記的核心主張）
- **依據**：（為什麼是這個等級？有什麼支持或限制？）

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
{related_cards_section}

## 7. 雙語摘要（搜尋索引用）
ZH: <20-40字繁體中文摘要，說明核心發現>
EN: <15-30 word English summary of the core finding>

## 8. 對使用者的價值
- 可追蹤的方向
- 可執行的應用場景
- 適合哪個專案或工作流程

## 9. 原始來源
- Tweet: {source_url}
- Links: (list URLs found in content)

Quality principles: conservative > hallucination, understanding > summary, structured > verbose"""


def _find_related_context(bookmark_content: str, top_k: int = 3) -> str:
    """Quick keyword search to find related existing cards for context injection."""
    index_path = WORKSPACE / "memory" / "bookmarks" / "search_index.json"
    if not index_path.exists():
        return ""
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        items = data.get("items", []) if isinstance(data, dict) else data
    except Exception:
        return ""

    stopwords = {"的", "了", "是", "在", "有", "和", "與", "就", "也", "都", "這", "那",
                 "this", "that", "with", "from", "have", "will", "for", "and", "the", "a"}

    raw_tokens = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", bookmark_content[:1000].lower())
    query_tokens: set[str] = set()
    for t in raw_tokens:
        if re.match(r"[\u4e00-\u9fff]", t):
            for i in range(len(t) - 1):
                query_tokens.add(t[i:i+2])
        else:
            query_tokens.add(t)
    query_tokens -= stopwords
    if not query_tokens:
        return ""

    scored = []
    for item in items:
        combined = " ".join([
            (item.get("title") or "").lower(),
            (item.get("summary") or "").lower(),
            " ".join(item.get("tags") or []).lower(),
        ])
        score = sum(1 for t in query_tokens if t in combined)
        if score > 1:
            scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    if not top:
        return "（知識庫中尚無明顯相關的已存 card）"

    lines = ["根據知識庫搜尋，以下是最相關的現有 cards，請分析這篇與它們的關係（補充/衝突/重複/延伸）："]
    for _, item in top:
        title = item.get("title", "")[:60]
        summary = (item.get("summary") or "")[:100]
        lines.append(f"- **{title}**：{summary}")
    lines.append("\n請分析：這篇和以上 cards 的關係是什麼？（補充新面向 / 與某篇結論衝突 / 與某篇重複 / 帶入新概念）")
    return "\n".join(lines)


def _call_llm(api_key: str, content: str, card_id: str, source_url: str, category: str, related_context: str = "") -> str:
    related_section = related_context if related_context else "（知識庫中尚無明顯相關的已存 card）"
    system = SYSTEM_PROMPT.format(
        id=card_id,
        source_url=source_url,
        category=category,
        related_cards_section=related_section,
    )
    user_msg = (
        f"Please process this bookmark:\n\nID: {card_id}\n"
        f"Source: {source_url}\nCategory: {category}\n\n"
        f"--- Raw content ---\n{content[:4000]}\n---\n\n"
        "Output the knowledge card. If content is low-value (login page/404/noise), output only: SKIPPED"
    )
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
        "max_tokens": 2500,
        "temperature": 0.3,
    })
    req = urllib.request.Request(
        LLM_API_URL,
        data=payload.encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content_blocks = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content_blocks, list):
        text = next((b["text"] for b in content_blocks if b.get("type") == "text"), "")
    else:
        text = content_blocks
    return text.strip()


# ── Scan-specific helpers ──────────────────────────────────────────────────────

def _extract_frontmatter_value(text: str, key: str) -> str:
    m = re.search(rf'^{re.escape(key)}:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _extract_status_id(value: str) -> str:
    if not value:
        return ""
    m = re.search(r"/status/(\d{15,20})", value)
    return m.group(1) if m else ""


def _build_legacy_card_id(filepath: Path) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", filepath.stem).strip("-").lower() or "untitled"
    return f"legacy-{slug}"


def _get_card_id(filepath: Path, content: str) -> str:
    """Determine a stable card ID for this bookmark file."""
    tweet_id = _extract_frontmatter_value(content, "tweet_id")
    if tweet_id and re.fullmatch(r"\d{15,20}", tweet_id):
        return tweet_id

    for field in ("source_url", "source"):
        url = _extract_frontmatter_value(content, field)
        status_id = _extract_status_id(url)
        if status_id:
            return status_id

    if re.fullmatch(r"\d{15,20}", filepath.stem):
        return filepath.stem

    m = re.match(r"^(\d{15,20})", filepath.stem)
    if m:
        return m.group(1)

    return _build_legacy_card_id(filepath)


def _get_source_url(content: str, card_id: str) -> str:
    for field in ("source_url", "source"):
        url = _extract_frontmatter_value(content, field)
        if url and url.startswith("http"):
            return url
    if re.fullmatch(r"\d{15,20}", card_id):
        return f"https://x.com/i/status/{card_id}"
    return ""


def _get_category(filepath: Path) -> str:
    try:
        parts = filepath.relative_to(BOOKMARKS_DIR).parts
        if len(parts) >= 2:
            return parts[0]
    except Exception:
        pass
    return ""


def scan_missing(limit: int, category_filter: str = "") -> list[tuple[Path, str, str, str, str]]:
    """Return list of (filepath, content, card_id, source_url, category) for unenriched files."""
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    existing_card_ids = {f.stem for f in CARDS_DIR.glob("*.md")}

    results = []
    skip_dirs = {"notebooklm_exports", "__pycache__", "youtube"}

    for md_file in sorted(BOOKMARKS_DIR.rglob("*.md")):
        if any(d in md_file.parts for d in skip_dirs):
            continue
        if md_file.name.startswith("."):
            continue

        content = md_file.read_text(encoding="utf-8", errors="ignore")
        card_id = _get_card_id(md_file, content)
        source_url = _get_source_url(content, card_id)
        category = _get_category(md_file)

        if category_filter and category_filter not in category:
            continue

        if card_id in existing_card_ids:
            continue

        results.append((md_file, content, card_id, source_url, category))
        if len(results) >= limit:
            break

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan-mode bookmark enrichment worker")
    parser.add_argument("--limit",    type=int, default=20,          help="Max items to process (default: 20)")
    parser.add_argument("--worker",   default="scan-worker",         help="Worker name")
    parser.add_argument("--dry-run",  action="store_true",           help="Simulate without API calls")
    parser.add_argument("--category", default="",                    help="Filter by category slug")
    args = parser.parse_args()

    api_key = "" if args.dry_run else _get_api_key()
    if not api_key and not args.dry_run:
        print("❌ LLM_API_KEY not found. Set env var or add to openclaw.json.")
        sys.exit(1)

    missing = scan_missing(args.limit, args.category)
    total_missing = len(scan_missing(9999, args.category))

    if not missing:
        print("✅ All bookmarks already enriched")
        return

    print(f"📋 Found {total_missing} unenriched bookmarks  |  Processing {len(missing)} [worker: {args.worker}]")
    if args.dry_run:
        print("   (dry-run — no API calls)")

    results = {"done": 0, "skipped": 0, "failed": 0}

    for filepath, content, card_id, source_url, category in missing:
        label = str(filepath.relative_to(BOOKMARKS_DIR))
        print(f"  → {label}", end="  ", flush=True)

        if args.dry_run:
            print(f"[dry-run: {card_id}]")
            results["done"] += 1
            continue

        try:
            related_ctx = _find_related_context(content)
            text = _call_llm(api_key, content, card_id, source_url, category, related_ctx)
            if not text:
                results["failed"] += 1
                print("✗ empty response")
                continue
            if re.match(r"^SKIPPED", text.strip(), re.IGNORECASE):
                results["skipped"] += 1
                print("⏭ skipped")
                continue

            card_path = CARDS_DIR / f"{card_id}.md"
            card_path.write_text(text, encoding="utf-8")
            results["done"] += 1
            print("✓ done")
        except Exception as exc:
            results["failed"] += 1
            print(f"✗ {exc}")

    remaining = len(scan_missing(9999, args.category))
    print(f"\n📊 done={results['done']}  skipped={results['skipped']}  failed={results['failed']}  remaining={remaining}")


if __name__ == "__main__":
    main()
