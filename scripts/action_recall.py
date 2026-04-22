#!/usr/bin/env python3
"""
Action Recall — Active Recall Layer Phase 1.3

查詢來源：
  - scripts/*.py 腳本清單（比對名稱）
  - wiki/topics/*.md（找含 TODO / 下一步 / Phase / roadmap 的段落）
  - docs/plans/*.md（比對計畫文件）

適用於「規劃下一步、評估 implementation、拆任務」時，
主動提醒已有的腳本、元件或計畫可以復用。

Usage:
  python3 action_recall.py "active recall 自動觸發 hook"
  python3 action_recall.py "query" --json
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
SCRIPTS_DIR = _SKILL_DIR / "scripts"
WIKI_TOPICS_DIR = Path(os.getenv("XKB_WIKI_DIR", str(_SKILL_DIR / "wiki"))) / "topics"
DOCS_PLANS_DIR = WORKSPACE / "docs" / "plans"
DOCS_XKB_DIR = WORKSPACE / "docs" / "xkb"

STOPWORDS = {
    "的", "了", "是", "我", "你", "在", "有", "和", "也", "都", "要", "用",
    "this", "that", "with", "from", "have", "will", "the", "are",
}

# ── 可執行資產信號詞 ────────────────────────────────────────────────────────────
ACTION_SIGNALS = [
    r"TODO", r"下一步", r"下一個", r"接下來", r"Phase\s*\d", r"v\d\s*[:：]",
    r"roadmap", r"計畫", r"可以直接", r"已有", r"現有", r"可復用", r"可接",
    r"建議先", r"優先", r"待辦",
]
ACTION_RE = re.compile("|".join(ACTION_SIGNALS), re.IGNORECASE)


class ActionResult(NamedTuple):
    asset_type: str     # script | wiki_section | plan
    name: str           # script filename / wiki title / plan filename
    path: str           # relative path
    description: str    # one-line description or excerpt
    score: float


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{1,}", text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) >= 2]


def _score(tokens: list[str], text: str) -> float:
    if not tokens or not text:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for t in tokens if t in text_lower)
    phrase_bonus = sum(
        0.3 for i in range(len(tokens) - 1)
        if f"{tokens[i]} {tokens[i+1]}" in text_lower or tokens[i] + tokens[i+1] in text_lower
    )
    return hits / max(len(tokens), 1) + phrase_bonus


# ── Script search ──────────────────────────────────────────────────────────────

def _search_scripts(tokens: list[str]) -> list[ActionResult]:
    if not SCRIPTS_DIR.exists():
        return []
    results = []
    for path in SCRIPTS_DIR.glob("*.py"):
        stem = path.stem.replace("_", " ").replace("-", " ")
        score = _score(tokens, stem + " " + path.name)
        if score < 0.2:
            continue
        # Try to get one-line description from docstring
        try:
            content = path.read_text(encoding="utf-8")
            docstring_match = re.search(r'"""(.*?)"""', content, re.DOTALL)
            desc = ""
            if docstring_match:
                first_line = docstring_match.group(1).strip().splitlines()[0].strip()
                desc = first_line[:100]
        except Exception:
            desc = ""
        results.append(ActionResult(
            asset_type="script",
            name=path.name,
            path=f"scripts/{path.name}",
            description=desc or stem,
            score=round(score, 3),
        ))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:3]


# ── Wiki action sections ───────────────────────────────────────────────────────

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


def _search_wiki_actions(tokens: list[str]) -> list[ActionResult]:
    if not WIKI_TOPICS_DIR.exists():
        return []
    results = []
    for path in WIKI_TOPICS_DIR.glob("*.md"):
        try:
            content = re.sub(r"^---\n.*?\n---\n", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        except Exception:
            continue
        for title, body in _split_sections(content):
            # Only sections with action signals
            if not ACTION_RE.search(body) and not ACTION_RE.search(title):
                continue
            rel_score = _score(tokens, f"{title} {body}")
            if rel_score < 0.15:
                continue
            excerpt = (body.strip().splitlines()[0] or "")[:120]
            results.append(ActionResult(
                asset_type="wiki_section",
                name=f"{path.stem} § {title}",
                path=f"wiki/topics/{path.name}",
                description=excerpt,
                score=round(rel_score, 3),
            ))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:2]


# ── Plan / spec docs search ────────────────────────────────────────────────────

def _search_plans(tokens: list[str]) -> list[ActionResult]:
    results = []
    for dir_ in [DOCS_PLANS_DIR, DOCS_XKB_DIR]:
        if not dir_.exists():
            continue
        for path in dir_.glob("*.md"):
            stem = path.stem.replace("-", " ").replace("_", " ")
            score = _score(tokens, stem)
            if score < 0.2:
                continue
            try:
                first_line = path.read_text(encoding="utf-8").splitlines()[0].lstrip("#").strip()
            except Exception:
                first_line = stem
            results.append(ActionResult(
                asset_type="plan",
                name=path.name,
                path=str(path.relative_to(WORKSPACE)),
                description=first_line[:100],
                score=round(score, 3),
            ))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:2]


# ── Main recall ────────────────────────────────────────────────────────────────

def recall(query: str, top_k: int = 4) -> list[ActionResult]:
    tokens = tokenize(query)
    if not tokens:
        return []

    candidates: list[ActionResult] = []
    candidates.extend(_search_scripts(tokens))
    candidates.extend(_search_wiki_actions(tokens))
    candidates.extend(_search_plans(tokens))

    # De-dup by path, keep highest score
    seen: dict[str, ActionResult] = {}
    for r in candidates:
        if r.path not in seen or r.score > seen[r.path].score:
            seen[r.path] = r

    sorted_results = sorted(seen.values(), key=lambda r: r.score, reverse=True)
    return sorted_results[:top_k]


def format_hint(results: list[ActionResult]) -> str:
    if not results:
        return ""
    lines = ["【🔧 可復用資產】"]
    type_labels = {"script": "腳本", "wiki_section": "Wiki", "plan": "計畫文件"}
    for r in results:
        label = type_labels.get(r.asset_type, r.asset_type)
        lines.append(f"• [{label}] {r.name}")
        if r.description:
            lines.append(f"  {r.description}")
        lines.append(f"  路徑：{r.path}")
        lines.append("")
    return "\n".join(lines).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Action Recall — finds existing scripts, wiki sections, and plans")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()

    query = args.query or sys.stdin.read().strip()
    if not query:
        print("Usage: action_recall.py <query>")
        return 1

    results = recall(query, top_k=args.limit)

    if args.as_json:
        print(json.dumps([r._asdict() for r in results], ensure_ascii=False, indent=2))
        return 0

    if not results:
        print("（沒找到相關的可執行資產）")
        return 0

    print(format_hint(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
