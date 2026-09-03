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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths
import xkb_text
from xkb_mmr import mmr_select
from xkb_provenance import is_self_derived

WORKSPACE_DIR = xkb_paths.WORKSPACE
BOOKMARKS_DIR = xkb_paths.BOOKMARKS_DIR
INDEX_FILE = xkb_paths.INDEX_FILE
VECTOR_FILE = xkb_paths.VECTOR_FILE
TOPIC_PROFILE_FILE = xkb_paths.TOPIC_PROFILE_FILE
_SKILL_DIR = xkb_paths.SKILL_DIR
WIKI_TOPICS_DIR = xkb_paths.WIKI_TOPICS_DIR
GENERIC_CATEGORIES = {"general", "99-general", "other", "misc", "uncategorized"}
LOW_SIGNAL_SUMMARIES = {"（待整理）", "待整理", "todo", "tbd", "n/a"}
LOW_SIGNAL_TITLES = {"(untitled)", "untitled", "tweet"}
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
    return xkb_text.tokenize(text, STOPWORDS)


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


def _is_valid_source_url(url: str) -> bool:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    x_status = re.search(r"https?://(?:x|twitter)\.com/[^\s/]+/status/(\d{15,20})(?:\b|/|\?)", url)
    x_i_status = re.search(r"https?://x\.com/i/status/(\d{15,20})(?:\b|/|\?)", url)
    if ("x.com" in url or "twitter.com" in url) and not (x_status or x_i_status):
        return False
    return True


def _normalize_source_url(url: str) -> str:
    url = (url or "").strip().strip('"')
    if not _is_valid_source_url(url):
        return ""
    return url


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
            normalized = _normalize_source_url(url)
            if normalized:
                return normalized
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
    tokens = tokenize(query)
    if not tokens:
        return 0.0
    text = " ".join([
        (item.get("title") or "").lower(),
        " ".join(item.get("tags") or []).lower(),
        (item.get("summary") or "").lower(),
    ])
    return xkb_text.overlap_score(tokens, text)


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


def _title_quality(title: str) -> float:
    title = (title or "").strip()
    lowered = title.lower()
    if not title:
        return -0.12
    if lowered in LOW_SIGNAL_TITLES:
        return -0.15
    if re.fullmatch(r"tweet\s+\d{15,20}", lowered):
        return -0.14
    if re.fullmatch(r"\d{15,20}", title):
        return -0.16
    if re.match(r"^\d{4}-\d{2}-\d{2}-", title) and len(title) < 42:
        return -0.08
    if len(title) < 8:
        return -0.05
    return 0.03


def _category_penalty(item: Dict[str, Any]) -> float:
    category = (item.get("category") or "").strip().lower()
    return -0.08 if category in GENERIC_CATEGORIES else 0.0


# 回音室防護（TODOS 2026-07-13）：對話蒸餾而來的知識（self-derived）召回時降權，
# 避免「收藏 → 召回 → 討論 → 再入庫」閉環讓既有觀點自我強化。
SELF_DERIVED_PENALTY = float(os.getenv("XKB_SELF_DERIVED_PENALTY", "-0.15"))


# 索引項目實際有的欄位：category, enriched, mtime, path, relative_path,
# searchable, size, source_type, source_url, summary, tags, title。
# 原本這裡讀的是 provenance / content / source_file / section_text——四個
# 都不存在，所以這道防護從第一天起就沒有觸發過一次。
PROVENANCE_FIELDS = ("summary", "searchable", "relative_path", "path", "title")


def _provenance_only(item: Dict[str, Any]) -> Dict[str, float]:
    """只帶出處降權的調整包。gbrain 路徑沒有主題加權可算。"""
    penalty = _provenance_penalty(item)
    return {"provenance": penalty, "total_adjustment": penalty}


