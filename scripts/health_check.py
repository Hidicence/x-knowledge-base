#!/usr/bin/env python3
"""
health_check.py — XKB 知識庫健康檢查

功能：
1. 衝突偵測：找出摘要語意相近但結論可能衝突的 card pair
2. 缺口偵測：找出某個主題的 cards 缺少哪些關鍵面向
3. 重複偵測：找出可能重複收錄的相似內容

Usage:
  python3 health_check.py                        # 全庫檢查
  python3 health_check.py --category research    # 只檢查 research 類
  python3 health_check.py --mode conflicts       # 只跑衝突偵測
  python3 health_check.py --mode gaps            # 只跑缺口偵測
  python3 health_check.py --mode duplicates      # 只跑重複偵測
  python3 health_check.py --out /tmp/report.md   # 輸出到檔案
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
INDEX_PATH = WORKSPACE / "memory" / "bookmarks" / "search_index.json"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent"
GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1/models/text-embedding-004:embedContent"


def _get_gemini_key() -> str:
    cfg_path = Path(os.getenv("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("env", {}).get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    except Exception:
        return os.getenv("GEMINI_API_KEY", "")


def _call_gemini(key: str, prompt: str, retries: int = 3) -> str:
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    url = f"{GEMINI_API_URL}?key={key}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 5)
            else:
                raise


def _embed(key: str, text: str) -> list[float]:
    payload = json.dumps({"model": "models/text-embedding-004", "content": {"parts": [{"text": text[:2000]}]}}).encode()
    url = f"{GEMINI_EMBED_URL}?key={key}"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return data["embedding"]["values"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def load_cards(category: str | None) -> list[dict]:
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    items = data.get("items", [])
    if category:
        items = [i for i in items if i.get("category") == category]
    # Only process cards that have summaries
    return [i for i in items if i.get("summary")]


# ── 1. Conflict Detection ─────────────────────────────────────────────────────

def detect_conflicts(cards: list[dict], key: str, threshold: float = 0.82) -> list[dict]:
    """Find pairs with high semantic similarity, then check for conflicts."""
    print(f"\n📊 衝突偵測：嵌入 {len(cards)} 張 cards...")

    # Get embeddings for summaries
    embeddings = []
    for i, card in enumerate(cards):
        summary = card.get("summary", "")[:500]
        try:
            emb = _embed(key, summary)
            embeddings.append(emb)
            if (i + 1) % 10 == 0:
                print(f"   {i+1}/{len(cards)} embedded...")
            time.sleep(0.1)  # Rate limit
        except Exception as e:
            embeddings.append(None)

    # Find high-similarity pairs
    similar_pairs = []
    n = len(cards)
    for i in range(n):
        for j in range(i + 1, n):
            if embeddings[i] is None or embeddings[j] is None:
                continue
            sim = cosine_similarity(embeddings[i], embeddings[j])
            if sim >= threshold:
                similar_pairs.append((sim, i, j))

    similar_pairs.sort(reverse=True)
    top_pairs = similar_pairs[:10]  # Check top 10 similar pairs

    print(f"   找到 {len(similar_pairs)} 組相似 pair（閾值 {threshold}），分析前 {len(top_pairs)} 組...")

    conflicts = []
    for sim, i, j in top_pairs:
        card_a = cards[i]
        card_b = cards[j]
        title_a = card_a.get("title", "")[:60]
        title_b = card_b.get("title", "")[:60]
        sum_a = card_a.get("summary", "")[:300]
        sum_b = card_b.get("summary", "")[:300]

        prompt = f"""Two knowledge cards have high semantic similarity ({sim:.2f}). Analyze if they conflict, complement, or are duplicates.

Card A: {title_a}
Summary A: {sum_a}

Card B: {title_b}
Summary B: {sum_b}

