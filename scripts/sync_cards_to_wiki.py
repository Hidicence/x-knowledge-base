#!/usr/bin/env python3
"""
Sync x-knowledge-base cards into wiki topic pages.

v3: LLM absorb gate + persistent decision log + per-run report.
Each candidate card is evaluated against the existing wiki page content
using the Policy 6 quality gate: "What new dimension does this card add?"
Decisions are persisted to review-decisions.json["decisions"] for status tracking.

Usage:
  python3 sync_cards_to_wiki.py --review [--topic SLUG] [--no-llm]
  python3 sync_cards_to_wiki.py --apply  [--topic SLUG] [--no-llm] [--limit N]
  python3 sync_cards_to_wiki.py --apply --topic ai-seo-and-geo
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
WIKI_DIR = WORKSPACE / "wiki"
TOPICS_DIR = WIKI_DIR / "topics"
INDEX_PATH = WIKI_DIR / "index.md"
LOG_PATH = WIKI_DIR / "log.md"
TOPIC_MAP_PATH = WIKI_DIR / "topic-map.json"
REVIEW_DECISIONS_PATH = WIKI_DIR / "review-decisions.json"
SEARCH_INDEX_PATH = WORKSPACE / "memory" / "bookmarks" / "search_index.json"

LLM_API_URL = "https://api.openai.com/v1/chat/completions"
LLM_MODEL = "gpt-4o-mini"

_llm_cache: dict[tuple[str, str], tuple[bool, str, str]] = {}


@dataclass
class Card:
    title: str
    url: str
    category: str
    tags: list
    date: str
    path: str = ""
    summary: str = ""


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
SOURCES_SECTION_RE = re.compile(r"\n## 來源\n(.*)$", re.DOTALL)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _call_minimax(api_key: str, system: str, user: str) -> str:
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 500,
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        LLM_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


def llm_absorb_judgment(
    card: Card, topic: str, wiki_content: str, card_full_content: str, api_key: str
) -> tuple[bool, str, str]:
    """
    Ask LLM: does this card add a new dimension to the wiki page?
    Returns (should_include, dimension, reason).
    dimension: new_case | new_concept | contradiction | none
    """
    cache_key = (card.url, topic)
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    wiki_excerpt = wiki_content[:2000]
    if card_full_content:
        card_excerpt = card_full_content[:800]
    else:
        tag_str = ", ".join(str(t) for t in card.tags)
        card_excerpt = f"{card.title}\nTags: {tag_str}\nSummary: {card.summary}"

    system = (
        "You are a wiki quality gate. You must output ONLY a JSON object. "
        "No reasoning, no explanation, no markdown. Just the JSON."
    )

    user = (
        f"Wiki topic: {topic}\n"
        f"Wiki excerpt: {wiki_excerpt[:1200]}\n\n"
        f"Card title: {card.title}\n"
        f"Card tags: {', '.join(str(t) for t in card.tags[:6])}\n"
        f"Card content: {card_excerpt[:500]}\n\n"
        "Does this card add new value to the wiki?\n"
        "Gates: (1) new dimension not in wiki (2) actionable/multi-source (3) relevant 6mo+\n"
        "Dimension: new_case|new_concept|contradiction|none\n\n"
        'Output JSON only: {"include": true/false, "dimension": "...", "reason": "one sentence"}'
    )

    try:
        response = _call_minimax(api_key, system, user)
        # Strip <think>...</think> blocks if present
        response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
        # Extract JSON with greedy match (handles full object)
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            # Fallback: infer from plain-text keywords in response
            text_lower = response.lower()
            include_fallback = "include: true" in text_lower or '"include": true' in text_lower
            dimension_fallback = "none"
            for dim in ("new_concept", "new_case", "contradiction"):
                if dim in text_lower:
                    dimension_fallback = dim
                    break
            parsed = {"include": include_fallback, "dimension": dimension_fallback, "reason": "text-parsed fallback"}
        include = bool(parsed.get("include", False))
        dimension = str(parsed.get("dimension", "none"))
        reason = str(parsed.get("reason", ""))
        result = (include, dimension, reason)
    except Exception as e:
        result = (True, "new_case", f"[llm_error: {e}] fallback include")

    _llm_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_search_items() -> list[dict]:
    raw = load_json(SEARCH_INDEX_PATH)
    if isinstance(raw, dict):
        return raw.get("items", [])
    return raw  # type: ignore


def load_topic_map() -> dict:
    raw = load_json(TOPIC_MAP_PATH)
    return raw.get("mapping", {})  # type: ignore


def load_review_file() -> dict:
    """Load the full review-decisions.json, initializing missing keys."""
    if not REVIEW_DECISIONS_PATH.exists():
        return {"decisions": {}, "topics": {}}
    try:
        data = load_json(REVIEW_DECISIONS_PATH)
        if not isinstance(data, dict):
            data = {}
        if "decisions" not in data:
            data["decisions"] = {}
        if "topics" not in data:
            data["topics"] = {}
        return data
    except Exception:
        return {"decisions": {}, "topics": {}}


def load_review_decisions() -> dict:
    return load_review_file().get("topics", {})


def save_absorb_decisions(records: list[dict]) -> None:
    """Persist LLM absorb decisions to review-decisions.json["decisions"]."""
    if not records:
        return
    data = load_review_file()
    today = datetime.now(timezone.utc).date().isoformat()
    for rec in records:
        key = rec["url"]
        data["decisions"][key] = {
            "decision": rec["decision"],          # approve | skip | manual-allow | manual-skip | duplicate
            "dimension": rec.get("dimension", ""),
            "reason": rec.get("reason", ""),
            "topic": rec.get("topic", ""),
            "evaluated_at": today,
        }
    REVIEW_DECISIONS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_card_content(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def normalize_date(item: dict) -> str:
    for key in ("created_at", "date", "published_at"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value[:10]
    return datetime.now(timezone.utc).date().isoformat()


def make_card(item: dict) -> Card | None:
    if item.get("excluded"):
        return None
    url = (item.get("source_url") or item.get("url") or "").strip()
    title = (item.get("title") or item.get("tweet_title") or "").strip()
    category = (item.get("category") or "").strip()
    if not url or not title or not category:
        return None
    return Card(
        title=title, url=url, category=category,
        tags=[str(t) for t in (item.get("tags") or [])],
        date=normalize_date(item),
        path=item.get("path", ""),
        summary=item.get("summary", ""),
    )


# ---------------------------------------------------------------------------
# Manual review decision gate (hard gate: overrides LLM)
# ---------------------------------------------------------------------------

def check_manual_decision(
    card: Card, topic: str, review_decisions: dict
) -> tuple[str, str | None]:
    decision = review_decisions.get(topic, {})
    allow = set(decision.get("allow") or [])
    skip = set(decision.get("skip") or [])
    move = decision.get("move") or {}
    if card.url in move:
        return "move", move[card.url]
    if card.url in skip:
        return "skip", None
    if card.url in allow:
        return "allow", None
    return "auto", None


# ---------------------------------------------------------------------------
# Card grouping
# ---------------------------------------------------------------------------

def collect_topic_existing_urls() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path in TOPICS_DIR.glob("*.md"):
        result[path.stem] = parse_existing_sources(path.read_text(encoding="utf-8"))
    return result


def iter_mapped_cards(
    items: list[dict],
    topic_map: dict,
    topic_filter: str | None,
    existing_urls_by_topic: dict[str, set[str]],
    review_decisions: dict,
) -> dict[str, list[Card]]:
    grouped: dict[str, list[Card]] = defaultdict(list)
    for item in items:
        card = make_card(item)
        if not card:
            continue
        mapping = topic_map.get(card.category)
        if not mapping:
            continue
        topics = mapping.get("topics")
        if not topics:
            continue
        for topic in topics:
            if topic_filter and topic != topic_filter:
                continue
            if card.url in existing_urls_by_topic.get(topic, set()):
                continue
            decision, moved_topic = check_manual_decision(card, topic, review_decisions)
            if decision == "skip":
                continue
            if decision == "move" and moved_topic:
                if not topic_filter or moved_topic == topic_filter:
                    if card.url not in existing_urls_by_topic.get(moved_topic, set()):
                        grouped[moved_topic].append(card)
                continue
            grouped[topic].append(card)

    for topic, cards in list(grouped.items()):
        seen: set[str] = set()
        deduped: list[Card] = []
        cards.sort(key=lambda c: (c.date, c.title), reverse=True)
        for card in cards:
            if card.url in seen:
                continue
            seen.add(card.url)
            deduped.append(card)
        grouped[topic] = deduped

    return grouped


# ---------------------------------------------------------------------------
# Wiki file manipulation
# ---------------------------------------------------------------------------

def parse_existing_sources(content: str) -> set[str]:
    section = SOURCES_SECTION_RE.search(content)
    if not section:
        return set()
    return set(re.findall(r"\((https?://[^)]+)\)", section.group(1)))


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(content)
    if not match:
        raise ValueError("missing frontmatter")
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data, content[match.end():]


def render_frontmatter(data: dict[str, str]) -> str:
    ordered_keys = ["title", "tags", "sources", "last_updated", "status"]
    lines = ["---"]
    for key in ordered_keys:
        if key in data:
            lines.append(f"{key}: {data[key]}")
    for key, value in data.items():
        if key not in ordered_keys:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def format_source_line(card: Card) -> str:
    return f"- [{card.title}]({card.url}) — {card.date}，xkb"


def update_topic_file(
    topic: str, approved_cards: list[Card], apply: bool
) -> tuple[bool, str]:
    path = TOPICS_DIR / f"{topic}.md"
    if not path.exists():
        return False, f"skip {topic}: not seeded yet"

    content = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)
    existing_urls = parse_existing_sources(content)
    new_cards = [c for c in approved_cards if c.url not in existing_urls]
    if not new_cards:
        return False, f"no new sources for {topic}"

    source_match = SOURCES_SECTION_RE.search(content)
    if not source_match:
        return False, f"skip {topic}: missing sources section"

    source_block = source_match.group(1).rstrip()
    appended = "\n".join(format_source_line(c) for c in new_cards)
    new_source_block = source_block + "\n" + appended + "\n"

    frontmatter["sources"] = str(len(existing_urls) + len(new_cards))
    frontmatter["last_updated"] = datetime.now(timezone.utc).date().isoformat()
    rebuilt = render_frontmatter(frontmatter) + "\n\n" + body.lstrip("\n")
    rebuilt = SOURCES_SECTION_RE.sub(
        "\n## 來源\n" + new_source_block.rstrip() + "\n", rebuilt
    )

    if apply:
        path.write_text(rebuilt, encoding="utf-8")
    label = "updated" if apply else "[dry-run] would update"
    return True, f"{label} {topic}: +{len(new_cards)} sources"


def update_index(apply: bool) -> None:
    lines = [
        "# Wiki Index", "",
        "> Topic registry for the derived knowledge layer.", "",
        "## Status legend",
        "- `draft` — 已註冊，但尚未形成穩定頁面",
        "- `seeded` — 已建立第一版內容，但仍在驗證",
        "- `active` — 持續更新中的主題頁",
        "- `stale` — 長時間未更新，或內容需要檢查",
        "", "## Topics",
    ]
    for path in sorted(TOPICS_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        try:
            fm, _ = parse_frontmatter(content)
        except ValueError:
            continue
        title = fm.get("title", path.stem)
        status = fm.get("status", "draft")
        sources = fm.get("sources", "0")
        updated = fm.get("last_updated", "unknown")
        lines.append(
            f"- [{title}](topics/{path.name}) — | status: {status} | sources: {sources} | last_updated: {updated}"
        )
    lines += ["", "## Meta", "- [Wiki Schema](WIKI-SCHEMA.md)", "- [Wiki Log](log.md)", ""]
    if apply:
        INDEX_PATH.write_text("\n".join(lines), encoding="utf-8")


def append_log(messages: list[str], apply: bool) -> None:
    if not messages:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    block = "\n".join(
        f"## [{timestamp}] ingest-xkb | {msg}" for msg in messages
    ) + "\n"
    if apply:
        existing = LOG_PATH.read_text(encoding="utf-8")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            if not existing.endswith("\n"):
                fh.write("\n")
            fh.write(block)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync x-knowledge-base cards into wiki (v2: LLM absorb gate)"
    )
    parser.add_argument("--topic", help="only sync one topic slug")
    parser.add_argument("--limit", type=int, default=15, help="max cards per topic per run")
    parser.add_argument("--review", action="store_true", help="show LLM judgment without writing")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--no-llm", action="store_true", help="skip LLM, list all candidates")
    args = parser.parse_args()

    api_key = os.environ.get("LLM_API_KEY", "")
    use_llm = bool(api_key) and not args.no_llm
    if not api_key and not args.no_llm:
        print("WARNING: LLM_API_KEY not set. Use --no-llm to list candidates without LLM.")

    topic_map = load_topic_map()
    review_decisions = load_review_decisions()
    items = load_search_items()
    existing_urls_by_topic = collect_topic_existing_urls()
    grouped = iter_mapped_cards(
        items, topic_map, args.topic, existing_urls_by_topic, review_decisions
    )

    if not grouped:
        print("No mapped cards found for requested scope.")
        return 0

    messages: list[str] = []
    decision_records: list[dict] = []

    # Stats for absorb report
    stats = {
        "candidates": 0,
        "approved": 0,
        "skipped_llm": 0,
        "skipped_manual": 0,
        "manual_allow": 0,
        "no_llm_passthrough": 0,
    }

    for topic, cards in sorted(grouped.items()):
        topic_path = TOPICS_DIR / f"{topic}.md"
        if not topic_path.exists():
            print(f"skip {topic}: not seeded yet")
            continue

        wiki_content = topic_path.read_text(encoding="utf-8")
        print(f"\n--- {topic}: {len(cards)} candidates ---")

        if args.review:
            for card in cards[:args.limit + 5]:
                if use_llm:
                    card_content = load_card_content(card.path)
                    include, dimension, reason = llm_absorb_judgment(
                        card, topic, wiki_content, card_content, api_key
                    )
                    verdict = "INCLUDE" if (include and dimension != "none") else "SKIP"
                    print(f"  [{verdict}] {card.title[:60]}")
                    print(f"    dim: {dimension} | {reason}")
                    print(f"    url: {card.url}")
                else:
                    print(f"  [?] {card.title[:60]}")
                    print(f"    tags: {', '.join(card.tags[:5])}")
                    print(f"    url: {card.url}")
            continue

        # Apply / dry-run: run LLM absorb filter
        approved: list[Card] = []
        hard_allow = set((review_decisions.get(topic) or {}).get("allow") or [])

        for card in cards[:args.limit]:
            stats["candidates"] += 1
            if card.url in hard_allow:
                approved.append(card)
                stats["manual_allow"] += 1
                print(f"  ALLOW (manual) {card.title[:60]}")
                decision_records.append({
                    "url": card.url, "topic": topic,
                    "decision": "manual-allow", "dimension": "", "reason": "manual allow list",
                })
                continue
            if use_llm:
                card_content = load_card_content(card.path)
                include, dimension, reason = llm_absorb_judgment(
                    card, topic, wiki_content, card_content, api_key
                )
                if include and dimension != "none":
                    print(f"  PASS [{dimension}] {card.title[:55]} — {reason}")
                    approved.append(card)
                    stats["approved"] += 1
                    decision_records.append({
                        "url": card.url, "topic": topic,
                        "decision": "approve", "dimension": dimension, "reason": reason,
                    })
                else:
                    lbl = "low-value" if dimension == "none" else "skip"
                    print(f"  {lbl} [{dimension}] {card.title[:55]}")
                    stats["skipped_llm"] += 1
                    decision_records.append({
                        "url": card.url, "topic": topic,
                        "decision": "skip", "dimension": dimension, "reason": reason,
                    })
            else:
                approved.append(card)
                stats["no_llm_passthrough"] += 1

        if not approved:
            print(f"  no cards passed absorb gate")
            continue

        changed, message = update_topic_file(topic, approved, apply=args.apply)
        print(f"  {message}")
        if changed:
            messages.append(message)

    # --- Absorb Report ---
    print("\n" + "─" * 50)
    print("  Absorb Report")
    print("─" * 50)
    print(f"  Candidates evaluated : {stats['candidates']}")
    print(f"  Approved (LLM)       : {stats['approved']}")
    print(f"  Manual allow         : {stats['manual_allow']}")
    print(f"  Skipped (LLM gate)   : {stats['skipped_llm']}")
    if stats["no_llm_passthrough"]:
        print(f"  Pass-through (no-LLM): {stats['no_llm_passthrough']}")
    total_approved = stats["approved"] + stats["manual_allow"] + stats["no_llm_passthrough"]
    if stats["candidates"]:
        rate = 100 * total_approved // stats["candidates"]
        print(f"  Approval rate        : {rate}%")
    if decision_records:
        skip_reasons: dict[str, int] = {}
        for r in decision_records:
            if r["decision"] == "skip":
                dim = r.get("dimension") or "none"
                skip_reasons[dim] = skip_reasons.get(dim, 0) + 1
        if skip_reasons:
            print("  Skip breakdown:")
            for dim, cnt in sorted(skip_reasons.items(), key=lambda x: -x[1]):
                print(f"    {cnt:3d}  {dim}")
    print("─" * 50)

    if messages and args.apply:
        update_index(apply=True)
        append_log(messages, apply=True)
        save_absorb_decisions(decision_records)
        print(f"\nApplied: {len(messages)} topic(s) updated. Decisions saved.")
    elif messages:
        print(f"\n[dry-run] would update {len(messages)} topic(s). Use --apply to write.")
    else:
        if args.apply and decision_records:
            save_absorb_decisions(decision_records)
        print("\nNo topics needed updates.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
