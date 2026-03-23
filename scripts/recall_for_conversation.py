#!/usr/bin/env python3
"""對話主動召回：根據當前對話 query，從 X 書籤索引找出最相關的知識卡。"""

from __future__ import annotations

import argparse
import json
import math
import re
import os
import sys
from pathlib import Path
from typing import List, Dict, Any

WORKSPACE_DIR = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"
VECTOR_FILE = BOOKMARKS_DIR / "vector_index.json"
TOPIC_PROFILE_FILE = Path(
    os.getenv("XKB_TOPIC_PROFILE_PATH", str(WORKSPACE_DIR / "memory" / "x-knowledge-base" / "topic_profile.json"))
)
GENERIC_CATEGORIES = {"general", "99-general", "other", "misc", "uncategorized"}
LOW_SIGNAL_SUMMARIES = {"（待整理）", "待整理", "todo", "tbd", "n/a"}
LOW_SIGNAL_SOURCES = {"x", "twitter"}

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


def load_topic_profile(topic_profile_file: Path = TOPIC_PROFILE_FILE) -> Dict[str, Any]:
    if not topic_profile_file.exists():
        return {}
    try:
        return json.loads(topic_profile_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_topic_profile_matches(query_tokens: List[str], topic_profile: Dict[str, Any]) -> Dict[str, Any]:
    categories = {item.get("name", ""): item.get("weight", 0.0) for item in topic_profile.get("top_categories", [])}
    tags = {item.get("name", ""): item.get("weight", 0.0) for item in topic_profile.get("top_tags", [])}

    matched_categories = [name for name in categories if name and any(token in name or name in token for token in query_tokens)]
    matched_tags = [name for name in tags if name and any(token == name or token in name or name in token for token in query_tokens)]

    cat_boost = max((categories[name] for name in matched_categories), default=0.0)
    tag_boost = max((tags[name] for name in matched_tags), default=0.0)
    combined = max(cat_boost, tag_boost)

    return {
        "matched_categories": matched_categories[:3],
        "matched_tags": matched_tags[:5],
        "topic_boost": round(combined, 4),
    }


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


def build_relevance_reason(item: Dict[str, Any], query_tokens: List[str], topic_matches: Dict[str, Any] | None = None) -> str:
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

    if topic_matches:
        if topic_matches.get("matched_categories"):
            reasons.append(f"命中使用者高頻分類：{'、'.join(topic_matches['matched_categories'][:2])}")
        elif topic_matches.get("matched_tags"):
            reasons.append(f"命中使用者高頻標籤：{'、'.join(topic_matches['matched_tags'][:3])}")

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



def _keyword_score(query: str, item: dict) -> float:
    """Fraction of query tokens found in title + tags + summary."""
    import re as _re
    tokens = set(_re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))
    if not tokens:
        return 0.0
    text = " ".join([
        (item.get("title") or "").lower(),
        " ".join(item.get("tags") or []).lower(),
        (item.get("summary") or "").lower(),
    ])
    return sum(1 for t in tokens if t in text) / len(tokens)


def _cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    denom = norm_a * norm_b
    return dot / denom if denom > 1e-9 else 0.0


def _normalize_vector(vec: Any) -> list[float] | None:
    if not isinstance(vec, list) or not vec:
        return None
    normalized = []
    for value in vec:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        normalized.append(float(value))
    return normalized


def _source_quality(item: Dict[str, Any]) -> float:
    source = (item.get("source") or "").strip().lower()
    source_url = (item.get("source_url") or "").strip().lower()
    title = (item.get("title") or "").strip().lower()

    score = 0.0
    if source == "youtube":
        score += 0.05
    if source_url.startswith("https://"):
        score += 0.05
    if "github.com" in source_url:
        score += 0.05
    if any(host in source_url for host in ["x.com/", "twitter.com/"]):
        score += 0.02
    if re.match(r"^\d{4}-\d{2}-\d{2}-", title):
        score -= 0.08
    if source in LOW_SIGNAL_SOURCES and not source_url:
        score -= 0.03
    return score


def _summary_quality(summary: str) -> float:
    summary = clean_summary(summary)
    if not summary:
        return -0.08
    if summary in LOW_SIGNAL_SUMMARIES:
        return -0.12
    if len(summary) < 12:
        return -0.05
    if len(summary) >= 40:
        return 0.05
    return 0.02


def _category_penalty(item: Dict[str, Any]) -> float:
    category = (item.get("category") or "").strip().lower()
    return -0.08 if category in GENERIC_CATEGORIES else 0.0


