#!/usr/bin/env python3
"""
卡片分類 — 關鍵字優先，關鍵字認不出來才問 LLM

為什麼需要這支：原本的 auto_categorize.sh 只處理 `memory/bookmarks/inbox/`，
但卡片早就改存在 `memory/cards/` 平放、分類寫在 frontmatter——
兩套儲存方式分家之後，那支腳本對現有卡片完全沒有作用。

2026-07-29 盤點的後果：497 張卡片的分類是 `99-general` 或 `inbox`，
其中 495 張其實可以分類。兩個原因疊在一起：

  1. 沒有影像生成的分類。使用者最大的知識領域（visual-ai 408 張、
     prompt 391 張、gpt-image-2 51 張）在規則裡沒有位置，只能掉進預設值。
  2. 規則是英文的，卡片是中文的。93 個關鍵字裡只有 20 個中文，
     而 497 張未分類卡片每一張都含中文。01-openclaw-workflows 的
     14 個關鍵字甚至一個中文都沒有。

分類順序：
  關鍵字命中 → 用它（便宜、可重現）
  沒命中     → 問 LLM，給它現有分類清單，它可以選一個或提出新的
  LLM 不可用 → 保持原分類不動，並計數回報

最後一條是刻意的。分類猜錯比沒分類更糟——沒分類還看得出來要處理，
猜錯了會被當成已分類。今天修的 absorb gate 就是因為「失敗時放行」
而讓 120 張卡片沒經判斷就進了 wiki。

LLM 提出的新分類會寫回 category-rules.json，下次就是便宜的關鍵字比對。

Usage:
    python3 scripts/categorize_cards.py --review                  # 只看不改
    python3 scripts/categorize_cards.py --review --only-unclassified
    python3 scripts/categorize_cards.py --apply --only-unclassified
    python3 scripts/categorize_cards.py --apply --no-llm          # 只用關鍵字
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths

RULES_PATH = xkb_paths.SKILL_DIR / "config" / "category-rules.json"
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
CATEGORY_LINE_RE = re.compile(r"^category:\s*(.*)$", re.MULTILINE)
UNCLASSIFIED = {"99-general", "inbox", "general", "", None}

# 新分類的命名格式。放任 LLM 自由命名會長出 `AI Tools`、`ai_tools`、
# `07-AI-工具` 這種同義但不同名的分類，等於沒有分類。
SLUG_RE = re.compile(r"^\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NEW_CATEGORIES = 6


def load_rules() -> dict:
    with RULES_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_rules(rules: dict) -> None:
    RULES_PATH.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def read_card(path: Path) -> tuple[dict, str]:
    """回傳 (frontmatter 欄位, 全文)。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, ""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields, text


def match_text(fields: dict, body: str) -> str:
    """只拿標題、標籤、摘要來比對，不用全文。

    全文比對是原本 auto_categorize.sh 的做法，在這裡完全失準：一張九段式卡片
    連同外部連結摘錄有好幾千字，「記憶」「工具」「自動化」這種字幾乎每張都會
    出現一次。實測用全文比對時 500 張卡片有 500 張都命中 01-openclaw-workflows。
    標題與標籤才是這張卡在講什麼。
    """
    summary = ""
    m = re.search(r"##\s+[^\n]*(?:一句話摘要|摘要)\s*\n(.+?)(?=\n##|\Z)", body, re.DOTALL)
    if m:
        summary = re.sub(r"\s+", " ", m.group(1)).strip()[:300]
    title = fields.get("title", "")
    if not title:
        t = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = t.group(1) if t else ""
    return f"{title}\n{fields.get('tags', '')}\n{summary}"


def keyword_category(text: str, rules: dict) -> str | None:
    lowered = text.lower()
    for rule in rules.get("rules", []):
        for keyword in rule.get("keywords", []):
            if str(keyword).lower() in lowered:
                return rule["category"]
    return None


