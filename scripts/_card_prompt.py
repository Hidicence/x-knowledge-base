"""
_card_prompt.py

Shared knowledge card generation — used by ALL ingest scripts:
  run_scan_worker.py, local_ingest.py, fetch_youtube_playlist.py, fetch_github_repos.py

One unified 9-section format, source_type adapts per script.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

# ── LLM config (read once, shared) ───────────────────────────────────────────
LLM_API_BASE   = os.getenv("LLM_API_URL", "https://api.minimax.io/anthropic")
LLM_MODEL      = os.getenv("LLM_MODEL", "MiniMax-M2.5")
_USE_ANTHROPIC = "anthropic" in LLM_API_BASE or "minimax" in LLM_API_BASE

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
以下是一份知識來源的內容，請生成一張 9-section 知識卡片。

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

def llm_call(prompt: str, api_key: str, max_tokens: int = 2000,
             system: str | None = SYSTEM_PROMPT) -> str:
    if _USE_ANTHROPIC:
        body: dict[str, Any] = {
            "model": LLM_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{LLM_API_BASE}/v1/messages", data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        return next(
            item["text"] for item in data["content"] if item.get("type") == "text"
        ).strip()
    else:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }).encode()
        req = urllib.request.Request(
            LLM_API_BASE, data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()


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
    """Keyword search against existing index items; returns formatted string for section 6."""
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
