#!/usr/bin/env python3
"""
Bookmark enrichment worker — processes todo items from tiege-queue.json one at a time.

Usage:
    python3 scripts/run_bookmark_worker.py --limit 5
    python3 scripts/run_bookmark_worker.py --limit 1 --dry-run
    python3 scripts/run_bookmark_worker.py --worker myagent --limit 10
    python3 scripts/run_bookmark_worker.py --category 01-openclaw-workflows --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE / "memory" / "bookmarks")))
QUEUE_PATH = Path(os.getenv("XKB_QUEUE_PATH", str(WORKSPACE / "memory" / "x-knowledge-base" / "tiege-queue.json")))
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(WORKSPACE / "memory" / "cards")))

# ── gbrain integration ────────────────────────────────────────────────────────
_GBRAIN_DIR = Path(os.getenv("GBRAIN_DIR", str(Path.home() / "Desktop" / "gbrain")))
_GBRAIN_CLI = str(_GBRAIN_DIR / "src" / "cli.ts")
_GBRAIN_AVAILABLE = (_GBRAIN_DIR / "src" / "cli.ts").exists()

# Load GEMINI_API_KEY for gbrain embed
_GBRAIN_ENV: dict[str, str] = {**os.environ}
if not _GBRAIN_ENV.get("GEMINI_API_KEY"):
    try:
        import json as _j
        _cfg = Path.home() / ".openclaw" / "openclaw.json"
        if _cfg.exists():
            _k = _j.loads(_cfg.read_text(encoding="utf-8")).get("env", {}).get("GEMINI_API_KEY", "")
            if _k:
                _GBRAIN_ENV["GEMINI_API_KEY"] = _k
    except Exception:
        pass

# ── Unified LLM helper ────────────────────────────────────────────────────────
_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR / "scripts"))
from _llm import call as _llm_backend


def _get_api_key() -> str:
    return ""  # auth handled by _llm.py via openclaw CLI


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
sensitivity: public
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


def _gbrain_put(card_path: Path, slug: str) -> bool:
    """Push a card to gbrain database and trigger embedding. Returns True on success."""
    if not _GBRAIN_AVAILABLE:
        return False
    try:
        import subprocess as _sp
        content = card_path.read_text(encoding="utf-8")
        result = _sp.run(
            ["bun", "run", _GBRAIN_CLI, "put", slug],
            input=content,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_GBRAIN_ENV,
            cwd=str(_GBRAIN_DIR),
            timeout=30,
        )
        if result.returncode != 0:
            print(f"    [gbrain] put failed: {result.stderr.strip()[:120]}", flush=True)
            return False
        # Trigger embedding for this card
        _sp.run(
            ["bun", "run", _GBRAIN_CLI, "embed", slug],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_GBRAIN_ENV,
            cwd=str(_GBRAIN_DIR),
            timeout=60,
        )
        return True
    except Exception as e:
        print(f"    [gbrain] error: {e}", flush=True)
        return False


def _find_related_context(bookmark_content: str, top_k: int = 3) -> str:
    """Find related existing cards for context injection.
    Uses gbrain hybrid search if available, falls back to keyword search."""
    if _GBRAIN_AVAILABLE:
        return _find_related_context_gbrain(bookmark_content, top_k)
    return _find_related_context_keyword(bookmark_content, top_k)


def _find_related_context_gbrain(bookmark_content: str, top_k: int = 3) -> str:
    """Use gbrain hybrid search (RRF + Gemini) to find related cards."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from gbrain_recall import gbrain_query
        # Use first 300 chars of content as query
        query = bookmark_content[:300].replace("\n", " ").strip()
        results = gbrain_query(query, limit=top_k, no_expand=True)
        if not results:
            return "（知識庫中尚無明顯相關的已存 card）"
        lines = ["根據知識庫搜尋，以下是最相關的現有 cards，請分析這篇與它們的關係（補充/衝突/重複/延伸）："]
        for r in results:
            title = r.get("title", r.get("slug", ""))[:60]
            chunk = r.get("chunk_text", "")[:120].replace("\n", " ")
            lines.append(f"- **{title}**：{chunk}")
        lines.append("\n請分析：這篇和以上 cards 的關係是什麼？（補充新面向 / 與某篇結論衝突 / 與某篇重複 / 帶入新概念）")
        return "\n".join(lines)
    except Exception:
        return _find_related_context_keyword(bookmark_content, top_k)


def _find_related_context_keyword(bookmark_content: str, top_k: int = 3) -> str:
    """Fallback: keyword search using search_index.json."""
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


