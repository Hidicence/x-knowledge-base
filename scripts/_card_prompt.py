"""
_card_prompt.py

Shared knowledge card generation — used by ALL ingest scripts:
  run_scan_worker.py, local_ingest.py, fetch_youtube_playlist.py, fetch_github_repos.py

One unified 9-section format, source_type adapts per script.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

# ── Unified LLM helper (config/llm.json → single model setting) ──────────────
sys.path.insert(0, str(Path(__file__).parent))
from _llm import call as _llm_call

# ── XBrain integration (path resolved by xbrain_recall, never hardcoded) ────
try:
    from xbrain_recall import (
        GBRAIN_DIR as _GBRAIN_DIR_OR_NONE,
        GBRAIN_AVAILABLE as _GBRAIN_AVAILABLE,
        GEMINI_API_KEY as _GEMINI_API_KEY,
        _make_subprocess_env,
    )
    _GBRAIN_DIR = _GBRAIN_DIR_OR_NONE
    _GBRAIN_CLI = str(_GBRAIN_DIR / "src" / "cli.ts") if _GBRAIN_DIR else ""
    _GBRAIN_ENV = _make_subprocess_env(semantic=True)
except ImportError:
    _GBRAIN_DIR = None
    _GBRAIN_CLI = ""
    _GBRAIN_AVAILABLE = False
    _GBRAIN_ENV = {**os.environ}


def gbrain_put(card_path: Path, slug: str) -> bool:
    """Push a card to gbrain and trigger embedding. Returns True on success."""
    if not _GBRAIN_AVAILABLE or not _GBRAIN_DIR or not _GBRAIN_CLI:
        return False
    try:
        import subprocess as _sp
        content = card_path.read_text(encoding="utf-8")
        r = _sp.run(
            ["bun", "run", _GBRAIN_CLI, "put", slug],
            input=content, capture_output=True, text=True,
            encoding="utf-8", env=_GBRAIN_ENV, cwd=str(_GBRAIN_DIR), timeout=30,
        )
        if r.returncode != 0:
            return False
        _sp.run(
            ["bun", "run", _GBRAIN_CLI, "embed", slug],
            capture_output=True, text=True,
            encoding="utf-8", env=_GBRAIN_ENV, cwd=str(_GBRAIN_DIR), timeout=60,
        )
        return True
    except Exception:
        return False

# ── Shared system prompt ──────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are a knowledge card generator for a personal learning base. \
Given content from any source — academic paper, social media bookmark, \
YouTube video, GitHub repo, or local document — output one structured \
knowledge card in Traditional Chinese.

Strict rules:
- Leave sections empty with "無" if uncertain — never hallucinate
- Use only information from the provided content
- Do NOT use the reader's personal name in any section

Quality principles: conservative > hallucination, understanding > summary, structured > verbose"""

