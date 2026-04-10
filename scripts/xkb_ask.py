#!/usr/bin/env python3
"""
xkb_ask.py

直接提問知識庫，系統自動搜尋 wiki topics + knowledge cards，
用 LLM 生成帶引用的答案。

Usage:
    python3 scripts/xkb_ask.py "RAG 的替代方案是什麼？"
    python3 scripts/xkb_ask.py "OpenClaw 的 skill 怎麼設計" --format chat
    python3 scripts/xkb_ask.py "什麼是 GEO" --no-wiki
    python3 scripts/xkb_ask.py "agent memory" --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────────────────
WORKSPACE_DIR = Path(os.getenv("OPENCLAW_WORKSPACE",
    os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
INDEX_FILE     = BOOKMARKS_DIR / "search_index.json"
VECTOR_FILE    = BOOKMARKS_DIR / "vector_index.json"
WIKI_DIR       = WORKSPACE_DIR / "wiki"
TOPICS_DIR     = WIKI_DIR / "topics"

# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_API_BASE   = os.getenv("LLM_API_URL", "https://api.minimax.io/anthropic")
LLM_MODEL      = os.getenv("LLM_MODEL", "MiniMax-M2.5")
_USE_ANTHROPIC = "anthropic" in LLM_API_BASE or "minimax" in LLM_API_BASE

MAX_WIKI_CHARS  = 2000   # per topic excerpt fed to LLM
MAX_CARD_CHARS  = 300    # per card summary fed to LLM
MAX_WIKI_TOPICS = 3
MAX_CARDS       = 12

STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "和", "與", "就", "也", "都",
    "很", "想", "要", "用", "讓", "把", "跟", "對", "中", "上", "下", "嗎", "呢", "啊", "吧",
    "這", "那", "this", "that", "with", "from", "have", "will", "about", "into", "your",
    "their", "they", "them", "what", "when", "where", "which", "how", "why", "for", "and",
    "the", "are", "was", "were", "been",
}


# ── LLM call ──────────────────────────────────────────────────────────────────

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


def llm_call(prompt: str, api_key: str, max_tokens: int = 1000,
             system: str | None = None) -> str:
    if _USE_ANTHROPIC:
        body: dict[str, Any] = {
            "model": LLM_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{LLM_API_BASE}/v1/messages", data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        return next(item["text"] for item in data["content"] if item.get("type") == "text").strip()
    else:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }).encode()
        req = urllib.request.Request(
            LLM_API_BASE, data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()


# ── Tokenization ──────────────────────────────────────────────────────────────

def tokenize(text: str) -> list[str]:
    ascii_tokens = re.findall(r"[A-Za-z0-9_\-]{2,}", text.lower())
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text.lower())
    cjk_bigrams = []
    for run in cjk_runs:
        for i in range(len(run) - 1):
            cjk_bigrams.append(run[i:i+2])
    raw = ascii_tokens + cjk_bigrams
    return [t for t in raw if t not in STOPWORDS]


# ── Wiki topic search ─────────────────────────────────────────────────────────

def load_wiki_topics() -> list[dict]:
    """Load all wiki topic pages with title, slug, and content."""
    if not TOPICS_DIR.exists():
        return []
    topics = []
    for path in TOPICS_DIR.glob("*.md"):
        content = path.read_text(encoding="utf-8", errors="ignore")
        # Parse frontmatter title
        title_match = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
        title = title_match.group(1).strip().strip('"') if title_match else path.stem
        tags_match = re.search(r"^tags:\s*\[(.+)\]", content, re.MULTILINE)
        tags = [t.strip() for t in tags_match.group(1).split(",")] if tags_match else []
        # Strip frontmatter for searchable text
        body = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL).strip()
        topics.append({
            "slug": path.stem,
            "title": title,
            "tags": tags,
            "body": body,
            "path": str(path),
        })
    return topics


def score_wiki_topic(topic: dict, query_tokens: list[str], query: str) -> float:
    title_lower = topic["title"].lower()
    tags_lower  = " ".join(topic["tags"]).lower()
    body_lower  = topic["body"].lower()

    score = 0.0
    for token in query_tokens:
        if token in title_lower: score += 5.0
        if token in tags_lower:  score += 3.0
        if token in body_lower:  score += 1.0

    # Bonus for multi-word query phrase
    if query.lower() in title_lower: score += 8.0
    if query.lower() in body_lower:  score += 4.0
    return score


def search_wiki_topics(query: str, limit: int = MAX_WIKI_TOPICS) -> list[dict]:
    topics = load_wiki_topics()
    if not topics:
        return []
    tokens = tokenize(query)
    scored = [(t, score_wiki_topic(t, tokens, query)) for t in topics]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, s in scored[:limit] if s > 0]


def excerpt_wiki_topic(topic: dict, query_tokens: list[str], max_chars: int = MAX_WIKI_CHARS) -> str:
    """Extract most relevant sections from a wiki topic body."""
    body = topic["body"]
    if len(body) <= max_chars:
        return body

    # Split into sections and score each
    sections = re.split(r"\n(?=#{1,3} )", body)
    scored_sections = []
    for sec in sections:
        sec_lower = sec.lower()
        sec_score = sum(3 if t in sec_lower[:200] else (1 if t in sec_lower else 0)
                        for t in query_tokens)
        scored_sections.append((sec, sec_score))
    scored_sections.sort(key=lambda x: x[1], reverse=True)

    # Build excerpt up to max_chars
    excerpt = ""
    for sec, _ in scored_sections:
        if len(excerpt) + len(sec) > max_chars:
            remaining = max_chars - len(excerpt)
            if remaining > 100:
                excerpt += sec[:remaining] + "…"
            break
        excerpt += sec + "\n\n"
    return excerpt.strip()


# ── Card search ───────────────────────────────────────────────────────────────

def load_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    return json.loads(INDEX_FILE.read_text(encoding="utf-8")).get("items", [])


def clean_summary(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"^一句話摘要\s*", "", text)
    text = re.sub(r"^[-•]\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def score_card(item: dict, tokens: list[str], query: str) -> float:
    title   = (item.get("title") or "").lower()
    tags    = " ".join(item.get("tags") or []).lower()
    summary = (item.get("summary") or "").lower()
    blob    = (item.get("searchable") or "").lower()
    score   = 0.0
    for t in tokens:
        if t in title:   score += 8
        if t in tags:    score += 6
        if t in summary: score += 3
        if t in blob:    score += 1
    if query.lower() in blob: score += 8
    if item.get("summary"): score += 2
    return score


def search_cards(query: str, limit: int = MAX_CARDS) -> list[dict]:
    items = load_index()
    tokens = tokenize(query)
    scored = []
    for item in items:
        s = score_card(item, tokens, query)
        if s >= 5:
            scored.append((item, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    results = []
    for item, _ in scored[:limit]:
        title = (item.get("title") or "").strip()
        if not title or re.fullmatch(r"\d{10,}", title):
            continue
        results.append({
            "title": title,
            "summary": clean_summary(item.get("summary") or ""),
            "category": item.get("category") or "",
            "tags": item.get("tags") or [],
            "source_url": item.get("source_url") or "",
            "relative_path": item.get("relative_path") or "",
        })
    return results


# ── Answer generation ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
你是一個直接、有判斷力的助理。你平常會整理筆記和閱讀材料，回答問題時可以參考這些背景資料。

回答方式：
- 先給結論，再補充細節
- 語氣自然，像在跟認識的人說話，不要像在念報告
- 全程用繁體中文，不要出現簡體字
- 不要在答案裡提「知識庫」「卡片」「wiki」這些系統詞
- 不要加 [W1]、[C1] 這類引用標記
- 如果背景資料裡真的沒有相關內容，直接說不確定，不要硬掰
- 背景資料裡可能有研究分析用的角色標籤（例如「圖書館員」「教練」「主動決策者」「被動記錄者」之類），這些是內部分析用語，不要直接引用，用你自己的話說清楚那個意思就好
- 長度以夠回答為準，不用湊字數
"""