def _ranking_adjustments(item: Dict[str, Any], topic_matches: Dict[str, Any]) -> Dict[str, float]:
    topic_boost = topic_matches.get("topic_boost", 0.0)
    matched_categories = set(topic_matches.get("matched_categories", []))
    matched_tags = set(topic_matches.get("matched_tags", []))

    item_category = (item.get("category") or "").lower()
    item_tags = {str(tag).lower() for tag in (item.get("tags") or [])}

    topic_bonus = 0.0
    if topic_boost > 0:
        if item_category in matched_categories:
            topic_bonus += round(topic_boost * 0.12, 4)
        elif item_tags & matched_tags:
            topic_bonus += round(topic_boost * 0.08, 4)

    summary_bonus = _summary_quality(item.get("summary") or "")
    source_bonus = _source_quality(item)
    category_penalty = _category_penalty(item)

    return {
        "topic_bonus": topic_bonus,
        "summary_bonus": summary_bonus,
        "source_bonus": source_bonus,
        "category_penalty": category_penalty,
        "total_adjustment": round(topic_bonus + summary_bonus + source_bonus + category_penalty, 4),
    }



def _display_title(item: dict) -> str:
    """Return a human-readable title. If title is just a tweet ID, use summary snippet."""
    import re as _re
    title = (item.get("title") or "").strip()
    if title and not _re.match(r"^\d{10,}$", title):
        return title
    summary = (item.get("summary") or "").strip()
    if summary and summary not in ("待整理", "待補充") and not summary.startswith("###"):
        first = summary.split("。")[0].split(".")[0][:60].strip()
        if first:
            return first + "…"
    return title or "(untitled)"

def semantic_recall(query: str, limit: int, vector_file: Path = VECTOR_FILE,
                    index_file: Path = INDEX_FILE,
                    topic_profile_file: Path = TOPIC_PROFILE_FILE) -> List[Dict[str, Any]]:
    """Semantic recall using vector similarity. Falls back to keyword if index missing."""
    if not vector_file.exists():
        print(f"⚠️  Vector index not found: {vector_file}", file=sys.stderr)
        print("   Falling back to keyword search. Run: python3 scripts/build_vector_index.py", file=sys.stderr)
        return []

    # Load vector index
    import json as _json
    vdata = _json.loads(vector_file.read_text(encoding="utf-8"))
    vectors = vdata.get("vectors", {})
    if not vectors:
        return []

    normalized_vectors: dict[str, list[float]] = {}
    expected_dim = None
    for rel_path, raw_vec in vectors.items():
        norm_vec = _normalize_vector(raw_vec)
        if norm_vec is None:
            print(f"⚠️  Invalid vector payload for: {rel_path}", file=sys.stderr)
            continue
        if expected_dim is None:
            expected_dim = len(norm_vec)
        elif len(norm_vec) != expected_dim:
            print(
                f"⚠️  Vector dimension mismatch for: {rel_path} "
                f"(expected {expected_dim}, got {len(norm_vec)})",
                file=sys.stderr,
            )
            continue
        normalized_vectors[rel_path] = norm_vec

    if not normalized_vectors:
        print("⚠️  No valid vectors found in vector index. Falling back to keyword search.", file=sys.stderr)
        return []

    # Embed the query
    try:
        import sys as _sys, os as _os
        _skill_dir = Path(__file__).resolve().parent.parent
        if str(_skill_dir) not in _sys.path:
            _sys.path.insert(0, str(_skill_dir))
        from tools.embedding_providers import get_provider
        provider = get_provider()
        query_vec = provider.embed(query)
        query_vec = _normalize_vector(query_vec)
        if query_vec is None:
            print("⚠️  Query embedding payload is invalid. Falling back to keyword search.", file=sys.stderr)
            return []
        if expected_dim is not None and len(query_vec) != expected_dim:
            print(
                f"⚠️  Query embedding dimension mismatch (expected {expected_dim}, got {len(query_vec)}). "
                "Falling back to keyword search.",
                file=sys.stderr,
            )
            return []
    except EnvironmentError as e:
        print(f"⚠️  {e}", file=sys.stderr)
        print("   Falling back to keyword search.", file=sys.stderr)
        return []
    except Exception as e:
        print(f"⚠️  Embedding failed: {e}", file=sys.stderr)
        return []

    # Compute cosine similarity for all cards
    scored = []
    for rel_path, vec in normalized_vectors.items():
        sim = _cosine_similarity(query_vec, vec)
        scored.append((rel_path, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:limit * 2]  # fetch extra to allow filtering

    # Load search index for metadata
    data = load_index(index_file)
    items = data.get("items", [])
    index_by_path = {
        (item.get("relative_path") or item.get("path") or ""): item
        for item in items
    }
    query_tokens = tokenize(query)
    topic_profile = load_topic_profile(topic_profile_file)
    topic_matches = get_topic_profile_matches(query_tokens, topic_profile) if topic_profile else {}

    results = []
    for rel_path, sim in top:
        if sim < 0.25:
            break
        item = index_by_path.get(rel_path)
        if not item:
            continue

        source_url = item.get("source_url") or ""
        if not source_url:
            md_path = BOOKMARKS_DIR / rel_path if not rel_path.startswith("/") else Path(rel_path)
            source_url = extract_source_url(md_path)

        kw = _keyword_score(query, item)
        hybrid = 0.65 * sim + 0.35 * kw
        adjustments = _ranking_adjustments(item, topic_matches)
        final_score = round(hybrid + adjustments["total_adjustment"], 4)
        reason = [f"語意 {sim:.0%}", f"關鍵字 {kw:.0%}"]
        if adjustments["topic_bonus"] > 0:
            reason.append(f"主題加權 +{adjustments['topic_bonus']:.2f}")
        if adjustments["summary_bonus"] != 0:
            reason.append(f"摘要調整 {adjustments['summary_bonus']:+.2f}")
        if adjustments["source_bonus"] != 0:
            reason.append(f"來源調整 {adjustments['source_bonus']:+.2f}")
        if adjustments["category_penalty"] != 0:
            reason.append(f"泛分類調整 {adjustments['category_penalty']:+.2f}")
        results.append({
            "title": _display_title(item),
            "summary": clean_summary(item.get("summary") or ""),
            "category": item.get("category") or "general",
            "tags": item.get("tags") or [],
            "relative_path": rel_path,
            "source_url": source_url,
            "score": final_score,
            "topic_boost": topic_matches.get("topic_boost", 0.0),
            "matched_categories": topic_matches.get("matched_categories", []),
            "matched_tags": topic_matches.get("matched_tags", []),
            "ranking_adjustments": adjustments,
            "relevance_reason": " + ".join(reason),
        })

    # Re-sort by hybrid score and trim to limit
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]

