#!/usr/bin/env python3
"""
Continuity Recall — Active Recall Layer Phase 1

查詢來源：MEMORY.md + memory/*.md + wiki/topics/*.md
用於 hard trigger（進度詢問、定義回溯、決策查詢）

Usage:
  python3 continuity_recall.py "XKB 下一步是什麼"
  python3 continuity_recall.py "active recall 的定義" --json
  python3 continuity_recall.py "query" --source memory   # 只查 memory
  python3 continuity_recall.py "query" --source wiki     # 只查 wiki
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
MEMORY_DIR = WORKSPACE / "memory"
WIKI_TOPICS_DIR = WORKSPACE / "wiki" / "topics"
MEMORY_MD = WORKSPACE / "MEMORY.md"

STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "和", "與", "就", "也", "都", "很",
    "想", "要", "用", "讓", "把", "跟", "對", "中", "上", "下", "嗎", "呢", "啊", "吧", "這", "那",
    "this", "that", "with", "from", "have", "will", "about", "your", "they", "what",
    "when", "where", "which", "how", "why", "for", "and", "the", "are", "was",
}


class RecallResult(NamedTuple):
    source_type: str    # memory | wiki
    source_file: str    # relative path
    section: str        # section title or ""
    excerpt: str        # text snippet (150 chars)
    score: float
    url: str = ""       # wiki topic URL or ""


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{1,}", text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) >= 2]


def _score_text(tokens: list[str], text: str) -> float:
    """Simple token overlap score."""
    if not tokens or not text:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for t in tokens if t in text_lower)
    # Phrase bonus: check if two consecutive tokens appear adjacent
    phrase_bonus = 0.0
    for i in range(len(tokens) - 1):
        phrase = tokens[i] + tokens[i + 1]
        if phrase in text_lower or f"{tokens[i]} {tokens[i+1]}" in text_lower:
            phrase_bonus += 0.5
    return hits / max(len(tokens), 1) + phrase_bonus


def _split_into_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown into (section_title, body) pairs."""
    sections: list[tuple[str, str]] = []
    current_title = ""
    current_lines: list[str] = []

    for line in content.splitlines():
        if re.match(r"^#{1,3} .+", line):
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = re.sub(r"^#{1,3} ", "", line).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))

    return sections


def _excerpt(text: str, tokens: list[str], max_len: int = 200) -> str:
    """Extract the most relevant snippet from text."""
    text_clean = re.sub(r"\n+", " ", text).strip()
    if len(text_clean) <= max_len:
        return text_clean

    # Find best window containing most tokens
    words = text_clean.split()
    best_start = 0
    best_score = 0.0
    window = 30  # words
    for i in range(0, max(1, len(words) - window)):
        chunk = " ".join(words[i:i + window])
        s = _score_text(tokens, chunk)
        if s > best_score:
            best_score = s
            best_start = i

    snippet = " ".join(words[best_start:best_start + window])
    if len(snippet) > max_len:
        snippet = snippet[:max_len - 1] + "…"
    return snippet


# ── Memory recall ─────────────────────────────────────────────────────────────

def recall_from_memory(query: str, top_k: int = 3) -> list[RecallResult]:
    """Search MEMORY.md + memory/*.md for relevant sections."""
    tokens = tokenize(query)
    if not tokens:
        return []

    candidates: list[RecallResult] = []

    # Collect all memory files
    memory_files: list[Path] = []
    if MEMORY_MD.exists():
        memory_files.append(MEMORY_MD)
    if MEMORY_DIR.exists():
        memory_files.extend(MEMORY_DIR.glob("*.md"))

    for path in memory_files:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue

        rel_path = str(path.relative_to(WORKSPACE))
        sections = _split_into_sections(content)

        for section_title, body in sections:
            if not body.strip():
                continue
            # Score: title match weighted higher
            title_score = _score_text(tokens, section_title) * 2.0
            body_score = _score_text(tokens, body)
            total = title_score + body_score * 0.5

            if total < 0.3:
                continue

            excerpt = _excerpt(body, tokens)
            candidates.append(RecallResult(
                source_type="memory",
                source_file=rel_path,
                section=section_title,
                excerpt=excerpt,
                score=round(total, 3),
            ))

    candidates.sort(key=lambda r: r.score, reverse=True)
    return candidates[:top_k]