CONTEXT_TMPL = """\
以下是我整理過的相關背景資料，可以參考：

{context}

---

{query}

（用自己的話回答，不要引用上面的原文措辭）
"""

WIKI_ENTRY_TMPL = "【{title}】\n{excerpt}"

CARD_ENTRY_TMPL = "【{title}】\n{summary}"

# Internal metaphor/role labels that appear in card summaries but shouldn't leak into answers
_INTERNAL_LABELS = re.compile(
    r"(圖書館管理員|圖書館員|主動決策者|被動記錄者?|被動記錄|知識庫助理|主力維護者|偶爾的閱讀者)",
    re.UNICODE,
)


def _strip_internal_labels(text: str) -> str:
    """Remove internal analysis role-labels from card/wiki excerpts before sending to LLM."""
    text = _INTERNAL_LABELS.sub("", text)
    # Clean up empty bracket pairs left behind (regex avoids CJK char class issues)
    text = re.sub("\u300c\u300d", "", text)  # 「」
    text = re.sub("\u300e\u300f", "", text)  # 『』
    # Clean up orphaned connectors like "和「」" -> "和"
    text = re.sub(r"\s*\u548c\s*$", "", text, flags=re.MULTILINE)  # 和
    return text


def build_answer(query: str, wiki_hits: list[dict], card_hits: list[dict],
                 query_tokens: list[str], api_key: str) -> str:
    entries = []
    for t in wiki_hits:
        excerpt = _strip_internal_labels(excerpt_wiki_topic(t, query_tokens))
        entries.append(WIKI_ENTRY_TMPL.format(
            title=t["title"], excerpt=excerpt[:MAX_WIKI_CHARS]))
    for c in card_hits:
        summary = _strip_internal_labels(c["summary"][:MAX_CARD_CHARS])
        entries.append(CARD_ENTRY_TMPL.format(
            title=c["title"], summary=summary))

    if not entries:
        return "這方面我目前沒有足夠的資料，說不準。"

    prompt = CONTEXT_TMPL.format(
        context="\n\n".join(entries),
        query=query,
    )
    answer = llm_call(prompt, api_key, max_tokens=900, system=SYSTEM_PROMPT)
    return _strip_internal_labels(answer)