def _call_llm(api_key: str, bookmark_content: str, item: dict, related_context: str = "") -> str:
    related_section = related_context if related_context else "（知識庫中尚無明顯相關的已存 card）"
    system = SYSTEM_PROMPT.format(
        id=item["id"],
        source_url=item.get("source_url", ""),
        category=item.get("category", ""),
        related_cards_section=related_section,
    )
    user_msg = (
        f"Please process this bookmark:\n\nID: {item['id']}\n"
        f"Source: {item.get('source_url', '')}\nCategory: {item.get('category', '')}\n\n"
        f"--- Raw content ---\n{bookmark_content[:4000]}\n---\n\n"
        "Output the knowledge card. If content is low-value (login page/404/noise), output only: SKIPPED"
    )
    return _llm_backend(system, user_msg)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_queue() -> dict:
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def _save_queue(data: dict) -> None:
    QUEUE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_bookmark(source_path: str) -> str:
    full_path = WORKSPACE / source_path
    if full_path.exists():
        return full_path.read_text(encoding="utf-8", errors="ignore")
    return ""


def _process_item(item: dict, api_key: str, dry_run: bool) -> tuple[str, str]:
    """Returns (status, error). status: done | skipped | failed"""
    bookmark_content = _read_bookmark(item["source_path"])
    if not bookmark_content:
        return "failed", "bookmark file not found"

    if dry_run:
        preview = bookmark_content[:80].replace("\n", " ")
        print(f"    preview: {preview}...")
        return "done", ""

    related_ctx = _find_related_context(bookmark_content)
    text = _call_llm(api_key, bookmark_content, item, related_ctx)

    if not text:
        return "failed", "empty response from API"

    if re.match(r"^SKIPPED", text.strip(), re.IGNORECASE):
        return "skipped", ""

    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    card_path = CARDS_DIR / f"{item['id']}.md"
    card_path.write_text(text, encoding="utf-8")

    # Sync to gbrain (non-blocking: failure doesn't abort the queue item)
    if _GBRAIN_AVAILABLE:
        ok = _gbrain_put(card_path, item["id"])
        if ok:
            print("    [gbrain] ✓ synced", end="", flush=True)

    return "done", ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Bookmark enrichment worker")
    parser.add_argument("--limit",      type=int, default=5,     help="Max items to process (default: 5)")
    parser.add_argument("--worker",     default="worker",        help="Worker name recorded in queue")
    parser.add_argument("--dry-run",    action="store_true",     help="Simulate without calling API")
    parser.add_argument("--local-only", action="store_true",     help="Skip LLM enrichment — index bookmarks without sending content to any API")
    parser.add_argument("--category",   help="Filter by category slug (e.g. 01-openclaw-workflows)")
    args = parser.parse_args()

    if args.local_only:
        args.dry_run = True  # local-only implies dry-run (no API calls)

    api_key = ""  # auth handled by _llm.py via openclaw CLI

    data = _load_queue()
    items = data["items"]

    todo = [i for i in items if i["status"] == "todo"]
    if args.category:
        todo = [i for i in todo if args.category in i.get("category", "")]
    todo = todo[:args.limit]

    if not todo:
        print("✅ No todo items found")
        return

    total_todo = len([i for i in items if i["status"] == "todo"])
    print(f"📋 Processing {len(todo)}/{total_todo} todo items  [worker: {args.worker}]")
    if args.local_only:
        print("   (local-only mode — no content sent to external APIs)")
    elif args.dry_run:
        print("   (dry-run mode — no API calls)")
    else:
        print(f"   ⚠️  Bookmark content will be sent to LLM for enrichment (via openclaw).")

    id_to_indices: dict[str, list[int]] = defaultdict(list)
    for idx, it in enumerate(items):
        id_to_indices[it["id"]].append(idx)

    results = {"done": 0, "skipped": 0, "failed": 0}

    for item in todo:
        print(f"  → {item['id']}  [{item.get('category', '')}]", end="  ", flush=True)

        indices = id_to_indices[item["id"]]
        for idx in indices:
            data["items"][idx].update({"status": "processing", "worker": args.worker, "started_at": _now_iso()})
        _save_queue(data)

        try:
            status, error = _process_item(item, api_key, args.dry_run)

            title_update = {}
            if status == "done" and not args.dry_run:
                card_path = CARDS_DIR / f"{item['id']}.md"
                if card_path.exists():
                    m = re.search(r"^# (.+)$", card_path.read_text(encoding="utf-8"), re.MULTILINE)
                    if m:
                        title_update = {"title": m.group(1).strip()}

            for idx in indices:
                data["items"][idx].update({"status": status, "finished_at": _now_iso(), "error": error, **title_update})

            results[status] += 1
            print(f"✓ {status}")
        except Exception as exc:
            for idx in indices:
                data["items"][idx].update({"status": "failed", "error": str(exc)[:200], "finished_at": _now_iso()})
            results["failed"] += 1
            print(f"✗ failed: {exc}")

        _save_queue(data)

    remaining = len([i for i in data["items"] if i["status"] == "todo"])
    print(f"\n📊 done={results['done']}  skipped={results['skipped']}  failed={results['failed']}  remaining todo={remaining}")


if __name__ == "__main__":
    main()
