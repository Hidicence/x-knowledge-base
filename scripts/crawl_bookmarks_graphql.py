#!/usr/bin/env python3
"""
Crawl X bookmarks directly via GraphQL, page by page, with resume support.

This bypasses bird pagination instability by calling the bookmarks GraphQL endpoint
with session cookies directly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
SECRETS_FILE = WORKSPACE / ".secrets" / "x-knowledge-base.env"
STATE_FILE = WORKSPACE / "memory" / "x-knowledge-base" / "graphql-bookmarks-crawl.json"
BOOKMARKS_QUERY = "qToeLeMs43Q8cr7tRYXmaQ"
BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
FEATURES = {
    "graphql_timeline_v2_bookmark_timeline": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_media_download_video_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
}


def load_env() -> None:
    if not SECRETS_FILE.exists():
        return
    for line in SECRETS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)


def walk_entries(node: Any, entries: list[dict]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "entries" and isinstance(v, list):
                entries.extend(v)
            walk_entries(v, entries)
    elif isinstance(node, list):
        for item in node:
            walk_entries(item, entries)


def parse_page(data: dict[str, Any]) -> tuple[list[str], str | None]:
    entries: list[dict] = []
    walk_entries(data.get("data", {}), entries)
    tweet_ids: list[str] = []
    next_cursor = None
    for e in entries:
        eid = e.get("entryId", "")
        m = re.match(r"tweet-(\d+)", eid)
        if m:
            tweet_ids.append(m.group(1))
        content = e.get("content", {})
        if content.get("entryType") == "TimelineTimelineCursor":
            value = content.get("value")
            if value and (content.get("cursorType") == "Bottom" or "cursor-bottom" in eid):
                next_cursor = value
    return tweet_ids, next_cursor


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"pages": 0, "unique_ids": [], "cursor": None, "completed": False, "errors": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--delay-ms", type=int, default=1200)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    load_env()
    auth = os.getenv("BIRD_AUTH_TOKEN", "")
    ct0 = os.getenv("BIRD_CT0", "")
    if not auth or not ct0:
        print("❌ missing BIRD_AUTH_TOKEN / BIRD_CT0")
        return 1

    state = load_state(STATE_FILE)
    if args.restart:
        state = {"pages": 0, "unique_ids": [], "cursor": None, "completed": False, "errors": []}
    seen = set(state.get("unique_ids", []))
    cursor = state.get("cursor")
    pages = int(state.get("pages", 0))

    headers = {
        "authorization": f"Bearer {urllib.parse.unquote(BEARER)}",
        "x-csrf-token": ct0,
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
        "cookie": f"auth_token={auth}; ct0={ct0}",
        "user-agent": "Mozilla/5.0",
        "accept": "*/*",
        "referer": "https://x.com/i/bookmarks",
        "origin": "https://x.com",
    }

    with httpx.Client(headers=headers, timeout=args.timeout) as client:
        for _ in range(args.max_pages):
            variables = {"count": args.count, "includePromotedContent": True}
            if cursor:
                variables["cursor"] = cursor
            params = {
                "variables": json.dumps(variables, separators=(",", ":")),
                "features": json.dumps(FEATURES, separators=(",", ":")),
            }
            url = f"https://x.com/i/api/graphql/{BOOKMARKS_QUERY}/Bookmarks?" + urllib.parse.urlencode(params)
            try:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                state.update({"pages": pages, "unique_ids": sorted(seen), "cursor": cursor, "completed": False})
                state.setdefault("errors", []).append(str(e))
                save_state(STATE_FILE, state)
                print(json.dumps({"ok": False, "pages": pages, "unique_count": len(seen), "cursor": cursor, "error": str(e)}, ensure_ascii=False, indent=2))
                return 2

            page_ids, next_cursor = parse_page(data)
            if not page_ids:
                state.update({"pages": pages, "unique_ids": sorted(seen), "cursor": None, "completed": True})
                save_state(STATE_FILE, state)
                print(json.dumps({"ok": True, "completed": True, "pages": pages, "unique_count": len(seen)}, ensure_ascii=False, indent=2))
                return 0

            pages += 1
            before = len(seen)
            seen.update(page_ids)
            added = len(seen) - before
            cursor = next_cursor
            state.update({"pages": pages, "unique_ids": sorted(seen), "cursor": cursor, "completed": False})
            save_state(STATE_FILE, state)
            print(json.dumps({"page": pages, "page_count": len(page_ids), "added_unique": added, "unique_count": len(seen), "has_next_cursor": bool(next_cursor)}, ensure_ascii=False))
            if not next_cursor:
                state.update({"pages": pages, "unique_ids": sorted(seen), "cursor": None, "completed": True})
                save_state(STATE_FILE, state)
                print(json.dumps({"ok": True, "completed": True, "pages": pages, "unique_count": len(seen)}, ensure_ascii=False, indent=2))
                return 0
            time.sleep(args.delay_ms / 1000)

    state.update({"pages": pages, "unique_ids": sorted(seen), "cursor": cursor, "completed": False})
    save_state(STATE_FILE, state)
    print(json.dumps({"ok": True, "completed": False, "pages": pages, "unique_count": len(seen), "cursor": cursor}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
