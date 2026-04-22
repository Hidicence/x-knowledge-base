#!/usr/bin/env python3
"""
Trigger gate for x-knowledge-base conversation recall.

Given a conversation snippet or a user query, decide whether recall should run,
build a short recall query, and optionally score whether surfaced results are worth showing.

Usage:
    python3 scripts/recall_gate.py "我想優化 OpenClaw workflow，有沒有值得抄的做法？"
    python3 scripts/recall_gate.py "最近 AI SEO 值不值得做" --json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from recall_for_conversation import tokenize, load_topic_profile, TOPIC_PROFILE_FILE, get_topic_profile_matches

STRONG_INTENT_PATTERNS = {
    "howto": [r"怎麼", r"如何", r"workflow", r"sop", r"流程", r"規劃", r"設計", r"做法", r"框架", r"framework"],
    "case_study": [r"案例", r"參考", r"靈感", r"對照", r"有沒有.*類似", r"值得抄", r"case study"],
    "strategy": [r"值不值得", r"要不要", r"方向", r"策略", r"比較", r"優先", r"下一步", r"decision"],
}

NEGATIVE_PATTERNS = [
    r"哈+", r"哈哈", r"早安", r"晚安", r"謝謝", r"收到", r"ok$", r"好$",
]


def detect_intents(text: str) -> list[str]:
    intents = []
    for intent, patterns in STRONG_INTENT_PATTERNS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            intents.append(intent)
    return intents


def is_negative_case(text: str) -> bool:
    lowered = text.strip().lower()
    if len(lowered) <= 8 and lowered in {"ok", "好", "收到", "謝謝", "thanks"}:
        return True
    return any(re.search(p, text, re.IGNORECASE) for p in NEGATIVE_PATTERNS)


def build_query(text: str, intents: list[str], topic_matches: dict[str, Any]) -> str:
    tokens = tokenize(text)
    topic_tokens = []
    if topic_matches.get("matched_categories"):
        for cat in topic_matches["matched_categories"][:2]:
            topic_tokens.extend(cat.split("-"))
    if topic_matches.get("matched_tags"):
        topic_tokens.extend(topic_matches["matched_tags"][:3])

    intent_words = []
    if "howto" in intents:
        intent_words.append("workflow")
    if "case_study" in intents:
        intent_words.append("case-study")
    if "strategy" in intents:
        intent_words.append("planning")

    merged = []
    seen = set()
    for token in topic_tokens + tokens[:8] + intent_words:
        token = token.strip().lower()
        if len(token) < 2:
            continue
        if token in seen:
            continue
        seen.add(token)
        merged.append(token)
    return " ".join(merged[:8])


def should_recall(text: str, topic_profile_path: Path = TOPIC_PROFILE_FILE) -> dict[str, Any]:
    text = text.strip()
    if not text:
        return {"should_recall": False, "reason": "empty_input", "query": ""}
    if is_negative_case(text):
        return {"should_recall": False, "reason": "casual_or_ack", "query": ""}

    intents = detect_intents(text)
    tokens = tokenize(text)
    topic_profile = load_topic_profile(topic_profile_path)
    topic_matches = get_topic_profile_matches(tokens, topic_profile) if topic_profile else {}
    topic_boost = topic_matches.get("topic_boost", 0.0)

    should = bool(intents) or topic_boost >= 0.35
    query = build_query(text, intents, topic_matches) if should else ""

    reasons = []
    if intents:
        reasons.append(f"intent:{','.join(intents)}")
    if topic_boost >= 0.35:
        reasons.append(f"topic_boost:{topic_boost}")

    return {
        "should_recall": should,
        "reason": ";".join(reasons) or "no_trigger",
        "query": query,
        "intents": intents,
        "topic_matches": topic_matches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger gate for x-knowledge-base recall")
    parser.add_argument("text", help="Current conversation text or user question")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--topic-profile-file", default=str(TOPIC_PROFILE_FILE))
    args = parser.parse_args()

    result = should_recall(args.text, Path(args.topic_profile_file))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"should_recall={str(result['should_recall']).lower()}")
        print(f"reason={result['reason']}")
        if result.get('query'):
            print(f"query={result['query']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
