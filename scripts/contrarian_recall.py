#!/usr/bin/env python3
"""
Contrarian Recall — Active Recall Layer Phase 1.3

查詢來源：wiki/topics/*.md + memory/*.md
專找反例、衝突資訊、限制、失敗案例。

用於在「快速收斂方案」時主動提醒已知的反方觀點。

Usage:
  python3 contrarian_recall.py "active recall 自動觸發"
  python3 contrarian_recall.py "query" --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
_SKILL_DIR = Path(__file__).resolve().parent.parent
WIKI_TOPICS_DIR = Path(os.getenv("XKB_WIKI_DIR", str(_SKILL_DIR / "wiki"))) / "topics"
MEMORY_DIR = WORKSPACE / "memory"
MEMORY_MD = WORKSPACE / "MEMORY.md"

STOPWORDS = {
    "的", "了", "是", "我", "你", "在", "有", "和", "也", "都", "很", "要", "用",
    "this", "that", "with", "from", "have", "will", "about", "your", "the", "are",
}

# ── 反例 / 衝突信號詞 ──────────────────────────────────────────────────────────
CONTRARIAN_SIGNALS = [
    # 中文否定 / 警示
    r"不[要建議適合]", r"避免", r"問題[是在]", r"失敗", r"踩坑", r"缺陷", r"限制",
    r"注意[：:]?", r"⚠️", r"警告", r"但[是]?.*不", r"然而", r"反例", r"反而",
    r"缺口", r"弱點", r"做不到", r"沒有解決", r"無法", r"不夠",
    # 英文
    r"avoid", r"warning", r"caveat", r"limitation", r"failed", r"problem",
    r"however", r"but\s+not", r"don.?t", r"issue", r"drawback", r"risk",
]

CONTRARIAN_RE = re.compile("|".join(CONTRARIAN_SIGNALS), re.IGNORECASE)


class ContrarianResult(NamedTuple):
    source_type: str   # wiki | memory
    source_file: str
    section: str
    excerpt: str
    score: float
    signal_words: list[str]


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{1,}", text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) >= 2]


def _relevance_score(tokens: list[str], text: str) -> float:
    if not tokens or not text:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for t in tokens if t in text_lower)
    return hits / max(len(tokens), 1)


def _signal_count(text: str) -> tuple[int, list[str]]:
    """Count contrarian signal words and return (count, found_signals)."""
    found = re.findall(CONTRARIAN_RE, text)
    return len(found), found[:5]


def _excerpt_around_signal(text: str, tokens: list[str], max_len: int = 200) -> str:
    """Find the sentence with the most signals + relevance."""
    sentences = re.split(r"[。！？\n]", text)
    best = ""
    best_score = 0.0
    for s in sentences:
        s = s.strip()
        if len(s) < 10:
            continue
        sig_count, _ = _signal_count(s)
        rel = _relevance_score(tokens, s)
        score = sig_count * 0.4 + rel * 0.6
        if score > best_score:
            best_score = score
            best = s
    result = best or text[:max_len]
    return result[:max_len] + ("…" if len(result) > max_len else "")


def _split_sections(content: str) -> list[tuple[str, str]]:
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


def _search_file(
    path: Path,
    tokens: list[str],
    source_type: str,
    rel_base: Path,
) -> list[ContrarianResult]:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return []

    # Remove frontmatter
    content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL).strip()
    rel_path = str(path.relative_to(rel_base))
    results = []

    for title, body in _split_sections(content):
        if len(body.strip()) < 20:
            continue
        sig_count, signals = _signal_count(body)
        if sig_count == 0:
            continue
        rel = _relevance_score(tokens, f"{title} {body}")
        if rel < 0.15:
            continue
        score = round(rel * 0.6 + min(sig_count / 5, 1.0) * 0.4, 3)
        excerpt = _excerpt_around_signal(body, tokens)
        results.append(ContrarianResult(
            source_type=source_type,
            source_file=rel_path,
            section=title,
            excerpt=excerpt,
            score=score,
            signal_words=signals,
        ))
    return results


def recall(query: str, top_k: int = 2) -> list[ContrarianResult]:
    tokens = tokenize(query)
    if not tokens:
        return []

    candidates: list[ContrarianResult] = []

    # Search wiki topics
    if WIKI_TOPICS_DIR.exists():
        for path in WIKI_TOPICS_DIR.glob("*.md"):
            candidates.extend(_search_file(path, tokens, "wiki", WORKSPACE))

    # Search memory
    memory_files: list[Path] = []
    if MEMORY_MD.exists():
        memory_files.append(MEMORY_MD)
    if MEMORY_DIR.exists():
        memory_files.extend(MEMORY_DIR.glob("*.md"))
    for path in memory_files:
        candidates.extend(_search_file(path, tokens, "memory", WORKSPACE))

    candidates.sort(key=lambda r: r.score, reverse=True)
    return candidates[:top_k]


def format_hint(results: list[ContrarianResult]) -> str:
    if not results:
        return ""
    lines = ["【⚠️ 反例提醒】"]
    for r in results:
        label = f"wiki/{r.source_file.split('/')[-1]}" if r.source_type == "wiki" else "MEMORY"
        lines.append(f"• [{label}] {r.section}")
        lines.append(f"  {r.excerpt}")
        lines.append("")
    return "\n".join(lines).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Contrarian Recall — finds counter-examples and warnings")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()

    query = args.query or sys.stdin.read().strip()
    if not query:
        print("Usage: contrarian_recall.py <query>")
        return 1

    results = recall(query, top_k=args.limit)

    if args.as_json:
        print(json.dumps([r._asdict() for r in results], ensure_ascii=False, indent=2))
        return 0

    if not results:
        print("（沒找到反例或警示）")
        return 0

    print(format_hint(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