def _provenance_penalty(item: Dict[str, Any]) -> float:
    if (item.get("provenance") or "").strip().lower() == "self-derived":
        return SELF_DERIVED_PENALTY
    # 路徑要一起看：self-derived 的標記常常在 memory/YYYY-MM-DD 這種路徑上，
    # 而不是在摘要文字裡。
    text = " ".join(str(item.get(k) or "") for k in PROVENANCE_FIELDS)
    if is_self_derived(text):
        return SELF_DERIVED_PENALTY
    return 0.0


def _should_filter_result(item: Dict[str, Any], source_url: str) -> bool:
    title = (item.get("title") or "").strip()
    summary = clean_summary(item.get("summary") or "")
    category = (item.get("category") or "").strip().lower()

    if item.get("excluded"):
        return True
    if summary in LOW_SIGNAL_SUMMARIES and not source_url:
        return True
    if re.fullmatch(r"\d{15,20}", title):
        return True
    if re.fullmatch(r"tweet\s+\d{15,20}", title.lower()) and summary in LOW_SIGNAL_SUMMARIES:
        return True
    if category in GENERIC_CATEGORIES and summary in LOW_SIGNAL_SUMMARIES:
        return True
    return False


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
    title_bonus = _title_quality(item.get("title") or "")
    source_bonus = _source_quality(item)
    category_penalty = _category_penalty(item)
    provenance_penalty = _provenance_penalty(item)

    return {
        "topic_bonus": topic_bonus,
        "summary_bonus": summary_bonus,
        "title_bonus": title_bonus,
        "source_bonus": source_bonus,
        "category_penalty": category_penalty,
        "provenance_penalty": provenance_penalty,
        "total_adjustment": round(topic_bonus + summary_bonus + title_bonus + source_bonus + category_penalty + provenance_penalty, 4),
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

def gbrain_semantic_recall(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Recall via gbrain hybrid search (pgvector RRF + Gemini).
    Returns same format as semantic_recall() for drop-in replacement.
    Falls back silently if gbrain is not available.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from xbrain_recall import xbrain_query as gbrain_query
    except ImportError:
        return []

    try:
        raw = gbrain_query(query, limit=limit)
    except RuntimeError:
        return []

    results = []
    for item in raw:
        chunk = item.get("chunk_text", "")
        # Extract title from first markdown heading
        title_m = re.search(r"^#\s+(.+)$", chunk, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else item.get("title") or item.get("slug", "")

        # Extract summary from 雙語摘要 ZH line
        zh_m = re.search(r"ZH[:\s]+(.+?)(?:\n|EN:|$)", chunk, re.IGNORECASE)
        summary = zh_m.group(1).strip() if zh_m else chunk[:120].replace("\n", " ").strip()

        source_url = item.get("source_url") or ""
        score = item.get("score", 0.0)
        slug = item.get("slug", "")

        # 出處懲罰算一次就好。原本同一個 dict literal 建了兩次，
        # 一次進分數、一次進說明——同一個量兩個來源，正是這個專案的老毛病。
        _adjustments = _provenance_only({
            "summary": summary,
            "relative_path": f"cards/{slug}.md",
            "title": title,
        })
        results.append({
            "title": title,
            "summary": summary,
            "category": item.get("type", "knowledge-card"),
            "tags": [],
            "relative_path": f"cards/{slug}.md",
            "source_url": source_url,
            # 懲罰要進分數。原本只放進 ranking_adjustments 給人看，score
            # 還是原始 RRF——於是回音室防護在這台機器實際會走的路徑上，
            # 算得出來、顯示得出來、就是不會影響排序。
            "score": round(score + _adjustments["total_adjustment"], 4),
            "topic_boost": 0.0,
            "matched_categories": [],
            "matched_tags": [],
            # gbrain 是這台機器上實際會走的模式，而它原本完全不算調整——
            # 於是回音室防護在主要路徑上等於不存在。
            # 要看組好的欄位（摘要、路徑），不是 gbrain 回傳的原始 item：
            # self-derived 的線索在 relative_path 與 summary 上。
            "ranking_adjustments": _adjustments,
            "relevance_reason": f"gbrain 語意+關鍵字混合 RRF ({score:.4f})",
        })
    # 另外兩條 semantic_recall 都是 sort 之後才 [:limit]，只有這條直接回
    # gbrain 的原始順序——而這條正是這台機器實際會走的。同一個函式契約、
    # 三個實作、兩個排序，於是任何加減分在這裡都不會改變輸出。
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def wiki_recall(query: str, limit: int = 2) -> List[Dict[str, Any]]:
    """第一層召回：從 wiki/topics/*.md 找合成知識段落。"""
    if not WIKI_TOPICS_DIR.exists():
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    results = []
    for path in WIKI_TOPICS_DIR.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # 從 frontmatter 取 title
        fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        topic_title = path.stem
        if fm_match:
            title_m = re.search(r"^title:\s*(.+)$", fm_match.group(1), re.MULTILINE)
            if title_m:
                topic_title = title_m.group(1).strip().strip('"')

        # 移除 frontmatter，切成 ## 段落
        body = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL).strip()
        sections = re.split(r"\n(?=##+ )", body)

        for section in sections:
            section = section.strip()
            if not section:
                continue
            first_line = section.split("\n")[0]
            section_title = re.sub(r"^#+\s*", "", first_line).strip()
            section_body = "\n".join(section.split("\n")[1:]).strip()
            if not section_body:
                continue

            text_lower = (section_title + " " + section_body).lower()
            score = 0
            for token in query_tokens:
                if token in section_title.lower():
                    score += 6
                elif token in text_lower:
                    score += 2

            if score < 4:
                continue

            # 擷取有意義的摘錄（跳過純分隔線）
            excerpt_lines = [l for l in section_body.split("\n")
                             if l.strip() and not re.fullmatch(r"-{3,}", l.strip())]
            excerpt = " ".join(excerpt_lines)[:240].strip()
            if len(excerpt) >= 240:
                excerpt = excerpt.rsplit(" ", 1)[0] + "…"

            results.append({
                "topic_title": topic_title,
                "section_title": section_title,
                "excerpt": excerpt,
                "path": f"wiki/topics/{path.name}",
                "score": score,
            })

    results.sort(key=lambda x: x["score"], reverse=True)

    # 原本是「每個 topic 只留最高分的一段」。那條規則兩邊都鈍：同一頁的第二段
    # 就算講的是完全不同的事也會被丟掉，而兩段幾乎一樣的內容只要分屬不同頁就
    # 都會留下。治理開始讓每則升級的論點各自成段之後，一頁可以有好幾百段，
    # 這條規則等於讓一頁的內容在每次召回時互相淘汰，只出得來一段。
    #
    # 改成 MMR：一段要「比已選的多說了些什麼」才留。分數接近時它會挑不重複的，
    # 分數差很多時不會為了多樣性硬塞——後者靠函式內的正規化保證。
    return mmr_select(
        results,
        limit,
        text=lambda item: f"{item['section_title']} {item['excerpt']}",
    )


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

    # Compute cosine similarity, then aggregate to card level
    # （論點級向量鍵為 relpath#kpN，同卡取最高分 — TODOS 2026-07-13）
    best: dict[str, float] = {}
    for key, vec in normalized_vectors.items():
        sim = _cosine_similarity(query_vec, vec)
        base = key.split("#", 1)[0]
        if sim > best.get(base, -1.0):
            best[base] = sim
    scored = sorted(best.items(), key=lambda x: x[1], reverse=True)
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

        source_url = _normalize_source_url(item.get("source_url") or "")
        if not source_url:
            md_path = BOOKMARKS_DIR / rel_path if not rel_path.startswith("/") else Path(rel_path)
            source_url = extract_source_url(md_path)

        kw = _keyword_score(query, item)
        hybrid = 0.65 * sim + 0.35 * kw
        adjustments = _ranking_adjustments(item, topic_matches)
        final_score = round(hybrid + adjustments["total_adjustment"], 4)
        if _should_filter_result(item, source_url):
            continue
        reason = [f"語意 {sim:.0%}", f"關鍵字 {kw:.0%}"]
        if adjustments["topic_bonus"] > 0:
            reason.append(f"主題加權 +{adjustments['topic_bonus']:.2f}")
        if adjustments["summary_bonus"] != 0:
            reason.append(f"摘要調整 {adjustments['summary_bonus']:+.2f}")
        if adjustments["title_bonus"] != 0:
            reason.append(f"標題調整 {adjustments['title_bonus']:+.2f}")
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
        source_url = _normalize_source_url(item.get("source_url") or "")
        if not source_url:
            md_path = BOOKMARKS_DIR / rel_path if rel_path and not rel_path.startswith("/") else Path(rel_path)
            source_url = extract_source_url(md_path)
        if _should_filter_result(item, source_url):
            continue
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


def print_markdown(wiki_hits: List[Dict[str, Any]], results: List[Dict[str, Any]], query: str) -> None:
    if not wiki_hits and not results:
        print("沒有找到適合主動召回的知識。")
        return

    print(f"# 對話主動召回結果\n")
    print(f"查詢：{query}\n")

    if wiki_hits:
        print("## Wiki 知識層\n")
        for hit in wiki_hits:
            print(f"### {hit['topic_title']} — {hit['section_title']}")
            print(f"{hit['excerpt']}")
            print(f"- 來源：{hit['path']}\n")

    if results:
        print("## 原始卡片層\n")
        for idx, item in enumerate(results, start=1):
            print(f"### {idx}. {item['title']}")
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


def print_prompt(wiki_hits: List[Dict[str, Any]], results: List[Dict[str, Any]], query: str) -> None:
    if not wiki_hits and not results:
        print("NO_RECALL")
        return

    if wiki_hits:
        print("根據你的知識庫，這個主題有以下整理：")
        for hit in wiki_hits:
            print(f"- [{hit['topic_title']}] {hit['section_title']}：{hit['excerpt'][:120]}")

    if results:
        print("相關原始書籤：")
        for item in results:
            print(f"- {item['title']}")
            if item.get("summary"):
                print(f"  摘要：{item['summary'][:120]}")
            print(f"  為什麼相關：{item['relevance_reason']}")
            if item.get("source_url"):
                print(f"  原文：{item['source_url']}")
            else:
                print(f"  檔案：{item['relative_path']}")


def print_chat(wiki_hits: List[Dict[str, Any]], results: List[Dict[str, Any]], query: str) -> None:
    if not wiki_hits and not results:
        print("NO_RECALL")
        return

    if wiki_hits:
        hit = wiki_hits[0]
        print(f"[知識庫] {hit['topic_title']}：{hit['excerpt'][:120]}")

    if results:
        top = results[:2]
        print("相關書籤：")
        for item in top:
            summary = item.get("summary") or "這篇和你現在聊的主題接近。"
            reason = item.get("relevance_reason") or "主題接近"
            print(f"- {item['title']}：{summary[:90]}")
            print(f"  為什麼相關：{reason}")
            if item.get("source_url"):
                print(f"  原文：{item['source_url']}")


MODE_LABELS = {
    "gbrain": "gbrain 混合搜尋（RRF + Gemini）",
    "semantic": "語意向量搜尋",
    "keyword": "關鍵字搜尋",
    "keyword_fallback": "關鍵字搜尋（語意降級）",
}


_IDENT_SPLIT = re.compile(r"[\s,;，、。]+")
_IDENT_HARD_FIELDS = ("id", "source_url", "relative_path", "path")
_IDENT_SOFT_FIELDS = ("title", "searchable", "summary", "tags")


def _identifier_tokens(query: str) -> List[str]:
    """查詢裡看起來像識別碼、而不是普通詞的 token（原樣保留，不再切分）。

    命中條件：含數字、含底線、或長度 >= 8 的純 ASCII 連字號字串。斷詞器會把
    gpt-5.6-luna 從 . 切成兩半，這裡不經過它，所以識別碼保持完整。

    引號不是免死金牌。一度讓「引號括住就一律算識別碼」，於是隨口一句
    `他說那是個 "good idea"` 就讓幾張字面沾到邊的卡片拿到字面命中的特權
    （跳過餘弦門檻、強制 side_hint、繞過 light 掃描的成本閘門）。引號片段
    照樣要通過 looks_id：引號裡的 "gpt-5.6" 本來就會過，"machine learning
    survey" 不會。
    """
    # 引號內的內容拆開來，跟一般 token 走同一條 looks_id 判斷
    parts = re.findall(r'"([^"]+)"', query)
    parts += _IDENT_SPLIT.split(re.sub(r'"[^"]*"', " ", query))
    out: List[str] = []
    for tok in parts:
        if not tok:
            continue
        # 去掉句尾標點：_IDENT_SPLIT 會切 CJK 的「。」但不切 ASCII 的「.」，
        # 於是 "gpt-5.6." 會帶著尾點，t in blob 就對不上 "gpt-5.6"。
        t = tok.strip().strip('".,;:!?()[] ').lower()
        if len(t) < 3:
            continue
        digits = sum(c.isdigit() for c in t)
        alpha = sum(c.isalpha() for c in t)
        looks_id = (
            # 純數字要夠長才算識別碼（tweet ID 有 18-19 位）；四位數多半是年份
            (digits >= 10 and alpha == 0)
            # 英數混合、帶數字：型號、錯誤碼（gpt-5.6、err_1042）
            or (digits and alpha and ("_" in t or "-" in t or "." in t or len(t) >= 6))
            # 底線分隔的識別碼
            or "_" in t
            # 夠長的連字號 ASCII slug（repo 名）
            or (len(t) >= 8 and "-" in t and t.isascii() and " " not in t)
        )
        if looks_id:
            out.append(t)
    return list(dict.fromkeys(out))


def identifier_recall(query: str, items: List[Dict[str, Any]], limit: int,
                      *, _tokens: List[str] | None = None) -> List[Dict[str, Any]]:
    """字面命中識別碼的卡片。分數高到能排進召回前段，但不保證第一。

    向量腿對錯誤碼、repo slug、tweet ID 這種字串算不出意義，於是它們進不了
    「純向量排名的前 2*limit」候選集，關鍵字腿也就沒機會重排到它們——救不回
    沒入選的東西。這條腿直接掃索引補上。
    """
    idents = _tokens if _tokens is not None else _identifier_tokens(query)
    if not idents:
        return []
    # 一個 token 命中太多卡片 => 它其實不是識別碼（例如年份、常見縮寫）。
    # 真正的 repo slug / tweet ID / 型號只會命中個位數張卡。
    _MAX_IDENT_HITS = 8
    _blobs = []
    _noise = set()
    for it in items:
        b = (" ".join(str(it.get(k) or "") for k in _IDENT_HARD_FIELDS) + " " +
             " ".join((" ".join(str(x) for x in it.get(k)) if isinstance(it.get(k), list)
                        else str(it.get(k) or "")) for k in _IDENT_SOFT_FIELDS)).lower()
        _blobs.append(b)
    for t in idents:
        if sum(1 for b in _blobs if t in b) > _MAX_IDENT_HITS:
            _noise.add(t)
    if all(t in _noise for t in idents):
        return []
    hits: List[tuple] = []
    for item, blob in zip(items, _blobs):
        hard = " ".join(str(item.get(k) or "") for k in _IDENT_HARD_FIELDS).lower()
        matched = [t for t in idents if t in blob and t not in _noise]
        if not matched:
            continue
        in_hard = sum(1 for t in matched if t in hard)
        # 命中在 id / url / 路徑 這種「硬」欄位 => 幾乎確定就是要找的那張卡
        score = round(min(0.99, 0.72 + 0.12 * min(in_hard, 2) + 0.03 * (len(matched) - 1)), 4)
        rel_path = item.get("relative_path") or item.get("path") or ""
        hits.append((score, {
            "title": _display_title(item),
            "summary": clean_summary(item.get("summary") or ""),
            "category": item.get("category") or "general",
            "tags": item.get("tags") or [],
            "relative_path": rel_path,
            "source_url": _normalize_source_url(item.get("source_url") or ""),
            "score": score,
            "score_scale": "card_semantic",
            # 字面命中：相關性由「索引裡真的有這個字串」建立，不是餘弦。
            # 下游的相似度閘門要放這種結果過，不能用 0.55 的餘弦門檻砍掉。
            "match_kind": "literal",
            "topic_boost": 0.0,
            "matched_categories": [],
            "matched_tags": [],
            "ranking_adjustments": {"total_adjustment": 0.0},
            "relevance_reason": f"字面命中識別碼：{', '.join(matched)}",
        }))
    hits.sort(key=lambda x: x[0], reverse=True)
    return [h[1] for h in hits[:limit]]


def search(
    query: str,
    limit: int = 3,
    min_score: int = 6,
    *,
    index_file: Path | None = None,
    vector_file: Path | None = None,
    topic_profile_file: Path | None = None,
    no_wiki: bool = False,
    no_semantic: bool = False,
    force_semantic: bool = False,
    force_gbrain: bool = False,
    identifier_only: bool = False,
) -> dict:
    """兩層召回：先查合成過的 wiki 知識，再查卡片細節。

    這段原本長在 main() 裡，所以想用它的人只能另外開一個 Python 行程——
    父行程已經為了語意召回載入過向量索引，子行程再載入一次，一次 hard 召回
    因此多付 1.3 秒，而 hook 只給六秒。搬出來之後兩邊呼叫同一份程式，
    main() 回歸它本來的角色：命令列的殼。
    """
    index_path = Path(index_file) if index_file else INDEX_FILE
    vector_path = Path(vector_file) if vector_file else VECTOR_FILE
    profile_path = Path(topic_profile_file) if topic_profile_file else TOPIC_PROFILE_FILE

    # 第一層：wiki 知識層（先查合成知識）。identifier_only 是 light 掃描的
    # 純識別碼模式，呼叫端已經自己查過 wiki，這裡再查一次只是丟掉——跳過。
    wiki_hits: List[Dict[str, Any]] = ([] if (no_wiki or identifier_only)
                                       else wiki_recall(query, limit=2))

    # 第二層：cards 細節層（gbrain > semantic > keyword）
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from xbrain_recall import GBRAIN_AVAILABLE as _gbrain_available, GEMINI_API_KEY as _gemini_key
    except ImportError:
        _gbrain_available = False
        _gemini_key = ""
    use_gbrain = force_gbrain or (not no_semantic and _gbrain_available and bool(_gemini_key))

    search_mode = "keyword"
    if identifier_only:
        # light 掃描專用：跳過所有語意/關鍵字後端，只留識別碼那條腿。
        # 下面的識別碼合併對「沒有識別碼 token」的查詢本來就是 no-op。
        results = []
        search_mode = "identifier_only"
    elif use_gbrain:
        results = gbrain_semantic_recall(query, limit)
        if results:
            search_mode = "gbrain"
        else:
            results = recall(query, limit, min_score, index_path, profile_path)
            search_mode = "keyword_fallback"
    elif (not no_semantic) and (force_semantic or vector_path.exists()):
        results = semantic_recall(query, limit, vector_path, index_path, profile_path)
        if results:
            search_mode = "semantic"
        else:
            results = recall(query, limit, min_score, index_path, profile_path)
            search_mode = "keyword_fallback"
    else:
        results = recall(query, limit, min_score, index_path, profile_path)

    # 識別碼精確腿：任何後端跑完後都併一次。查詢沒有識別碼 token 時
    # _identifier_tokens 回空，連索引都不載入，行為與改動前完全相同。
    _q_idents = _identifier_tokens(query)
    if _q_idents:
        # 識別碼腿是**補充**：非 light 路徑上 gbrain 已經回了好結果，本地索引
        # 讀不到只該讓這條補充腿降級，不該把 gbrain 的結果一起丟掉——那正是
        # 第八輪審查抓到的「往上傳、連好結果一起炸」。所以本地 catch，但要
        # 出聲（不是第七輪那個靜默的 _idx_items = []）。
        try:
            _idx_items = load_index(index_path).get("items", [])
        except (OSError, ValueError, AttributeError) as _e:  # OSError 涵蓋
            # FileNotFoundError / PermissionError / IsADirectoryError
            try:
                import xkb_failures as _xf
                _xf.note("identifier index", _e)
            except Exception:  # noqa: BLE001 — 連告警都載不進來也不能因此中止
                pass
            _idx_items = []
        exact = identifier_recall(query, _idx_items, limit, _tokens=_q_idents)
        if exact:
            def _rk(r: Dict[str, Any]) -> str:
                return r.get("relative_path") or r.get("source_url") or r.get("title") or ""
            # 上限 3：留下位子給語意結果，不然 len(exact)==limit 時
            # 語意結果會在 rank() 重排之前就被整段截掉。
            exact = exact[: min(3, limit)]
            seen = {_rk(r) for r in exact}
            results = (exact + [r for r in results if _rk(r) not in seen])[: max(limit, len(exact))]
            # 不動 search_mode：recall_router 用它判斷「要不要把關鍵字分數
            # 換算尺度」，加後綴會讓那個判斷失效，未換算的關鍵字分數就直接
            # 壓過所有語意結果。識別碼的資訊改放在各筆結果的 match_kind 上。

    return {
        "query": query,
        "wiki_hits": wiki_hits,
        "results": results,
        "search_mode": search_mode,
    }


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
    parser.add_argument("--gbrain", action="store_true",
                        help="Use gbrain hybrid search backend (pgvector + RRF + Gemini)")
    parser.add_argument("--vector-file", default=str(VECTOR_FILE))
    parser.add_argument("--topic-profile-file", default=str(TOPIC_PROFILE_FILE))
    parser.add_argument("--no-wiki", action="store_true",
                        help="Skip wiki layer, search cards only")
    args = parser.parse_args()

    query = args.query or ""
    if args.query_file:
        query = Path(args.query_file).read_text(encoding="utf-8").strip()
    if not query.strip():
        print("請提供 query", file=sys.stderr)
        return 1

    # 檢索本身在 search() 裡。原本這裡有一份一模一樣的副本，於是 CLI 與
    # hook 走的是兩份會各自漂移的程式——而 commit message 宣稱這裡已經只是
    # 命令列的殼了。
    found = search(
        query,
        limit=args.limit,
        min_score=args.min_score,
        index_file=Path(args.index_file),
        vector_file=Path(args.vector_file),
        topic_profile_file=Path(args.topic_profile_file),
        no_wiki=args.no_wiki,
        no_semantic=args.no_semantic,
        force_semantic=args.semantic,
        force_gbrain=args.gbrain,
    )
    wiki_hits = found["wiki_hits"]
    results = found["results"]
    search_mode = found["search_mode"]

    if not args.json:
        wiki_label = "" if args.no_wiki else f" + Wiki({'有' if wiki_hits else '無'}命中)"
        print(f"[搜尋模式：{MODE_LABELS.get(search_mode, search_mode)}{wiki_label}]")

    if args.json:
        print(json.dumps({
            "query": query,
            "wiki_hits": wiki_hits,
            "results": results,
            "search_mode": search_mode,
        }, ensure_ascii=False, indent=2))
    elif args.format == "prompt":
        print_prompt(wiki_hits, results, query)
    elif args.format == "chat":
        print_chat(wiki_hits, results, query)
    else:
        print_markdown(wiki_hits, results, query)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