def llm_category(title: str, summary: str, tags: str, categories: list[str]) -> tuple[str | None, list[str], str]:
    """回傳 (分類, 建議關鍵字, 理由)。分類為 None 代表判斷不出來。"""
    sys.path.insert(0, str(xkb_paths.SCRIPTS_DIR))
    from _llm import call as llm_call

    system = "You classify knowledge cards. Output ONLY a JSON object, no prose, no markdown."
    user = (
        f"Existing categories:\n{chr(10).join('- ' + c for c in categories)}\n\n"
        f"Card title: {title}\n"
        f"Card tags: {tags}\n"
        f"Card summary: {summary[:400]}\n\n"
        "Pick the best existing category. Only propose a new one if none genuinely fits.\n"
        "New category slug format: NN-lowercase-words-with-hyphens (NN = next free number).\n"
        'Output JSON: {"category": "...", "is_new": true/false, '
        '"keywords": ["3-8 terms that would match cards like this, mix English and Chinese"], '
        '"reason": "one sentence"}'
    )
    response = llm_call(system, user)
    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
    m = re.search(r"\{.*\}", response, re.DOTALL)
    if not m:
        return None, [], "LLM 回傳無法解析"
    parsed = json.loads(m.group())
    category = str(parsed.get("category", "")).strip()
    return category or None, list(parsed.get("keywords") or []), str(parsed.get("reason", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-categorize knowledge cards")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--review", action="store_true", help="只顯示，不修改")
    mode.add_argument("--apply", action="store_true", help="寫入 frontmatter")
    parser.add_argument("--only-unclassified", action="store_true",
                        help="只處理 99-general / inbox（預設處理全部）")
    parser.add_argument("--no-llm", action="store_true", help="只用關鍵字，不問 LLM")
    parser.add_argument("--limit", type=int, help="最多處理幾張")
    args = parser.parse_args()

    rules = load_rules()
    known = [r["category"] for r in rules.get("rules", [])]
    cards = sorted(xkb_paths.CARDS_DIR.glob("*.md"))
    if args.limit:
        cards = cards[: args.limit]

    changes: list[tuple[Path, str, str, str]] = []   # path, old, new, source
    llm_failures = 0
    new_categories: dict[str, list[str]] = {}
    unchanged = 0

    for path in cards:
        fields, text = read_card(path)
        if not fields:
            continue
        current = fields.get("category", "")
        if args.only_unclassified and current not in UNCLASSIFIED:
            continue

        signal = match_text(fields, text)
        category = keyword_category(signal, rules)
        source = "keyword"

        if category is None and not args.no_llm:
            try:
                category, keywords, _ = llm_category(
                    fields.get("title", "") or path.stem,
                    signal,
                    fields.get("tags", ""),
                    known + list(new_categories),
                )
                source = "llm"
                if category and category not in known and category not in new_categories:
                    if not SLUG_RE.match(category):
                        category = None          # 命名不合格，不採用
                    elif len(new_categories) >= MAX_NEW_CATEGORIES:
                        category = None          # 避免一張卡開一個分類
                    else:
                        new_categories[category] = [str(k) for k in keywords][:8]
            except Exception:
                # 判斷不出來就不要猜。猜錯比沒分類更糟——
                # 沒分類看得出來要處理，猜錯了會被當成已分類。
                llm_failures += 1
                category = None

        if not category or category == current:
            unchanged += 1
            continue
        changes.append((path, current or "(空)", category, source))

    print(f"掃描 {len(cards)} 張卡片")
    print(f"  維持原分類 : {unchanged}")
    print(f"  會被改動   : {len(changes)}")
    if new_categories:
        print(f"  LLM 提出的新分類: {', '.join(new_categories)}")
    if llm_failures:
        print(f"  ⚠️  LLM 判斷失敗 {llm_failures} 次——這些卡片維持原分類，未被猜測")

    distribution = Counter(new for _, _, new, _ in changes)
    for category, count in distribution.most_common():
        print(f"    {count:>4}  → {category}")

    if args.review:
        for path, old, new, source in changes[:15]:
            print(f"    {old:14} → {new:24} [{source}] {path.name}")
        if len(changes) > 15:
            print(f"    …還有 {len(changes) - 15} 張")
        return 0

    for path, _, new, _ in changes:
        text = path.read_text(encoding="utf-8")
        if CATEGORY_LINE_RE.search(text):
            text = CATEGORY_LINE_RE.sub(f"category: {new}", text, count=1)
        else:
            text = text.replace("---\n", f"---\ncategory: {new}\n", 1)
        path.write_text(text, encoding="utf-8")

    if new_categories:
        for category, keywords in new_categories.items():
            rules.setdefault("rules", []).append({"category": category, "keywords": keywords})
        save_rules(rules)
        print(f"\n已把 {len(new_categories)} 個新分類寫回規則檔——下次就是關鍵字比對，不必再問 LLM")

    print(f"\n已更新 {len(changes)} 張卡片。記得重建索引：")
    print("  bash scripts/build_search_index.sh && python3 scripts/build_vector_index.py --incremental")
    return 2 if llm_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
