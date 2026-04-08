#!/usr/bin/env python3
"""
suggest_topic_map.py

自動分析 search_index.json 的 category 分布，對尚未映射的 category
使用 LLM 生成 topic slug 建議，輸出可審閱的 topic-map patch。

Usage:
    python3 scripts/suggest_topic_map.py --review          # 顯示建議（不修改）
    python3 scripts/suggest_topic_map.py --apply           # 自動寫入 topic-map.json
    python3 scripts/suggest_topic_map.py --review --min-cards 5
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
WORKSPACE_DIR = Path(os.getenv("OPENCLAW_WORKSPACE",
    os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
INDEX_FILE     = BOOKMARKS_DIR / "search_index.json"
WIKI_DIR       = WORKSPACE_DIR / "wiki"
TOPIC_MAP_FILE = WIKI_DIR / "topic-map.json"
TOPICS_DIR     = WIKI_DIR / "topics"

# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_API_BASE   = os.getenv("LLM_API_URL", "https://api.minimax.io/anthropic")
LLM_MODEL      = os.getenv("LLM_MODEL", "MiniMax-M2.5")
_USE_ANTHROPIC = "anthropic" in LLM_API_BASE or "minimax" in LLM_API_BASE


def load_env_key() -> str:
    cfg_path = Path(os.getenv("OPENCLAW_JSON",
        str(Path.home() / ".openclaw" / "openclaw.json")))
    try:
        cfg = json.loads(cfg_path.read_text())
        env = cfg.get("env", {})
        return (env.get("LLM_API_KEY") or env.get("MINIMAX_API_KEY") or
                os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY") or "")
    except Exception:
        return os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY") or ""


def llm_call(prompt: str, api_key: str) -> str:
    if _USE_ANTHROPIC:
        payload = json.dumps({
            "model": LLM_MODEL,
            "max_tokens": 800,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            f"{LLM_API_BASE}/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return next(item["text"] for item in data["content"] if item.get("type") == "text").strip()
    else:
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 800,
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(
            LLM_API_BASE, data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()


def load_index() -> list:
    if not INDEX_FILE.exists():
        print(f"[ERROR] search_index.json not found: {INDEX_FILE}", file=sys.stderr)
        sys.exit(1)
    return json.loads(INDEX_FILE.read_text(encoding="utf-8")).get("items", [])


def load_topic_map() -> dict:
    if not TOPIC_MAP_FILE.exists():
        return {"mapping": {}}
    return json.loads(TOPIC_MAP_FILE.read_text(encoding="utf-8"))


def existing_topic_slugs() -> list:
    if not TOPICS_DIR.exists():
        return []
    return [f.stem for f in TOPICS_DIR.glob("*.md")]


def sample_titles(items: list, category: str, n: int = 8) -> list:
    return [
        (i.get("title") or "").strip()
        for i in items
        if i.get("category") == category and i.get("title")
    ][:n]


SUGGEST_PROMPT = """\
你是一個知識庫架構師。我有一批知識卡片，屬於同一個 category，請你為這批卡片建議合適的 wiki topic slug。

Category: {category}
卡片數量: {count}
範例標題:
{titles}

現有的 wiki topic slugs（已存在，避免重複）:
{existing}

請輸出 JSON，格式如下：
{{"topics": ["slug-1"], "reason": "一句話說明為何這樣映射", "confidence": "high/medium/low"}}

