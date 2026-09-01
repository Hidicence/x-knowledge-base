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
import xkb_provenance
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


def _result(*, source_type, source_file, section, excerpt, score, url="") -> RecallResult:
    """Build a result with the bookkeeping taken out.

    Governance stamps every promoted line with the candidate fingerprint that
    makes its batch reversible. A recall for 碳盤查 was answering with 64
    characters of hex in the middle of the sentence. There are three places
    that build a result and more than one that renders it, so the removal
    belongs here, once, where the next one added inherits it.
    """
    return RecallResult(
        source_type=source_type,
        source_file=source_file,
        section=xkb_provenance.strip_markers(section),
        excerpt=xkb_provenance.strip_markers(excerpt),
        score=score,
        url=url,
    )


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
            candidates.append(_result(
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


_CARD_KEYS: dict[str, int] | None = None
_CARD_DIMS = 0

# 卡片相關度下限。gbrain 回的是 RRF 排名分數，不是相關度——
# 名次第一永遠約 0.88，即使整個知識庫裡沒有一張卡跟問題有關。
# 實測「量子力學的波函數塌縮」（知識庫裡完全沒有的主題）照樣拿到 0.855，
# 而且第一名是一段 Bilibili 導覽選單的爬蟲殘渣。
#
# 換成真實餘弦相似度後兩者分得很開（2026-07-29 實測，各取前三名）：
#     seedance 工作流程        0.60 0.64 0.67
#     gpt image 2 人像 prompt  0.66 0.74 0.65
#     碳盤查                   0.49 0.48 0.51
#     量子力學的波函數塌縮      0.49 0.50 0.57
#     幫我訂明天的餐廳          0.47 0.48 0.53
# 相關的落在 0.60~0.74，不相關的落在 0.47~0.57，門檻取中間。
CARD_MIN_SIMILARITY = float(os.getenv("XKB_CARD_MIN_SIMILARITY", "0.58"))


def _card_index_paths() -> tuple[Path, Path]:
    meta = Path(os.getenv("XKB_CARDS_INDEX",
                          str(xkb_paths.BOOKMARKS_DIR / "cards_index.json")))
    return meta, meta.with_suffix(".bin")


def _find_card_rows(key: str, *, card_level_only: bool = False) -> list[int]:
    """同一張卡在不同來源有不同前綴，比對時要把它們對起來。

    索引鍵是 `01-openclaw-workflows/X.md` 或 `memory/cards/X.md`，
    gbrain 回的是 `cards/01-openclaw-workflows/X.md`。
    直接字串比對永遠對不上——而對不上的後果是靜默的：
    算不出相似度就不過濾，等於這個功能沒生效卻沒人知道。
    """
    assert _CARD_KEYS is not None
    if key in _CARD_KEYS:
        return [_CARD_KEYS[key]]

    stem = key
    for prefix in ("cards/", "memory/cards/", "memory/"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    for candidate in (stem, f"cards/{stem}", f"memory/cards/{stem}"):
        if candidate in _CARD_KEYS:
            return [_CARD_KEYS[candidate]]

    if card_level_only:
        # 呼叫端明講只要卡片級。退回論點級會偷偷換掉粒度，而有些門檻
        # （像吸收閘門的 MAX_REDUNDANCY）是照卡片級向量校準的。
        return []
    # 論點級向量的鍵是 relpath#kpN。卡片級找不到時要看這張卡的**所有**論點，
    # 不是字典順序的第一個——分數要面對 0.55 這道硬門檻，所以「第三個論點
    # 正好命中」的卡片，會因為第一個論點離題而被丟掉。semantic_recall 本來
    # 就是取最大值，這條路徑漏了。
    #
    # 2026-09-01 實測：目前 1,628 張有論點向量的卡片，全部都有卡片級鍵，
    # 所以這條路徑現在走不到，實際影響是零。留著修好的版本是因為
    # 「卡片級鍵不存在」是完全可能出現的狀態（索引重建、卡片改名），
    # 而那時候取第一個論點會是靜默的錯誤。
    for candidate in (key, stem):
        rows = [i for k, i in _CARD_KEYS.items() if k.startswith(f"{candidate}#")]
        if rows:
            return rows

    # 最後退路：只比對檔名。分類目錄改過名時仍能對上。
    name = stem.rsplit("/", 1)[-1]
    return [i for k, i in _CARD_KEYS.items() if k.rsplit("/", 1)[-1] == name][:1]


# 一張卡最多讀幾條論點向量。定長記錄的 seek 很便宜，但沒有上限的話，
# 一個命名異常的鍵可能讓一次召回讀進上百筆。
MAX_CARD_ARGUMENT_ROWS = 8


def lookup_card_vectors(keys: list[str], *, card_level_only: bool = False
                        ) -> dict[str, list[list[float]]]:
    """只取指定卡片的向量，用 seek 讀單筆。

    卡片有 6,000 多個向量、100MB，全部載入要好幾秒——但我們只需要驗證
    gbrain 挑出來的那三五張。定長記錄的好處就是可以直接算出位移，
    讀 12KB 而不是 100MB。
    """
    global _CARD_KEYS, _CARD_DIMS
    meta_path, bin_path = _card_index_paths()
    if _CARD_KEYS is None:
        _CARD_KEYS = {}
        try:
            with meta_path.open(encoding="utf-8") as fh:
                meta = json.load(fh)
            _CARD_DIMS = int(meta["dims"])
            _CARD_KEYS = {k: i for i, k in enumerate(meta["keys"])}
        except (OSError, ValueError, KeyError):
            _CARD_KEYS = {}
    if not _CARD_KEYS or not bin_path.exists():
        return {}

    import array
    row_bytes = _CARD_DIMS * 4
    out: dict[str, list[list[float]]] = {}
    try:
        with bin_path.open("rb") as fh:
            for key in keys:
                # 一張卡可能有好幾條論點向量。全部讀回來，讓上面決定用哪一個。
                rows = _find_card_rows(key, card_level_only=card_level_only)
                for row in rows[:MAX_CARD_ARGUMENT_ROWS]:
                    fh.seek(row * row_bytes)
                    chunk = fh.read(row_bytes)
                    if len(chunk) != row_bytes:
                        continue
                    vec = array.array("f")
                    vec.frombytes(chunk)
                    out.setdefault(key, []).append(vec.tolist())
    except OSError:
        return {}
    return out


def card_similarities(query: str, keys: list[str]) -> dict[str, float] | None:
    """回傳每張卡片與問題的真實相似度。None 代表無法判斷（不該據此過濾）。"""
    if not keys:
        return {}
    vectors = lookup_card_vectors(keys)
    if not vectors:
        return None
    query_vector = _embed_query(query)
    if query_vector is None:
        return None
    # 一張卡取它最像的那條論點。用第一條的話，第三點正好命中的卡片會被
    # 第一點的分數判死。
    return {k: max(_cosine(query_vector, v) for v in rows)
            for k, rows in vectors.items() if rows}


# 同一句話在一次召回裡會被 embed 兩次：混合檢索先打一次，相關度過濾再打一次。
# 查詢字串一模一樣，向量當然也一樣，第二次純粹是多付一趟網路來回。
# 小快取就夠——一次召回內命中，而且行程結束就丟掉，不會拿到過期的向量。
_QUERY_VECTORS: dict[str, list[float] | None] = {}
_QUERY_VECTOR_LIMIT = 64


def _embed_query(query: str) -> list[float] | None:
    """拿不到 embedding 就回 None，呼叫端會退回字串比對。"""
    if query in _QUERY_VECTORS:
        return _QUERY_VECTORS[query]
    vector = _embed_query_uncached(query)
    if len(_QUERY_VECTORS) >= _QUERY_VECTOR_LIMIT:
        _QUERY_VECTORS.clear()
    _QUERY_VECTORS[query] = vector
    return vector


def _embed_query_uncached(query: str) -> list[float] | None:
    global _PROVIDER
    if _PROVIDER is None:
        try:
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


# 索引用 @N 標示「同一段被切成好幾塊」、~N 標示「同名標題的第幾個」，兩個都
# 加在 # 之後，所以讀取端會拿「做法-Workflow~2」去找標題。實測索引裡有 895
# 個這種鍵，648 個是 wiki 的——每一個都佔掉名額然後顯示空白。
_INDEX_SUFFIX = re.compile(r"[@~]\d+$")


def _base_heading(source: str, section: str) -> str:
    """把索引加上的 @N / ~N 拿掉，還原成真正的標題。

    只在「原樣找不到、去掉後綴找得到」時才換：真的以 ~2 結尾的標題照樣能用。

    後綴會**疊加**，所以要一層一層剝。寫入端加兩種：`~N` 是同名標題在這一頁
    的第幾次出現，`@N` 是那個段落切成的第幾塊，於是會出現
    `#結論（消化自累積筆記）~2@7` 這種鍵。原本只剝一層，這類鍵一律讀不回內容。

    這件事本來看不出來：另外 506 個已經死掉的鍵靠「退回母標題」碰巧讀得回東西，
    把失敗率稀釋到測試的 90% 門檻之下。清掉死鍵沒有弄壞這個測試，是不再幫它遮掩。
    """
    if not section:
        return section
    # 沒有後綴就沒得剝——直接回。這一條要放在最前面：召回的熱路徑上絕大多數
    # 的鍵都沒有後綴，而 _section_text 每次都要重讀整份頁面（有的到 680K）。
    if not _INDEX_SUFFIX.search(section):
        return section
    candidate = section
    # 最多剝兩層（~N 與 @N），多留一次餘裕以防之後又加了第三種後綴。
    for _ in range(3):
        if _section_text(source, candidate):
            return candidate
        if not _INDEX_SUFFIX.search(candidate):
            return section
        candidate = _INDEX_SUFFIX.sub("", candidate)
    return candidate if _section_text(source, candidate) else section


def _section_text(topic_file: str, section: str) -> str:
    """一個 wiki 段落的內文。

    索引鍵長這樣：wiki/topics/learning-base.md#做法-Workflow。呼叫端拿掉
    wiki/ 之後傳進來的是 topics/learning-base.md，而 WIKI_TOPICS_DIR 本身
    就以 topics 結尾——原本直接相接，於是每一次都在找
    .../wiki/topics/topics/learning-base.md，讀不到、回空字串。

    每一個 wiki 命中都是空的，而 format_chat 在摘要為空時會退回顯示標題，
    所以看起來一直像正常運作。
    """
    name = topic_file[len("topics/"):] if topic_file.startswith("topics/") else topic_file
    path = WIKI_TOPICS_DIR / name
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
        section = _base_heading(rest, section)
        if rest in seen_topics:            # 同一份文件只取最相關的一段
            continue
        seen_topics.add(rest)
        is_wiki = source_type == "wiki_semantic"
        excerpt = _section_text(rest, section) if is_wiki else _memory_section_text(rest, section)
        results.append(_result(
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

        candidates.append(_result(
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
        # 語意路徑產生的是 memory_semantic / wiki_semantic，不是 memory /
        # wiki——原本只比對 "memory"，所以每一筆語意召回的記憶檔都被標成
        # wiki/memory/xxx.md：說它是 wiki 頁，還給一個不存在的路徑。
        if r.source_type.startswith("memory"):
            source_label = "MEMORY"
        elif r.url:
            source_label = f"wiki/{r.url.split('/')[-1]}"
        else:
            source_label = f"wiki/{Path(r.source_file).stem}"
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