# ── Output formatting ─────────────────────────────────────────────────────────

def print_full(query: str, answer: str, wiki_hits: list[dict], card_hits: list[dict]) -> None:
    print(f"\n# {query}\n")
    print(answer)
    print()

    if wiki_hits:
        print("## 引用的 Wiki Topics")
        for i, t in enumerate(wiki_hits, 1):
            path = f"wiki/topics/{t['slug']}.md"
            print(f"[W{i}] **{t['title']}** → `{path}`")
        print()

    if card_hits:
        print("## 引用的知識卡片")
        for i, c in enumerate(card_hits, 1):
            url = c.get("source_url") or c.get("relative_path") or ""
            print(f"[C{i}] **{c['title']}**")
            if c.get("summary"):
                print(f"     {c['summary'][:100]}")
            if url:
                print(f"     → {url}")
        print()


def print_chat(query: str, answer: str, wiki_hits: list[dict], card_hits: list[dict]) -> None:
    """Compact format for inline chat display."""
    print(answer)
    if wiki_hits or card_hits:
        print()
        refs = []
        for i, t in enumerate(wiki_hits, 1):
            refs.append(f"[W{i}] {t['title']} (wiki/topics/{t['slug']}.md)")
        for i, c in enumerate(card_hits, 1):
            url = c.get("source_url") or c.get("relative_path") or ""
            refs.append(f"[C{i}] {c['title']}" + (f" → {url}" if url else ""))
        print("來源：" + " | ".join(refs))


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Ask your XKB knowledge base")
    parser.add_argument("query", nargs="?", help="問題")
    parser.add_argument("--format", choices=["full", "chat"], default="full",
                        help="輸出格式（full=詳細, chat=簡潔）")
    parser.add_argument("--no-wiki",  action="store_true", help="不搜 wiki topics")
    parser.add_argument("--no-cards", action="store_true", help="不搜知識卡片")
    parser.add_argument("--json",     action="store_true", help="輸出 JSON")
    parser.add_argument("--max-wiki",  type=int, default=MAX_WIKI_TOPICS)
    parser.add_argument("--max-cards", type=int, default=MAX_CARDS)
    args = parser.parse_args()

    query = (args.query or "").strip()
    if not query:
        print("請提供問題，例如：python3 scripts/xkb_ask.py \"RAG 的替代方案是什麼？\"",
              file=sys.stderr)
        return 1

    api_key = load_env_key()
    if not api_key:
        print("[ERROR] 找不到 LLM_API_KEY", file=sys.stderr)
        return 1

    query_tokens = tokenize(query)

    wiki_hits = [] if args.no_wiki  else search_wiki_topics(query, args.max_wiki)
    card_hits = [] if args.no_cards else search_cards(query, args.max_cards)

    print(f"[搜尋結果] wiki topics: {len(wiki_hits)}, cards: {len(card_hits)}", file=sys.stderr)

    answer = build_answer(query, wiki_hits, card_hits, query_tokens, api_key)

    if args.json:
        output = {
            "query": query,
            "answer": answer,
            "wiki_refs": [{"slug": t["slug"], "title": t["title"]} for t in wiki_hits],
            "card_refs": [{"title": c["title"], "url": c.get("source_url", "")} for c in card_hits],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    elif args.format == "chat":
        print_chat(query, answer, wiki_hits, card_hits)
    else:
        print_full(query, answer, wiki_hits, card_hits)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
