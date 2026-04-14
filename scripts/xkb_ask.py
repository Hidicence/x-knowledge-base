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
import io
import json
import math
import os
import re
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows (prevents cp950 encoding errors)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Unified LLM helper ────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from _llm import call as _llm_backend

# ── gbrain bridge (optional) ──────────────────────────────────────────────────
_GBRAIN_DIR = Path(os.getenv("GBRAIN_DIR", str(Path.home() / "Desktop" / "gbrain")))
_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
if not _GEMINI_KEY:
    try:
        import json as _j
        _cfg = Path.home() / ".openclaw" / "openclaw.json"
        if _cfg.exists():
            _GEMINI_KEY = _j.loads(_cfg.read_text(encoding="utf-8")).get("env", {}).get("GEMINI_API_KEY", "")
    except Exception:
        pass

_GBRAIN_AVAILABLE = _GBRAIN_DIR.exists() and bool(_GEMINI_KEY)

# ── Paths ──────────────────────────────────────────────────────────────────────
WORKSPACE_DIR = Path(os.getenv("OPENCLAW_WORKSPACE",
    os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
INDEX_FILE     = BOOKMARKS_DIR / "search_index.json"
VECTOR_FILE    = BOOKMARKS_DIR / "vector_index.json"
_SKILL_DIR     = Path(__file__).resolve().parent.parent
WIKI_DIR       = Path(os.getenv("XKB_WIKI_DIR", str(_SKILL_DIR / "wiki")))
TOPICS_DIR     = WIKI_DIR / "topics"

# ── LLM ───────────────────────────────────────────────────────────────────────

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
    return ""  # auth handled by _llm.py via openclaw CLI


def llm_call(prompt: str, api_key: str = "", max_tokens: int = 1000,
             system: str | None = None) -> str:
    return _llm_backend(system or "", prompt)


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


def search_wiki_topics(query: str, limit: int = MAX_WIKI_TOPICS) -> tuple[list[dict], float]:
    """Returns (hits, max_score)."""
    topics = load_wiki_topics()
    if not topics:
        return [], 0.0
    tokens = tokenize(query)
    scored = [(t, score_wiki_topic(t, tokens, query)) for t in topics]
    scored.sort(key=lambda x: x[1], reverse=True)
    hits = [t for t, s in scored[:limit] if s > 0]
    max_score = scored[0][1] if scored else 0.0
    return hits, max_score


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


def search_cards_gbrain(query: str, limit: int = MAX_CARDS) -> list[dict]:
    """Use gbrain hybrid search (RRF + Gemini) instead of keyword index."""
    try:
        from xbrain_recall import xbrain_query as gbrain_query
    except ImportError:
        return []

    try:
        raw = gbrain_query(query, limit=limit)
    except Exception:
        return []

    results = []
    for r in raw:
        title = r.get("title", "").strip()
        chunk = r.get("chunk_text", "")

        # If title is just a numeric slug (tweet ID), extract from chunk heading
        if not title or re.fullmatch(r"\d{10,}", title):
            m = re.search(r"^#\s+(.+)$", chunk, re.MULTILINE)
            if m:
                title = m.group(1).strip()
            else:
                # Try 雙語摘要 first sentence as fallback title
                m2 = re.search(r"雙語摘要[^:：]*[：:]\s*\n[中英En][^：:]+[：:]?\s*(.+)", chunk)
                if m2:
                    title = m2.group(1).strip()[:60]
                else:
                    continue  # no usable title, skip

        # Extract 雙語摘要 section as summary, fall back to first 300 chars
        summary = ""
        m = re.search(r"雙語摘要[：:]\s*\n(.*?)(?=\n##|\Z)", chunk, re.DOTALL)
        if m:
            summary = m.group(1).strip()[:300]
        if not summary:
            # Strip frontmatter/heading
            body = re.sub(r"^---\n.*?\n---\n", "", chunk, flags=re.DOTALL)
            body = re.sub(r"^#.*\n", "", body).strip()
            summary = body[:300]

        results.append({
            "title": title,
            "summary": clean_summary(summary),
            "category": r.get("type", ""),
            "tags": [],
            "source_url": r.get("source_url", ""),
            "relative_path": "",
        })
    return results


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

WIKI_ENTRY_TMPL = "【{title}】\n{excerpt}"
CARD_ENTRY_TMPL = "【{title}】\n{summary}"

# ── Situation classifier ───────────────────────────────────────────────────────

# Score threshold: below this, wiki content doesn't add enough → go direct
WIKI_SCORE_THRESHOLD = 8.0

_CONTINUITY_RE = re.compile(
    r"(我們(之前|上次|說好|討論過|提到)|你(還)?記得|上次(說|講|討論)|之前(說|講|提到|決定)|"
    r"我們(的系統|做的|設計的|定的)|XKB|recall.router|wiki.pipeline)",
    re.IGNORECASE,
)

def classify_query(query: str, wiki_max_score: float, card_hits: list[dict]) -> str:
    """
    Classify query into one of three situations:
    - 'continuity': asking about past discussions / decisions → wiki-primary
    - 'enrichment': general question + wiki has relevant content → LLM base + wiki supplement
    - 'direct':     general question, wiki has nothing to add → LLM only
    """
    if _CONTINUITY_RE.search(query):
        return "continuity"
    if wiki_max_score >= WIKI_SCORE_THRESHOLD or len(card_hits) >= 3:
        return "enrichment"
    return "direct"


# ── System prompts per situation ───────────────────────────────────────────────

# direct: LLM answers from its own knowledge, no wiki context involved
PROMPT_DIRECT = """\
你是一個在 AI、agent 架構、知識管理領域有長期積累的人。
直接用自己的理解回答，語氣像在跟認識的朋友討論，直接有觀點。
全程繁體中文，不出現簡體字，不加大標題，長度夠用就好。
"""

# enrichment: LLM base answer + wiki adds genuinely new angles
PROMPT_ENRICHMENT = """\
你是一個在 AI、agent 架構、知識管理領域有長期積累的人，同時有自己的研究積累。

回答方式：
先用自己的通識理解回答這個問題，語氣自然，像在跟朋友討論。
然後，如果下面提供的研究資料裡有你回答中沒有提到的新角度或具體案例，自然地補充進來。

補充時的關鍵要求：
- 引入研究資料的觀點時，要說明來源背景（「有個做了 100 天實驗的案例...」「某個工具的實測結果...」），不要無頭無尾直接說結論
- 說完別人的觀點後，加上你自己的詮釋——為什麼這件事重要、背後的邏輯是什麼
- 格式：「[來源背景]提到/發現 [觀點]。我覺得這背後的原因是...」
- 研究資料裡的東西，只有在「你通識回答裡沒有的」時候才用，重複的不要再說一遍
- 如果研究資料沒有真正新的東西，直接用通識回答就好，不要硬湊
- 有相關連結就在最後附上 2~3 條，沒有就不附
- 全程繁體中文，不提「知識庫」「卡片」「wiki」「筆記」，不加大標題
"""

# continuity: wiki is primary, LLM organizes and presents
PROMPT_CONTINUITY = """\
你是一個在 AI、agent 架構、知識管理領域有長期積累的人，同時有自己的研究筆記和過往討論記錄。

有人在問你們之前討論過或研究過的東西。
從你的研究記錄中整理出相關內容，直接回答。
語氣自然，像在回憶一件你真的做過的事，不要像在朗讀文件。

規則：全程繁體中文，不提「知識庫」「卡片」「wiki」，長度夠用就好。
"""

CONTEXT_TMPL = """\
以下是與這個問題相關的研究資料，裡面可能有你通識回答中沒有的新角度或具體案例：

{context}

---

{query}
"""

# Internal metaphor/role labels that appear in card summaries but shouldn't leak into answers
_INTERNAL_LABELS = re.compile(
    r"(圖書館管理員|圖書館員|主動決策者|被動記錄者?|被動記錄|知識庫助理|主力維護者|偶爾的閱讀者)",
    re.UNICODE,
)

# Headings that signal internal system-status sections — strip entire section from wiki excerpts
_INTERNAL_SECTION_HEADINGS = re.compile(
    r"^#{1,3}\s*(未解決|待辦|TODO|這頁自身|系統狀態|治理|已知問題|open issue|roadmap 備注)",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_internal_sections(text: str) -> str:
    """Remove internal status/todo sections from wiki content before sending to LLM."""
    lines = text.splitlines()
    result = []
    skip = False
    for line in lines:
        if _INTERNAL_SECTION_HEADINGS.match(line):
            skip = True
            continue
        # Stop skipping when we hit the next same-or-higher heading
        if skip and re.match(r"^#{1,3} ", line):
            skip = False
        if not skip:
            result.append(line)
    return "\n".join(result)


def _strip_internal_labels(text: str) -> str:
    """Remove internal analysis role-labels from card/wiki excerpts before sending to LLM."""
    text = _INTERNAL_LABELS.sub("", text)
    # Clean up empty bracket pairs left behind (regex avoids CJK char class issues)
    text = re.sub("\u300c\u300d", "", text)  # 「」
    text = re.sub("\u300e\u300f", "", text)  # 『』
    # Clean up orphaned connectors like "和「」" -> "和"
    text = re.sub(r"\s*\u548c\s*$", "", text, flags=re.MULTILINE)  # 和
    return text


_SIMPLIFIED_TO_TRAD = str.maketrans(
    "调这条记忆终样对现时间来实际问题后还没说应该因为就已经体验处理决定创建设计发现开始结束",
    "調這條記憶終樣對現時間來實際問題後還沒說應該因為就已經體驗處理決定創建設計發現開始結束",
)

def _fix_simplified(text: str) -> str:
    """Best-effort fix for common simplified Chinese characters that MiniMax M2.7 occasionally outputs."""
    return text.translate(_SIMPLIFIED_TO_TRAD)


def build_answer(query: str, wiki_hits: list[dict], card_hits: list[dict],
                 query_tokens: list[str], api_key: str,
                 situation: str = "enrichment") -> str:

    # direct: no wiki context needed
    if situation == "direct":
        return _fix_simplified(llm_call(query, api_key, max_tokens=700, system=PROMPT_DIRECT))

    # Build context from hits
    entries = []
    source_links: list[tuple[str, str]] = []

    for t in wiki_hits:
        raw_excerpt = excerpt_wiki_topic(t, query_tokens)
        excerpt = _strip_internal_labels(_strip_internal_sections(raw_excerpt))
        entries.append(WIKI_ENTRY_TMPL.format(
            title=t["title"], excerpt=excerpt[:MAX_WIKI_CHARS]))

    for c in card_hits:
        summary = _strip_internal_labels(c["summary"][:MAX_CARD_CHARS])
        entries.append(CARD_ENTRY_TMPL.format(
            title=c["title"], summary=summary))
        url = c.get("source_url", "")
        if url and c.get("title"):
            source_links.append((c["title"], url))

    if not entries:
        return _fix_simplified(llm_call(query, api_key, max_tokens=700, system=PROMPT_DIRECT))

    links_block = ""
    if source_links and situation == "enrichment":
        links_block = "\n\n可用連結（只選真正相關的，最多挑 3 條）：\n" + "\n".join(
            f"- [{t}]({u})" for t, u in source_links[:5]
        )

    system = PROMPT_CONTINUITY if situation == "continuity" else PROMPT_ENRICHMENT
    prompt = CONTEXT_TMPL.format(
        context="\n\n".join(entries) + links_block,
        query=query,
    )
    answer = llm_call(prompt, api_key, max_tokens=900, system=system)
    return _fix_simplified(_strip_internal_labels(answer))


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
    parser.add_argument("--no-wiki",    action="store_true", help="不搜 wiki topics")
    parser.add_argument("--no-cards",   action="store_true", help="不搜知識卡片")
    parser.add_argument("--no-gbrain",  action="store_true", help="強制使用 keyword 搜尋，不用 gbrain")
    parser.add_argument("--json",       action="store_true", help="輸出 JSON")
    parser.add_argument("--max-wiki",   type=int, default=MAX_WIKI_TOPICS)
    parser.add_argument("--max-cards",  type=int, default=MAX_CARDS)
    args = parser.parse_args()

    query = (args.query or "").strip()
    if not query:
        print("請提供問題，例如：python3 scripts/xkb_ask.py \"RAG 的替代方案是什麼？\"",
              file=sys.stderr)
        return 1

    api_key = ""  # auth handled by _llm.py
    query_tokens = tokenize(query)

    use_gbrain = _GBRAIN_AVAILABLE and not args.no_gbrain
    card_search_fn = search_cards_gbrain if use_gbrain else search_cards
    card_backend = "gbrain⚡" if use_gbrain else "keyword"

    wiki_hits, wiki_max_score = ([], 0.0) if args.no_wiki else search_wiki_topics(query, args.max_wiki)
    card_hits = [] if args.no_cards else card_search_fn(query, args.max_cards)

    situation = classify_query(query, wiki_max_score, card_hits)
    print(f"[搜尋結果] wiki topics: {len(wiki_hits)}, cards: {len(card_hits)}, cards_backend: {card_backend}, situation: {situation}", file=sys.stderr)

    answer = build_answer(query, wiki_hits, card_hits, query_tokens, api_key, situation)

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
