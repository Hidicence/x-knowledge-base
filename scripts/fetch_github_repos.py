#!/usr/bin/env python3
"""
fetch_github_repos.py

從 GitHub 抓取使用者的 fork/star repo，生成知識卡片（repo-level only，不做整包 repo ingest），
加入 search_index.json。

Usage:
    python3 scripts/fetch_github_repos.py --forks
    python3 scripts/fetch_github_repos.py --stars
    python3 scripts/fetch_github_repos.py --forks --stars
    python3 scripts/fetch_github_repos.py --forks --limit 20
    python3 scripts/fetch_github_repos.py --forks --dry-run

Exit codes:
    0  — success, 0 new cards
    1  — error
    2  — success, >= 1 new cards added
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Shared card prompt module ─────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from _card_prompt import (
    build_prompt, extract_summary, find_related_context,
    llm_call as _llm_call, SOURCE_LABELS, gbrain_put as _gbrain_put,
)
SOURCE_LABELS["github_fork"] = "GitHub 倉庫（Fork）"
SOURCE_LABELS["github_star"] = "GitHub 倉庫（Star）"

# ── Paths ──────────────────────────────────────────────────────────────────────
WORKSPACE_DIR = Path(
    os.getenv("OPENCLAW_WORKSPACE",
              os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace")))
)
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
CARDS_DIR = WORKSPACE_DIR / "memory" / "cards"
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"
GITHUB_RAW_DIR = BOOKMARKS_DIR / "github"   # raw metadata per repo (source layer)

CARD_CATEGORIES = [
    "ai-tools", "developer-tools", "workflows", "data",
    "startup", "design", "tech", "learning", "other"
]


def load_env_key() -> str:
    cfg_path = Path(os.getenv("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
    try:
        cfg = json.loads(cfg_path.read_text())
        env = cfg.get("env", {})
        return (env.get("LLM_API_KEY") or env.get("MINIMAX_API_KEY") or
                os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY") or "")
    except Exception:
        return os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY") or ""


# llm_call imported from _card_prompt as _llm_call


def gh_api(endpoint: str) -> list:
    """Call gh CLI with pagination and return merged list."""
    result = subprocess.run(
        ["gh", "api", "--paginate", endpoint],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  [ERROR] gh api {endpoint}: {result.stderr.strip()}", file=sys.stderr)
        return []
    text = result.stdout.strip()
    # gh --paginate may return multiple JSON arrays on separate lines
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, list):
                items.extend(parsed)
            elif isinstance(parsed, dict):
                items.append(parsed)
        except Exception:
            pass
    return items or json.loads(text) if text else []


def get_readme_preview(full_name: str, max_chars: int = 500) -> str:
    """Fetch README first N chars. Returns empty string on any error — not fatal."""
    result = subprocess.run(
        ["gh", "api", f"repos/{full_name}/readme", "--jq", ".content"],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    try:
        decoded = base64.b64decode(result.stdout.strip()).decode("utf-8", errors="ignore")
        decoded = re.sub(r'#+\s*', '', decoded)
        decoded = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', decoded)
        decoded = re.sub(r'!\[.*?\]\(.*?\)', '', decoded)
        return decoded[:max_chars].strip()
    except Exception:
        return ""


def load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"version": "1.1", "items": []}


def save_index(data: dict):
    INDEX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_frontmatter(card: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---", card, re.DOTALL)
    if not m:
        return {}
    result = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


# extract_summary imported from _card_prompt


def filter_stars(repos: list) -> list:
    return [r for r in repos
            if not r.get("archived")
            and (r.get("description") or r.get("topics"))]


def make_dedup_key(url: str, action_type: str) -> str:
    """Dedup key is (source_url, source_type) — fork and star of same repo are different entries."""
    return f"{url}|{action_type}"


def process_repos(repos: list, action_type: str, dry_run: bool, api_key: str,
                  existing_keys: set, limit: int,
                  existing_items: list[dict] | None = None) -> tuple[int, list[dict]]:
    """
    Returns (count_processed, new_index_items).
    Does NOT write to index — caller batches and saves once.
    """
    subdir = GITHUB_RAW_DIR / (action_type.replace("github_", "") + "s")
    subdir.mkdir(parents=True, exist_ok=True)
    CARDS_DIR.mkdir(parents=True, exist_ok=True)

    new_items = []
    processed = 0

    for repo in repos:
        if limit and processed >= limit:
            break

        full_name = repo.get("full_name", "")
        url = repo.get("html_url", f"https://github.com/{full_name}")
        dedup_key = make_dedup_key(url, action_type)

        if dedup_key in existing_keys:
            print(f"  [SKIP] 已存在：{full_name} ({action_type})")
            continue

        description = repo.get("description") or "(no description)"
        language = repo.get("language") or "unknown"
        topics = ", ".join(repo.get("topics", [])) or "(none)"
        stars = repo.get("stargazers_count", 0)

        print(f"  📦 {full_name} [{language}] {'★'+str(stars) if stars else ''}")

        if dry_run:
            print(f"     → {description[:80]}")
            processed += 1
            continue

        # Save raw metadata (source layer)
        safe_name = full_name.replace("/", "__")
        raw_path = subdir / f"{safe_name}.json"
        raw_path.write_text(json.dumps({
            "full_name": full_name, "url": url, "description": description,
            "language": language, "topics": repo.get("topics", []),
            "stars": stars, "archived": repo.get("archived", False),
            "pushed_at": repo.get("pushed_at", ""), "action_type": action_type,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        # Fetch README preview (non-fatal)
        readme = get_readme_preview(full_name)

        # Card ID (also used in frontmatter and filename)
        card_id = f"{action_type}-{full_name.replace('/', '-')}"

        # Generate 9-section card via shared _card_prompt
        content = (
            f"Repo: {full_name}\n"
            f"Description: {description}\n"
            f"Language: {language}\n"
            f"Topics: {topics}\n"
            f"Stars: {stars}\n"
            f"Action: {action_type} "
            f"(github_fork = 主動複製並可能修改；github_star = 標記感興趣)\n\n"
            f"README:\n{readme or '(not available)'}"
        )
        related = find_related_context(content, existing_items or [])
        prompt = build_prompt(
            content=content,
            card_id=card_id,
            source_type=action_type,
            source_url=url,
            category="tech",
            related_context=related,
        )
        try:
            card_content = _llm_call(prompt, api_key)
        except Exception as e:
            print(f"     ❌ LLM 失敗：{e}")
            continue

        # Inject id into frontmatter if missing
        if "---\n" in card_content and "id:" not in card_content:
            card_content = card_content.replace("---\n", f"---\nid: {card_id}\n", 1)

        # Save LLM card to memory/cards/
        card_path = CARDS_DIR / f"{card_id}.md"
        card_path.write_text(card_content, encoding="utf-8")
        _gbrain_put(card_path, card_id)
        print(f"     💾 memory/cards/{card_id}.md")

        # Build index item
        fm = extract_frontmatter(card_content)
        summary = extract_summary(card_content)
        tags_raw = fm.get("tags", "")
        tags = list({t.strip() for t in tags_raw.split(",") if t.strip()})
        if language and language != "unknown":
            tags.append(language.lower())

        new_items.append({
            "path": str(card_path),
            "relative_path": f"cards/{card_id}.md",
            "title": fm.get("title", full_name),
            "category": fm.get("category", "tech"),
            "tags": list(set(tags)),
            "summary": summary,
            "source_url": url,
            "source_type": action_type,
            "searchable": f"{full_name} {description} {topics} {summary} {' '.join(tags)}",
            "mtime": datetime.now(timezone.utc).isoformat(),
            "size": card_path.stat().st_size,
            "enriched": True,
            "language": language,
        })
        processed += 1

    return processed, new_items


def main():
    parser = argparse.ArgumentParser(
        description="Fetch GitHub forks/stars into XKB (repo-level cards only)")
    parser.add_argument("--forks", action="store_true")
    parser.add_argument("--stars", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max repos per type (0=all)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-filter", action="store_true", help="Disable star quality filter")
    args = parser.parse_args()

    if not args.forks and not args.stars:
        parser.error("Specify --forks and/or --stars")

    api_key = "" if args.dry_run else load_env_key()
    if not api_key and not args.dry_run:
        print("[ERROR] LLM_API_KEY not found")
        sys.exit(1)

    # Build dedup key set from existing index
    index_data = load_index()
    existing_items_list = index_data.get("items", [])
    existing_keys = {
        make_dedup_key(item.get("source_url", ""), item.get("source_type", ""))
        for item in existing_items_list
    }

    all_new_items: list[dict] = []
    total = 0

    if args.forks:
        print("\n🔀 Fetching forks...")
        repos = gh_api("user/repos?type=fork&per_page=100")
        print(f"   Found {len(repos)} forks")
        n, items = process_repos(repos, "github_fork", args.dry_run, api_key,
                                 existing_keys, args.limit, existing_items_list)
        print(f"   ✅ Processed {n} fork cards")
        total += n
        all_new_items.extend(items)

    if args.stars:
        print("\n⭐ Fetching stars...")
        repos = gh_api("user/starred?per_page=100")
        if not args.no_filter:
            before = len(repos)
            repos = filter_stars(repos)
            print(f"   Found {before} stars → {len(repos)} after filtering")
        else:
            print(f"   Found {len(repos)} stars (no filter)")
        n, items = process_repos(repos, "github_star", args.dry_run, api_key,
                                 existing_keys, args.limit, existing_items_list)
        print(f"   ✅ Processed {n} star cards")
        total += n
        all_new_items.extend(items)

    # Batch merge into index — one write at the end
    if all_new_items and not args.dry_run:
        index_data["items"].extend(all_new_items)
        save_index(index_data)
        print(f"\n✅ 完成：共新增 {total} 張 GitHub 知識卡片，已寫入索引")
        print("💡 執行以下指令更新語意索引：")
        print("   python3 scripts/build_vector_index.py --incremental")
    else:
        print(f"\n✅ 完成：共新增 {total} 張 GitHub 知識卡片")

    # Exit code 2 = new cards added (for run_github_sync.sh to detect)
    sys.exit(2 if total > 0 and not args.dry_run else 0)


if __name__ == "__main__":
    main()