# ── Wiki recall ───────────────────────────────────────────────────────────────

def _parse_wiki_frontmatter(content: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not m:
        return {}
    data: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip()
    return data


def recall_from_wiki(query: str, top_k: int = 2) -> list[RecallResult]:
    """Search wiki/topics/*.md for relevant content."""
    tokens = tokenize(query)
    if not tokens or not WIKI_TOPICS_DIR.exists():
        return []

    candidates: list[RecallResult] = []

    for path in WIKI_TOPICS_DIR.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue

        fm = _parse_wiki_frontmatter(content)
        title = fm.get("title", path.stem)
        tags_str = fm.get("tags", "")

        # Remove frontmatter for body search
        body = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL).strip()

        # Score
        title_score = _score_text(tokens, title) * 3.0
        tags_score = _score_text(tokens, tags_str) * 1.5
        body_score = _score_text(tokens, body) * 0.4
        total = title_score + tags_score + body_score

        if total < 0.4:
            continue

        # Find best excerpt from body
        sections = _split_into_sections(body)
        best_excerpt = ""
        best_section = ""
        best_sec_score = 0.0
        for sec_title, sec_body in sections:
            s = _score_text(tokens, sec_title) * 2 + _score_text(tokens, sec_body)
            if s > best_sec_score:
                best_sec_score = s
                best_section = sec_title
                best_excerpt = _excerpt(sec_body, tokens)

        if not best_excerpt:
            best_excerpt = _excerpt(body, tokens)

        candidates.append(RecallResult(
            source_type="wiki",
            source_file=f"wiki/topics/{path.name}",
            section=best_section or title,
            excerpt=best_excerpt,
            score=round(total, 3),
            url=f"wiki/topics/{path.stem}",
        ))

    candidates.sort(key=lambda r: r.score, reverse=True)
    return candidates[:top_k]


# ── Main ──────────────────────────────────────────────────────────────────────

def recall(query: str, source: str = "both", top_k: int = 5) -> list[RecallResult]:
    """Unified entry: search memory and/or wiki."""
    results: list[RecallResult] = []
    if source in ("memory", "both"):
        results.extend(recall_from_memory(query, top_k=3))
    if source in ("wiki", "both"):
        results.extend(recall_from_wiki(query, top_k=2))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]


def format_chat(results: list[RecallResult]) -> str:
    if not results:
        return ""
    lines = ["【知識庫補位】"]
    for r in results:
        source_label = "MEMORY" if r.source_type == "memory" else f"wiki/{r.url.split('/')[-1] if r.url else r.source_file}"
        lines.append(f"• [{source_label}] {r.section}")
        if r.excerpt:
            lines.append(f"  {r.excerpt[:150]}")
        lines.append("")
    return "\n".join(lines).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuity Recall — searches MEMORY.md + wiki/topics")
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--source", choices=["memory", "wiki", "both"], default="both")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--format", choices=["chat", "full"], default="chat")
    args = parser.parse_args()

    query = args.query or sys.stdin.read().strip()
    if not query:
        print("Usage: continuity_recall.py <query>")
        return 1

    results = recall(query, source=args.source, top_k=args.limit)

    if args.json:
        print(json.dumps([r._asdict() for r in results], ensure_ascii=False, indent=2))
        return 0

    if not results:
        print("（沒找到相關的 continuity 記憶）")
        return 0

    if args.format == "chat":
        print(format_chat(results))
    else:
        for r in results:
            print(f"[{r.source_type}] {r.source_file} § {r.section}")
            print(f"  score: {r.score}")
            print(f"  {r.excerpt[:200]}")
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
