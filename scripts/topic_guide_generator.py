#!/usr/bin/env python3
"""
topic_guide_generator.py — XKB 領域導覽生成器

針對某個主題（tag 或 category），從現有 cards 自動生成：
- 領域基礎術語與 False Friends
- 建議閱讀順序（從基礎到進階）
- 主要作者與關鍵論文
- 目前的知識共識 vs 缺口

Usage:
  python3 topic_guide_generator.py --topic "醫療AI影像診斷"
  python3 topic_guide_generator.py --category research
  python3 topic_guide_generator.py --tag "影像診斷" --tag "人工智慧"
  python3 topic_guide_generator.py --topic "醫療AI" --out wiki/topics/medical-ai-guide.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
_SKILL_DIR = Path(__file__).resolve().parent.parent
INDEX_PATH = WORKSPACE / "memory" / "bookmarks" / "search_index.json"
WIKI_DIR = Path(os.getenv("XKB_WIKI_DIR", str(_SKILL_DIR / "wiki"))) / "topics"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent"


def _get_gemini_key() -> str:
    cfg_path = Path(os.getenv("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("env", {}).get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    except Exception:
        return os.getenv("GEMINI_API_KEY", "")


def _call_gemini(key: str, prompt: str, retries: int = 3) -> str:
    import time
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    url = f"{GEMINI_API_URL}?key={key}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 5
                print(f"  retrying in {wait}s... ({e})")
                time.sleep(wait)
            else:
                raise


def load_cards(category: str | None, tags: list[str]) -> list[dict]:
    """Load relevant cards from the search index."""
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    items = data.get("items", [])

    results = []
    for item in items:
        if category and item.get("category") != category:
            continue
        if tags:
            item_tags = [t.lower() for t in (item.get("tags") or [])]
            item_summary = (item.get("summary") or "").lower()
            if not any(t.lower() in " ".join(item_tags) + " " + item_summary for t in tags):
                continue
        results.append(item)

    return results


def build_card_digest(cards: list[dict]) -> str:
    """Build a condensed digest of cards for the LLM."""
    cards = cards[:15]  # Cap at 15 to keep prompt manageable
    parts = []
    for i, card in enumerate(cards, 1):
        title = card.get("title", "")[:100]
        summary = card.get("summary", "")[:200]
        card_tags = ", ".join((card.get("tags") or [])[:6])
        author = card.get("author", "")[:60]
        parts.append(
            f"[{i}] {title}\n"
            f"     Authors: {author}\n"
            f"     Tags: {card_tags}\n"
            f"     Summary: {summary}"
        )
    return "\n\n".join(parts)


def generate_topic_guide(topic: str, cards: list[dict], key: str) -> str:
    """Call Gemini to generate a structured topic guide."""
    digest = build_card_digest(cards)
    prompt = f"""You are a knowledge curator. Based on the following {len(cards)} knowledge cards about "{topic}", generate a comprehensive "Topic Guide" for someone new to this field.

The cards are:
{digest}

Generate the topic guide in Traditional Chinese with this exact structure:

# 領域導覽：{topic}

> 基於 {len(cards)} 篇相關文獻自動生成 | {datetime.now().strftime('%Y-%m-%d')}

## 一、這個領域在解決什麼問題？
（2-3 句話說明核心問題意識，為什麼這個領域值得研究）

## 二、必知基礎術語
列出 5-8 個關鍵術語，每個包含：
- **術語**：正確含義 + 常見誤解（如有）

## 三、建議閱讀順序
根據概念難度和依賴關係，列出建議的閱讀路徑：
### 第一步：建立基礎概念
- [論文編號] 標題 ——理由：為什麼先讀這篇？

### 第二步：深入方法論
- [論文編號] 標題 ——理由

### 第三步：進階應用與挑戰
- [論文編號] 標題 ——理由

（如有需要可增加步驟，每步驟1-3篇）

## 四、目前的知識共識
這個領域中，哪些觀點已有較多證據支持？（3-5點）

## 五、知識缺口與爭議
根據現有資料，哪些問題仍未解決或有爭議？（3-5點）

## 六、主要研究者與機構
列出收錄文獻中出現的重要作者或機構

## 七、延伸學習建議
根據目前收錄資料的缺口，建議下一步應該補充哪些方向的文獻？

---
*此導覽由 XKB 自動生成，基於已收錄的知識卡片。如需更新，重新執行 topic_guide_generator.py*"""

    return _call_gemini(key, prompt)


def main() -> int:
    parser = argparse.ArgumentParser(description="XKB 領域導覽生成器")
    parser.add_argument("--topic", help="主題名稱（用於標題和 LLM prompt）")
    parser.add_argument("--category", help="按 category 篩選 cards")
    parser.add_argument("--tag", action="append", dest="tags", default=[], help="按 tag 篩選（可重複）")
    parser.add_argument("--out", help="輸出檔案路徑（預設自動生成）")
    parser.add_argument("--min-cards", type=int, default=3, help="最少需要幾張 card 才生成（預設 3）")
    parser.add_argument("--dry-run", action="store_true", help="只顯示符合的 cards，不呼叫 LLM")
    args = parser.parse_args()

    if not args.topic and not args.category and not args.tags:
        print("Error: 需要至少一個篩選條件：--topic, --category, 或 --tag")
        return 1

    topic = args.topic or args.category or "+".join(args.tags)

    print(f"📚 篩選 cards：topic={topic}, category={args.category}, tags={args.tags}")
    cards = load_cards(args.category, args.tags)

    if not cards:
        print("❌ 沒有找到符合條件的 cards")
        return 1

    print(f"   找到 {len(cards)} 張 cards")
    for card in cards:
        print(f"   - {card.get('title','')[:70]}")

    if len(cards) < args.min_cards:
        print(f"⚠ cards 數量不足 {args.min_cards}，跳過生成（用 --min-cards 1 強制執行）")
        return 0

    if args.dry_run:
        print("\n[dry-run] 不呼叫 LLM")
        return 0

    key = _get_gemini_key()
    if not key:
        print("❌ GEMINI_API_KEY not found")
        return 1

    print(f"\n🤖 呼叫 Gemini 生成領域導覽（{len(cards)} 張 cards）...")
    guide = generate_topic_guide(topic, cards, key)

    # Determine output path
    if args.out:
        out_path = WORKSPACE / args.out if not Path(args.out).is_absolute() else Path(args.out)
    else:
        slug = re.sub(r"[^\w\-]", "-", topic.lower())[:40].strip("-")
        out_path = WIKI_DIR / f"{slug}-guide.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(guide, encoding="utf-8")
    print(f"\n✅ 領域導覽已儲存：{out_path}")
    print(f"\n--- 預覽（前 500 字）---")
    print(guide[:500])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
