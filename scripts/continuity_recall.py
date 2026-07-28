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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths
import xkb_text

WORKSPACE = xkb_paths.WORKSPACE
_SKILL_DIR = xkb_paths.SKILL_DIR
MEMORY_DIR = xkb_paths.DATA_DIR
WIKI_TOPICS_DIR = xkb_paths.WIKI_TOPICS_DIR
MEMORY_MD = xkb_paths.MEMORY_MD

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
    return xkb_text.tokenize(text, STOPWORDS)


def _score_text(tokens: list[str], text: str) -> float:
    """Simple token overlap score."""
    if not tokens or not text:
        return 0.0
    text_lower = text.lower()
    # Phrase bonus: check if two consecutive tokens appear adjacent
    phrase_bonus = 0.0
    for i in range(len(tokens) - 1):
        phrase = tokens[i] + tokens[i + 1]
        if phrase in text_lower or f"{tokens[i]} {tokens[i+1]}" in text_lower:
            phrase_bonus += 0.5
    return xkb_text.overlap_score(tokens, text) + phrase_bonus


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


# ── 語意召回 ──────────────────────────────────────────────────────────────────
#
# XKB 的核心不是資料庫，是根據語意主動召回。但 wiki 這一層原本只有字串比對：
# 中文沒有空格，切 n-gram 之後「之前」「怎麼」「處理」會命中任何一份文件，
# 於是「我們之前怎麼處理碳盤查的」撈回一堆無關結果，還給出 0.42 的分數。
#
# 語意分數說得出「沒有夠像的」——實測那句在 wiki 裡最高只有 0.574，
# 因為知識庫裡本來就沒有碳盤查的內容。字串比對永遠說不出這句話。

WIKI_MIN_SIMILARITY = float(os.getenv("XKB_WIKI_MIN_SIMILARITY", "0.65"))

_VECTORS: dict[str, list[float]] | None = None
_PROVIDER = None


SEMANTIC_PREFIXES = {"wiki/topics/": "wiki_semantic", "memory/": "memory_semantic"}


def _load_semantic_vectors() -> dict[str, list[float]]:
    """載入 wiki 與記憶檔的段落向量（已單位化）。

    優先讀二進位小檔：整份 vector_index.json 有 8,000 多個卡片向量，
    存成 JSON 光載入就要 5 秒，而召回是每句話都要跑的。
    """
    global _VECTORS
    if _VECTORS is not None:
        return _VECTORS
    _VECTORS = {}

    meta_path = Path(os.getenv("XKB_SEMANTIC_INDEX",
                               str(xkb_paths.BOOKMARKS_DIR / "semantic_index.json")))
    bin_path = meta_path.with_suffix(".bin")
    if meta_path.exists() and bin_path.exists():
        try:
            import array
            with meta_path.open(encoding="utf-8") as fh:
                meta = json.load(fh)
            dims, keys = int(meta["dims"]), list(meta["keys"])
            packed = array.array("f")
            payload = bin_path.read_bytes()

            # 二進位格式沒有自我描述能力，對不上時不會拋錯，只會安靜地
            # 切出空的或錯位的向量——分數變成 0，看起來就像「知識庫沒東西」。
            # 所以寧可大聲退回 JSON，也不要拿可能錯位的資料去算相似度。
            problems = []
            if meta.get("byteorder") and meta["byteorder"] != sys.byteorder:
                problems.append(f"byteorder {meta['byteorder']} != {sys.byteorder}")
            if meta.get("itemsize") and int(meta["itemsize"]) != packed.itemsize:
                problems.append(f"itemsize {meta['itemsize']} != {packed.itemsize}")
            expected = len(keys) * dims * packed.itemsize
            if len(payload) != expected:
                problems.append(f"size {len(payload)} != expected {expected}"
                                f"（keys 與 .bin 不同步，可能只重建了其中一個）")
            if problems:
                raise ValueError("; ".join(problems))

            packed.frombytes(payload)
            _VECTORS = {
                key: packed[i * dims:(i + 1) * dims].tolist()
                for i, key in enumerate(keys)
            }
            return _VECTORS
        except (OSError, ValueError, KeyError) as exc:
            print(f"（semantic index unusable, falling back to JSON: {exc}）", file=sys.stderr)
            _VECTORS = {}

    # 退路：直接讀完整索引（慢，但至少能動）
    try:
        with xkb_paths.VECTOR_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
        _VECTORS = {
            k: v for k, v in (data.get("vectors") or {}).items()
            if any(k.startswith(prefix) for prefix in SEMANTIC_PREFIXES)
            and not k.startswith("memory/cards/")
        }
    except (OSError, ValueError):
        pass
    return _VECTORS