規則：
- slug 用英文小寫 kebab-case，如 "ai-agent-memory"
- 若卡片太雜或太少（< 3 張），建議設為 null 並說明原因
- 若與現有 topic 重疊，直接映射過去（不新建）
- 只輸出 JSON，不要其他文字
"""


def suggest_topics_for_category(category: str, items: list, existing_slugs: list, api_key: str) -> dict:
    count = sum(1 for i in items if i.get("category") == category)
    titles_str = "\n".join(f"  - {t}" for t in sample_titles(items, category)) or "  （無標題）"
    existing_str = "\n".join(f"  - {s}" for s in existing_slugs) or "  （無）"

    prompt = SUGGEST_PROMPT.format(
        category=category, count=count,
        titles=titles_str, existing=existing_str,
    )

    raw = llm_call(prompt, api_key).strip()
    # Strip markdown code fences
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON object with regex
    m = re.search(r'\{[^{}]*"topics"[^{}]*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return {"topics": None, "reason": f"LLM 輸出無法解析：{raw[:120]}", "confidence": "low"}


def main():
    parser = argparse.ArgumentParser(description="Auto-suggest XKB topic-map from search index")
    parser.add_argument("--review", action="store_true", help="顯示建議（不修改檔案）")
    parser.add_argument("--apply",  action="store_true", help="將建議寫入 topic-map.json")
    parser.add_argument("--min-cards", type=int, default=3,
                        help="只建議 card 數 >= N 的 category（預設 3）")
    parser.add_argument("--category", help="只處理指定 category")
    args = parser.parse_args()

    if not args.review and not args.apply:
        parser.error("請指定 --review 或 --apply")

    api_key = load_env_key()
    if not api_key:
        print("[ERROR] 找不到 LLM_API_KEY", file=sys.stderr)
        sys.exit(1)

    items = load_index()
    topic_map = load_topic_map()
    current_mapping = topic_map.get("mapping", {})
    existing_slugs = existing_topic_slugs()

    cat_counts = Counter(i.get("category", "other") for i in items)
    print(f"📊 共 {len(items)} 張卡，{len(cat_counts)} 個 category")

    if args.category:
        count = cat_counts.get(args.category, 0)
        candidates = [(args.category, count)]
    else:
        candidates = [
            (cat, count) for cat, count in cat_counts.most_common()
            if cat not in current_mapping and count >= args.min_cards
        ]

    if not candidates:
        print("✅ 所有 category 已有映射，或數量不足 --min-cards 閾值。")
        return

    print(f"\n🔍 發現 {len(candidates)} 個待映射 category：")
    for cat, count in candidates:
        status = "（已映射）" if cat in current_mapping else ""
        print(f"   {cat}: {count} 張 {status}")

    print(f"\n🤖 使用 LLM 生成建議...\n")
    suggestions = {}
    for cat, count in candidates:
        print(f"  ▶ {cat} ({count} 張)...", end=" ", flush=True)
        suggestion = suggest_topics_for_category(cat, items, existing_slugs, api_key)
        suggestions[cat] = {"count": count, **suggestion}
        topics_str = str(suggestion.get("topics")) if suggestion.get("topics") else "null"
        print(f"{topics_str} [{suggestion.get('confidence', '?')}]")

    print("\n" + "─" * 60)
    print("📋 建議 topic-map patch：\n")
    patch = {}
    for cat, data in suggestions.items():
        topics = data.get("topics")
        reason = data.get("reason", "")
        conf = data.get("confidence", "?")
        print(f"  {cat} ({data['count']} 張) → {topics}")
        print(f"    原因：{reason}")
        print(f"    信心：{conf}")
        print()
        if topics is not None:
            patch[cat] = {"topics": topics, "reason": reason, "confidence": conf}
        else:
            patch[cat] = {"topics": None, "reason": reason, "status": "pending"}

    if args.apply:
        current_mapping.update(patch)
        topic_map["mapping"] = current_mapping
        TOPIC_MAP_FILE.write_text(
            json.dumps(topic_map, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"✅ 已寫入 {TOPIC_MAP_FILE}")
        print(f"   新增/更新 {len(patch)} 個 category 映射")
    else:
        print("💡 使用 --apply 將以上建議寫入 topic-map.json")

    new_slugs = set()
    for data in patch.values():
        for slug in (data.get("topics") or []):
            if slug not in existing_slugs:
                new_slugs.add(slug)
    if new_slugs:
        print(f"\n📌 以下 topic 需要建立新的 wiki 頁面：")
        for slug in sorted(new_slugs):
            print(f"   wiki/topics/{slug}.md")
        print("   執行 sync_cards_to_wiki.py --apply 後會自動建立")


if __name__ == "__main__":
    main()
