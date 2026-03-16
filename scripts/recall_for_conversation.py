#!/usr/bin/env python3
"""對話主動召回：根據當前對話 query，從 X 書籤索引找出最相關的知識卡。"""

from __future__ import annotations

import argparse
import json
import re
import os
import sys
from pathlib import Path
from typing import List, Dict, Any

WORKSPACE_DIR = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"

STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "和", "與", "就", "也", "都", "很",
    "想", "要", "用", "讓", "把", "跟", "對", "中", "上", "下", "嗎", "呢", "啊", "吧", "這", "那",
    "this", "that", "with", "from", "have", "will", "about", "into", "your", "their", "they", "them",
    "what", "when", "where", "which", "how", "why", "for", "and", "the", "are", "was", "were", "been",
}


def load_index(index_file: Path) -> Dict[str, Any]:
    if not index_file.exists():
        raise FileNotFoundError(f"search index not found: {index_file}")
    return json.loads(index_file.read_text(encoding="utf-8"))


def tokenize(text: str) -> List[str]:
    raw = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    return [t for t in raw if t not in STOPWORDS]


def clean_summary(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"^一句話摘要\s*", "", text)
    text = re.sub(r"^[-•]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_source_url(md_path: Path) -> str:
    if not md_path.exists():
        return ""
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    patterns = [
        r"^source_url:\s*\"?([^\"\n]+)\"?\s*$",
        r"^original_url:\s*\"?([^\"\n]+)\"?\s*$",
        r"\*\*原始連結\*\*：\s*(\S+)",
        r"https://x\.com/\S+",
        r"https://twitter\.com/\S+",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            url = m.group(1).strip() if m.groups() else m.group(0).strip()
            return url.strip('"')
    return ""


def build_relevance_reason(item: Dict[str, Any], query_tokens: List[str]) -> str:
    title = (item.get("title") or "").lower()
    category = (item.get("category") or "").lower()
    tags = " ".join(item.get("tags") or []).lower()
    summary = (item.get("summary") or "").lower()

    reasons = []
    title_hits = [t for t in query_tokens if t in title]
    tag_hits = [t for t in query_tokens if t in tags]
    category_hits = [t for t in query_tokens if t in category]
    summary_hits = [t for t in query_tokens if t in summary]

    if title_hits:
        reasons.append(f"標題命中：{'、'.join(title_hits[:3])}")
    if tag_hits:
        reasons.append(f"標籤接近：{'、'.join(tag_hits[:3])}")
    if category_hits:
        reasons.append(f"分類相關：{'、'.join(category_hits[:3])}")
    if summary_hits and not reasons:
        reasons.append(f"摘要語意接近：{'、'.join(summary_hits[:3])}")

    return "；".join(reasons[:2]) or "主題與當前對話高度相關"


def score_item(item: Dict[str, Any], query_tokens: List[str], query_text: str) -> int:
    title = (item.get("title") or "").lower()
    category = (item.get("category") or "").lower()
    tags = " ".join(item.get("tags") or []).lower()
    summary = (item.get("summary") or "").lower()
    blob = (item.get("searchable") or "").lower()

    score = 0
    for token in query_tokens:
        if token in title:
            score += 8
        if token in tags:
            score += 6
        if token in category:
            score += 4
        if token in summary:
            score += 3
        if token in blob:
            score += 1

    if query_text and query_text in blob:
        score += 8

    # 偏好有摘要、有 tags 的卡片，較適合直接對話回用
    if item.get("summary"):
        score += 2
    if item.get("tags"):
        score += 1

    return score


def recall(query: str, limit: int, min_score: int, index_file: Path = INDEX_FILE) -> List[Dict[str, Any]]:
    data = load_index(index_file)
    items = data.get("items", [])
    query = query.strip()
    query_tokens = tokenize(query)
    if not query_tokens and not query:
        return []

    results = []
    for item in items:
        score = score_item(item, query_tokens, query.lower())
        if score < min_score:
            continue

        rel_path = item.get("relative_path") or item.get("path") or ""
        # Use pre-indexed source_url first; fall back to file scan only if missing
        source_url = item.get("source_url") or ""
        if not source_url:
            md_path = BOOKMARKS_DIR / rel_path if rel_path and not rel_path.startswith("/") else Path(rel_path)
            source_url = extract_source_url(md_path)
        results.append({
            "title": item.get("title") or "(untitled)",
            "summary": clean_summary(item.get("summary") or ""),
            "category": item.get("category") or "general",
            "tags": item.get("tags") or [],
            "relative_path": rel_path,
            "source_url": source_url,
            "score": score,
            "relevance_reason": build_relevance_reason(item, query_tokens),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def print_markdown(results: List[Dict[str, Any]], query: str) -> None:
    if not results:
        print("沒有找到適合主動召回的書籤。")
        return

    print(f"# 對話主動召回結果\n")
    print(f"查詢：{query}\n")
    for idx, item in enumerate(results, start=1):
        print(f"## {idx}. {item['title']}")
        print(f"- 分類：{item['category']}")
        if item.get("tags"):
            print(f"- 標籤：{', '.join(item['tags'][:8])}")
        print(f"- 相關原因：{item['relevance_reason']}")
        print(f"- 分數：{item['score']}")
        if item.get("summary"):
            print(f"- 一句話摘要：{item['summary'][:180]}")
        if item.get("source_url"):
            print(f"- 原文連結：{item['source_url']}")
        print(f"- 檔案：{item['relative_path']}\n")


def print_prompt(results: List[Dict[str, Any]], query: str) -> None:
    if not results:
        print("NO_RECALL")
        return

    print("你之前 X 書籤裡有幾篇可能相關的：")
    for item in results:
        print(f"- {item['title']}")
        if item.get("summary"):
            print(f"  摘要：{item['summary'][:120]}")
        print(f"  為什麼相關：{item['relevance_reason']}")
        if item.get("source_url"):
            print(f"  原文：{item['source_url']}")
        else:
            print(f"  檔案：{item['relative_path']}")


def print_chat(results: List[Dict[str, Any]], query: str) -> None:
    if not results:
        print("NO_RECALL")
        return

    top = results[:2]
    print("你之前 X 書籤裡有篇相關的：")
    for item in top:
        summary = item.get("summary") or "這篇和你現在聊的主題接近。"
        reason = item.get("relevance_reason") or "主題接近"
        print(f"- {item['title']}：{summary[:90]}")
        print(f"  為什麼相關：{reason}")
        if item.get("source_url"):
            print(f"  原文：{item['source_url']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recall relevant X bookmarks for conversation use")
    parser.add_argument("query", nargs="?", help="當前對話查詢")
    parser.add_argument("--query-file", help="從檔案讀取 query")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--min-score", type=int, default=6)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--format", choices=["markdown", "prompt", "chat"], default="markdown")
    parser.add_argument("--index-file", default=str(INDEX_FILE))
    args = parser.parse_args()

    query = args.query or ""
    if args.query_file:
        query = Path(args.query_file).read_text(encoding="utf-8").strip()
    if not query.strip():
        print("請提供 query", file=sys.stderr)
        return 1

    results = recall(query, args.limit, args.min_score, Path(args.index_file))

    if args.json:
        print(json.dumps({"query": query, "results": results}, ensure_ascii=False, indent=2))
    elif args.format == "prompt":
        print_prompt(results, query)
    elif args.format == "chat":
        print_chat(results, query)
    else:
        print_markdown(results, query)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