def _load_embedding_env() -> None:
    """把 openclaw.json 裡的 embedding 設定補進環境變數（已存在的不覆蓋）。"""
    path = Path(os.getenv("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
    if not path.exists():
        return
    try:
        with path.open(encoding="utf-8") as fh:
            env = (json.load(fh).get("env") or {})
    except (OSError, ValueError):
        return
    for key in ("GEMINI_API_KEY", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL",
                "OPENAI_API_KEY", "OLLAMA_BASE_URL"):
        if env.get(key) and not os.getenv(key):
            os.environ[key] = str(env[key])
    # 有 key 但沒指定供應商時，預設走 gemini——索引就是用它建的
    if os.getenv("GEMINI_API_KEY") and not os.getenv("EMBEDDING_PROVIDER"):
        os.environ["EMBEDDING_PROVIDER"] = "gemini"


def _embed_query(query: str) -> list[float] | None:
    """拿不到 embedding 就回 None，呼叫端會退回字串比對。"""
    global _PROVIDER
    if _PROVIDER is None:
        try:
            # MCP server 是被 OpenClaw 叫起來的，未必帶著這些環境變數。
            # 沿用 xbrain_recall 的做法，從 openclaw.json 補上，否則語意召回會
            # 在正式環境靜默退回字串比對——正是今天修了一整天的那種失敗方式。
            _load_embedding_env()
            sys.path.insert(0, str(xkb_paths.SKILL_DIR))
            from tools.embedding_providers import get_provider
            _PROVIDER = get_provider()
        except Exception as exc:
            print(f"（semantic recall unavailable: {exc}）", file=sys.stderr)
            _PROVIDER = False
    if not _PROVIDER:
        return None
    try:
        return _PROVIDER.embed(query)
    except Exception as exc:
        print(f"（embedding failed: {exc}）", file=sys.stderr)
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na * nb > 1e-9 else 0.0


def _section_text(topic_file: str, section: str) -> str:
    path = WIKI_TOPICS_DIR / topic_file
    try:
        content = re.sub(r"^---\n.*?\n---\n", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    except OSError:
        return ""
    for title, body in _split_into_sections(content):
        if title == section:
            return re.sub(r"\n+", " ", body).strip()
    return ""


def _memory_section_text(filename: str, section: str) -> str:
    for base in (MEMORY_DIR, MEMORY_MD.parent):
        path = base / filename
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for title, body in _split_into_sections(content):
            if title == section:
                return re.sub(r"\s+", " ", body).strip()
        return re.sub(r"\s+", " ", content).strip()
    return ""


def recall_semantic(query: str, top_k: int = 2) -> list[RecallResult] | None:
    """向量召回。回傳 None 代表「語意能力不可用」，空 list 代表「真的沒有夠相關的」。

    這兩者必須分得開：不可用要退回字串比對，沒有夠相關的就該安靜。
    """
    vectors = _load_semantic_vectors()
    if not vectors:
        return None
    query_vector = _embed_query(query)
    if query_vector is None:
        return None

    scored: list[tuple[float, str]] = []
    for key, vec in vectors.items():
        scored.append((_cosine(query_vector, vec), key))
    scored.sort(reverse=True)

    results: list[RecallResult] = []
    seen_topics: set[str] = set()
    for similarity, key in scored:
        if similarity < WIKI_MIN_SIMILARITY or len(results) >= top_k:
            break
        prefix = next((p for p in SEMANTIC_PREFIXES if key.startswith(p)), "")
        source_type = SEMANTIC_PREFIXES.get(prefix, "wiki_semantic")
        rest, _, section = key[len(prefix):].partition("#")
        if rest in seen_topics:            # 同一份文件只取最相關的一段
            continue
        seen_topics.add(rest)
        is_wiki = source_type == "wiki_semantic"
        excerpt = _section_text(rest, section) if is_wiki else _memory_section_text(rest, section)
        results.append(RecallResult(
            source_type=source_type,
            source_file=f"{prefix}{rest}",
            section=section,
            excerpt=excerpt[:200],
            score=round(similarity, 3),
            url=f"wiki/topics/{Path(rest).stem}" if is_wiki else "",
        ))
    return results


def recall_from_wiki(query: str, top_k: int = 2, semantic: bool = True) -> list[RecallResult]:
    """Search wiki/topics/*.md for relevant content.

    semantic=True 時優先用向量。語意可用但沒有夠相關的內容，就回空——
    不再退回字串比對，否則剛擋掉的雜訊會從後門進來。
    """
    if semantic:
        semantic_results = recall_semantic(query, top_k)
        if semantic_results is not None:
            return semantic_results

    return _recall_from_wiki_keyword(query, top_k)


def _recall_from_wiki_keyword(query: str, top_k: int = 2) -> list[RecallResult]:
    """字串比對版本。語意不可用時的退路。"""
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

def recall(query: str, source: str = "both", top_k: int = 5,
           semantic: bool = True) -> list[RecallResult]:
    """Unified entry: search memory and/or wiki.

    語意可用時，memory 與 wiki 都走同一次向量查詢——兩層的段落都在同一份索引裡，
    分開查只會多打一次 embedding API。語意不可用才各自退回字串比對。
    """
    if semantic and source == "both":
        semantic_results = recall_semantic(query, top_k=top_k)
        if semantic_results is not None:
            return semantic_results

    results: list[RecallResult] = []
    if source in ("memory", "both"):
        results.extend(recall_from_memory(query, top_k=3))
    if source in ("wiki", "both"):
        results.extend(recall_from_wiki(query, top_k=2, semantic=semantic))
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