Output exactly three lines:
RELATION: [CONFLICT | COMPLEMENT | DUPLICATE | UNRELATED]
REASON: (one sentence explaining the relationship in Traditional Chinese)
NOTABLE: (one sentence on what's worth noting about this pair, in Traditional Chinese)"""

        try:
            result = _call_gemini(key, prompt)
            relation_m = re.search(r"RELATION:\s*(\w+)", result)
            reason_m = re.search(r"REASON:\s*(.+)", result)
            notable_m = re.search(r"NOTABLE:\s*(.+)", result)

            relation = relation_m.group(1) if relation_m else "UNKNOWN"
            reason = reason_m.group(1).strip() if reason_m else ""
            notable = notable_m.group(1).strip() if notable_m else ""

            conflicts.append({
                "relation": relation,
                "similarity": round(sim, 3),
                "card_a": {"title": title_a, "path": card_a.get("path", "")},
                "card_b": {"title": title_b, "path": card_b.get("path", "")},
                "reason": reason,
                "notable": notable,
            })
            time.sleep(0.5)
        except Exception as e:
            print(f"   ✗ LLM error for pair ({i},{j}): {e}")

    return conflicts


# ── 2. Gap Detection ───────────────────────────────────────────────────────────

def detect_gaps(cards: list[dict], key: str) -> str:
    """Ask LLM to identify knowledge gaps in the collection."""
    print(f"\n📊 缺口偵測：分析 {min(len(cards), 20)} 張 cards...")

    digest = []
    for card in cards[:20]:
        title = card.get("title", "")[:80]
        tags = ", ".join((card.get("tags") or [])[:5])
        digest.append(f"- {title} [{tags}]")

    prompt = f"""Based on this collection of {len(cards)} knowledge cards:

{chr(10).join(digest)}

Identify knowledge gaps in Traditional Chinese. Output:

## 知識缺口分析

### 已覆蓋的面向
（列出3-5個已有充分覆蓋的面向）

### 明顯缺少的面向
（列出3-5個重要但目前缺少的研究方向或主題）

### 建議下一步補充
（3個具體的建議搜尋查詢，可用來填補缺口）"""

    return _call_gemini(key, prompt)


# ── 3. Duplicate Detection ─────────────────────────────────────────────────────

def detect_duplicates(cards: list[dict]) -> list[dict]:
    """Simple title-based duplicate detection (no LLM needed)."""
    print(f"\n📊 重複偵測：掃描 {len(cards)} 張 cards...")

    # Normalize titles for comparison
    def normalize(title: str) -> str:
        return re.sub(r"[^\w\s]", "", title.lower()).strip()

    seen: dict[str, list] = defaultdict(list)
    for card in cards:
        norm = normalize(card.get("title", ""))
        if norm:
            seen[norm].append(card)

    duplicates = []
    for norm, group in seen.items():
        if len(group) > 1:
            duplicates.append({
                "normalized_title": norm,
                "cards": [{"title": c.get("title", ""), "path": c.get("path", "")} for c in group],
            })

    return duplicates


# ── Report builder ─────────────────────────────────────────────────────────────

def build_report(
    conflicts: list[dict],
    gaps_text: str,
    duplicates: list[dict],
    total_cards: int,
    category: str | None,
) -> str:
    lines = [
        f"# XKB 健康檢查報告",
        f"> 生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M')} | 範圍：{category or '全庫'} | 共 {total_cards} 張 cards",
        "",
    ]

    # Conflicts
    lines.append("## 一、關係分析（語意相近 cards）")
    if not conflicts:
        lines.append("無發現高度相似的 card pair。\n")
    else:
        by_relation: dict[str, list] = defaultdict(list)
        for c in conflicts:
            by_relation[c["relation"]].append(c)

        for relation in ["CONFLICT", "DUPLICATE", "COMPLEMENT", "UNRELATED", "UNKNOWN"]:
            group = by_relation.get(relation, [])
            if not group:
                continue
            emoji = {"CONFLICT": "⚡", "DUPLICATE": "🔁", "COMPLEMENT": "🔗", "UNRELATED": "—", "UNKNOWN": "?"}.get(relation, "")
            lines.append(f"\n### {emoji} {relation} ({len(group)} 組)")
            for item in group:
                lines.append(f"- **{item['card_a']['title'][:50]}**")
                lines.append(f"  vs **{item['card_b']['title'][:50]}**")
                lines.append(f"  相似度：{item['similarity']} | {item['reason']}")
                if item['notable']:
                    lines.append(f"  注意：{item['notable']}")
        lines.append("")

    # Gaps
    lines.append("## 二、知識缺口")
    lines.append(gaps_text)
    lines.append("")

    # Duplicates
    lines.append("## 三、標題重複偵測")
    if not duplicates:
        lines.append("無發現重複標題。\n")
    else:
        for dup in duplicates:
            lines.append(f"- **重複標題**：{dup['cards'][0]['title'][:70]}")
            for card in dup["cards"]:
                lines.append(f"  - {card['path']}")
        lines.append("")

    lines.append("---")
    lines.append("*此報告由 XKB health_check.py 自動生成*")
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="XKB 知識庫健康檢查")
    parser.add_argument("--category", help="只檢查某個 category")
    parser.add_argument("--mode", choices=["all", "conflicts", "gaps", "duplicates"], default="all")
    parser.add_argument("--out", help="輸出報告路徑")
    parser.add_argument("--threshold", type=float, default=0.82, help="相似度閾值（預設 0.82）")
    args = parser.parse_args()

    key = _get_gemini_key()
    if not key:
        print("❌ GEMINI_API_KEY not found")
        return 1

    cards = load_cards(args.category)
    print(f"✅ 載入 {len(cards)} 張 cards（category={args.category or '全部'}）")

    if not cards:
        print("❌ 沒有找到 cards")
        return 1

    conflicts = []
    gaps_text = "（跳過缺口偵測）"
    duplicates = []

    if args.mode in ("all", "conflicts"):
        conflicts = detect_conflicts(cards, key, args.threshold)

    if args.mode in ("all", "gaps"):
        gaps_text = detect_gaps(cards, key)

    if args.mode in ("all", "duplicates"):
        duplicates = detect_duplicates(cards)

    report = build_report(conflicts, gaps_text, duplicates, len(cards), args.category)

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = WORKSPACE / "memory" / "x-knowledge-base" / "health-check-report.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 報告已儲存：{out_path}")

    # Print conflict summary
    conflict_count = sum(1 for c in conflicts if c["relation"] == "CONFLICT")
    dup_count = len(duplicates)
    comp_count = sum(1 for c in conflicts if c["relation"] == "COMPLEMENT")
    print(f"\n📋 摘要：衝突 {conflict_count} | 互補 {comp_count} | 重複標題 {dup_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