def recall(query: str, limit: int, min_score: int, index_file: Path = INDEX_FILE,
           topic_profile_file: Path = TOPIC_PROFILE_FILE) -> List[Dict[str, Any]]:
    data = load_index(index_file)
    items = data.get("items", [])
    query = query.strip()
    query_tokens = tokenize(query)
    if not query_tokens and not query:
        return []

    topic_profile = load_topic_profile(topic_profile_file)
    topic_matches = get_topic_profile_matches(query_tokens, topic_profile) if topic_profile else {}
    topic_boost = topic_matches.get("topic_boost", 0.0)

    results = []
    for item in items:
        base_score = float(score_item(item, query_tokens, query.lower()))
        adjustments = _ranking_adjustments(item, topic_matches)
        score = base_score + adjustments["total_adjustment"] * 10

        if score < min_score:
            continue

        rel_path = item.get("relative_path") or item.get("path") or ""
        # Use pre-indexed source_url first; fall back to file scan only if missing
        source_url = item.get("source_url") or ""
        if not source_url:
            md_path = BOOKMARKS_DIR / rel_path if rel_path and not rel_path.startswith("/") else Path(rel_path)
            source_url = extract_source_url(md_path)
        results.append({
            "title": _display_title(item),
            "summary": clean_summary(item.get("summary") or ""),
            "category": item.get("category") or "general",
            "tags": item.get("tags") or [],
            "relative_path": rel_path,
            "source_url": source_url,
            "score": round(score, 4),
            "topic_boost": topic_boost,
            "matched_categories": topic_matches.get("matched_categories", []),
            "matched_tags": topic_matches.get("matched_tags", []),
            "ranking_adjustments": adjustments,
            "relevance_reason": build_relevance_reason(item, query_tokens, topic_matches),
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
    parser.add_argument("--semantic", action="store_true",
                        help="Force semantic search (default: auto-detect)")
    parser.add_argument("--no-semantic", action="store_true",
                        help="Force keyword search even if vector index exists")
    parser.add_argument("--vector-file", default=str(VECTOR_FILE))
    parser.add_argument("--topic-profile-file", default=str(TOPIC_PROFILE_FILE))
    args = parser.parse_args()

    query = args.query or ""
    if args.query_file:
        query = Path(args.query_file).read_text(encoding="utf-8").strip()
    if not query.strip():
        print("請提供 query", file=sys.stderr)
        return 1

    # Auto-detect: use semantic if vector index exists, unless --no-semantic is set
    vector_path = Path(args.vector_file)
    use_semantic = (not args.no_semantic) and (args.semantic or vector_path.exists())

    topic_profile_path = Path(args.topic_profile_file)

    search_mode = "keyword"
    if use_semantic:
        results = semantic_recall(query, args.limit, vector_path, Path(args.index_file), topic_profile_path)
        if results:
            search_mode = "semantic"
        else:
            # fallback to keyword if semantic returned nothing
            results = recall(query, args.limit, args.min_score, Path(args.index_file), topic_profile_path)
            search_mode = "keyword_fallback"
    else:
        results = recall(query, args.limit, args.min_score, Path(args.index_file), topic_profile_path)

    _mode_labels = {
        "semantic": "🔍 語意向量搜尋",
        "keyword": "🔤 關鍵字搜尋",
        "keyword_fallback": "🔤 關鍵字搜尋（語意降級）",
    }
    if not args.json:
        print(f"[搜尋模式：{_mode_labels.get(search_mode, search_mode)}]")

    if args.json:
        print(json.dumps({"query": query, "results": results, "search_mode": search_mode}, ensure_ascii=False, indent=2))
    elif args.format == "prompt":
        print_prompt(results, query)
    elif args.format == "chat":
        print_chat(results, query)
    else:
        print_markdown(results, query)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