# ── Unified 9-section card template ──────────────────────────────────────────
CARD_PROMPT = """\
以下是一份知識來源的內容，請生成一張知識卡片。

如果內容包含 `## 10. Media Evidence`、`### OCR` 或 `### Vision Notes`：
- 必須把圖片視為一級來源，不是裝飾。
- 若圖片內有 prompt、表格、截圖文字或前後對比，請在卡片中保留可複用 pattern。
- 不要捏造圖片文字；OCR/vision notes 標示不確定時，卡片也要保守標示。
- 圖片證據請整合到各 section，並在最後保留 Media Evidence 摘要。

來源類型: {source_type_label}
來源網址: {source_url}
分類: {category}

內容:
{content}

{related_section}
請輸出以下格式（YAML frontmatter + Markdown）：

---
id: {card_id}
type: knowledge-card
source_type: {source_type}
source_url: {source_url}
category: {category}
tags: [tag1, tag2, tag3]
sensitivity: public
confidence: medium
---

# <標題>

## 1. 核心問題與結論
- **提問**：這份內容試圖解答什麼問題？（一句話）
- **結論**：核心答案或發現是什麼？（一句話）
- **可信度說明**：有數據/實驗/引用支撐，還是個人意見？

## 2. Claim 等級
- **等級**：[Attested | Scholarship | Inference]
  - Attested：直接引用、有具體數據或實驗結果
  - Scholarship：作者/領域分析觀點，有明確來源依據
  - Inference：LLM 推論、個人猜測、尚未驗證的假設
- **主要主張**：（一句話說明被標記的核心主張）
- **依據**：（為什麼是這個等級？）

## 3. 關鍵論點
- 論點一
- 論點二
- 論點三

## 4. False Friends（如有）
這份內容涉及哪些看起來像普通詞彙但有特定技術含義的術語？
- term: （術語名稱）
  common_misunderstanding: （多數人誤以為是...）
  actual_meaning: （在此領域/內容中實際指的是...）
如果沒有：無

## 5. 驚訝點
讀者讀完後，可能感到意外或需要重新思考的是什麼？
（如果沒有明顯驚訝點，填「無」）

## 6. 與現有知識的關係
{related_cards_placeholder}

## 7. 雙語摘要（搜尋索引用）
ZH: <20-40字繁體中文摘要，說明核心發現>
EN: <15-30 word English summary of the core finding>

## 8. 對使用者的價值
- 可追蹤的方向
- 可執行的應用場景
- 適合哪個專案或工作流程

## 9. 原始來源
- 來源: {source_url}
- Links: （列出內容中出現的其他連結，如有）

## 10. Media Evidence（如有）
如果原文提供圖片 / OCR / Vision Notes，請列出：
- 圖片角色：prompt screenshot / result image / comparison / diagram / product mockup / unknown
- 圖片中最重要的可讀文字或 prompt 摘要
- 圖像 pattern：可複用的版面、風格、構圖、prompt 寫法
- 不確定處：不可讀文字、疑似誤辨、需要人工複核的地方
如果沒有圖片證據：無
"""

# Human-readable labels per source_type
SOURCE_LABELS: dict[str, str] = {
    "x-bookmark":   "X / Twitter 書籤",
    "youtube":      "YouTube 影片",
    "github-star":  "GitHub 倉庫（Star）",
    "github-fork":  "GitHub 倉庫（Fork）",
    "local-paper":  "學術論文 / 本地文件",
    "local":        "本地文件",
    "pubmed":       "PubMed / PMC 論文",
}


def source_label(source_type: str) -> str:
    return SOURCE_LABELS.get(source_type, source_type)


# ── LLM call ─────────────────────────────────────────────────────────────────

def llm_call(prompt: str, api_key: str = "", max_tokens: int = 2000,
             system: str | None = SYSTEM_PROMPT) -> str:
    """api_key kept for backwards compatibility but is no longer used.
    Model is configured via config/llm.json."""
    return _llm_call(system or "", prompt)


# ── 長文 map-reduce 濃縮（TODOS 2026-07-13）─────────────────────────────────
# 卡片生成原本硬截 4000 字元，論文/長影片後半段直接丟失且不可逆。
# 超過門檻的內容改為：分段摘要（map）→ 合併（reduce），保留全文重點。

MAPREDUCE_THRESHOLD = int(os.getenv("XKB_MAPREDUCE_THRESHOLD", "4000"))
MAPREDUCE_CHUNK = int(os.getenv("XKB_MAPREDUCE_CHUNK", "6000"))
MAPREDUCE_MAX_CHUNKS = int(os.getenv("XKB_MAPREDUCE_MAX_CHUNKS", "12"))

_MAP_PROMPT = (
    "以下是一份長文件的第 {i}/{n} 段。請用繁體中文濃縮這一段的關鍵資訊"
    "（論點、數據、結論），400 字以內，保留專有名詞原文。只輸出濃縮內容：\n\n{chunk}"
)


def condense_long_content(content: str, verbose: bool = False) -> str:
    """長文 map-reduce：超過門檻的內容分段摘要後合併，取代硬截斷。
    LLM 失敗時退回舊行為（截斷），絕不阻斷 ingest 主流程。"""
    if len(content) <= MAPREDUCE_THRESHOLD:
        return content
    chunks = [content[i:i + MAPREDUCE_CHUNK]
              for i in range(0, len(content), MAPREDUCE_CHUNK)][:MAPREDUCE_MAX_CHUNKS]
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        try:
            s = llm_call(_MAP_PROMPT.format(i=i, n=len(chunks), chunk=chunk),
                         max_tokens=600, system=None)
            summaries.append(s.strip())
            if verbose:
                print(f"  [map-reduce] chunk {i}/{len(chunks)} → {len(s)} chars")
        except Exception as e:
            if verbose:
                print(f"  [map-reduce] chunk {i} failed ({e}), falling back to truncation")
            return content[:MAPREDUCE_THRESHOLD]
    head = content[:800]
    reduced = "（以下為長文件分段濃縮，原文 %d 字元）\n\n%s\n\n--- 原文開頭 ---\n%s" % (
        len(content), "\n\n".join(summaries), head)
    return reduced


# ── Summary extraction ────────────────────────────────────────────────────────

def extract_summary(card: str) -> str:
    """Extract ZH + EN summary from section 7, with legacy fallback."""
    # New format: ## 7. 雙語摘要 with ZH:/EN: lines
    bilingual = re.search(
        r"##\s+7\.\s*雙語摘要[^\n]*\n(.+?)(?=\n##|\Z)", card, re.DOTALL
    )
    if bilingual:
        block = bilingual.group(1)
        zh_m = re.search(r"^ZH:\s*(.+)$", block, re.MULTILINE)
        en_m = re.search(r"^EN:\s*(.+)$", block, re.MULTILINE)
        parts = [m.group(1).strip() for m in [zh_m, en_m] if m and m.group(1).strip()]
        if parts:
            return " | ".join(parts)
    # Legacy
    zh = re.search(r"##\s*📝 一句話摘要\s*\n+(.+?)(\n##|\Z)", card, re.DOTALL)
    en = re.search(r"##\s*📝 English Summary\s*\n+(.+?)(\n##|\Z)", card, re.DOTALL)
    parts = [x.group(1).strip() for x in [zh, en] if x]
    if parts:
        return " | ".join(parts)
    lines = [l.strip() for l in card.splitlines()
             if l.strip() and not l.startswith("#") and not l.startswith("---")]
    return lines[0] if lines else ""


# ── Related context search ────────────────────────────────────────────────────

def find_related_context(content: str, existing_items: list[dict], top_k: int = 3) -> str:
    """Find related cards for context injection.
    Uses gbrain hybrid search if available, falls back to keyword search against existing_items."""
    if _GBRAIN_AVAILABLE:
        try:
            from xbrain_recall import xbrain_query as gbrain_query
            query = content[:300].replace("\n", " ").strip()
            results = gbrain_query(query, limit=top_k, no_expand=True)
            if results:
                lines = []
                for r in results:
                    title = r.get("title", r.get("slug", ""))[:60]
                    chunk = r.get("chunk_text", "")[:100].replace("\n", " ")
                    lines.append(f"- **{title}**：{chunk}")
                return "\n".join(lines)
        except Exception:
            pass

    # Keyword fallback
    stopwords = {
        "的", "了", "是", "在", "有", "和", "與", "就", "也", "都", "這", "那",
        "this", "that", "with", "from", "have", "will", "for", "and", "the", "a",
    }
    raw = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", content[:1000].lower())
    tokens: set[str] = set()
    for t in raw:
        if re.match(r"[\u4e00-\u9fff]", t):
            for i in range(len(t) - 1):
                tokens.add(t[i:i+2])
        else:
            tokens.add(t)
    tokens -= stopwords
    if not tokens:
        return "（無相關既有卡片）"

    scored = []
    for item in existing_items:
        combined = " ".join([
            (item.get("title") or "").lower(),
            (item.get("summary") or "").lower(),
            " ".join(item.get("tags") or []).lower(),
        ])
        score = sum(1 for t in tokens if t in combined)
        if score > 0:
            scored.append((item, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    if not scored:
        return "（無相關既有卡片）"

    lines = []
    for item, _ in scored[:top_k]:
        lines.append(
            f"- **{item.get('title', '(untitled)')}**："
            f"{(item.get('summary') or '')[:80]}"
        )
    return "\n".join(lines)


def build_prompt(
    content: str,
    card_id: str,
    source_type: str,
    source_url: str,
    category: str,
    related_context: str = "（無相關既有卡片）",
) -> str:
    """Fill in the unified CARD_PROMPT template."""
    return CARD_PROMPT.format(
        source_type_label=source_label(source_type),
        source_url=source_url,
        category=category,
        content=content,
        card_id=card_id,
        source_type=source_type,
        related_section=(
            f"相關既有卡片（供 Section 6 參考）：\n{related_context}\n"
            if related_context else ""
        ),
        related_cards_placeholder=related_context,
    )
