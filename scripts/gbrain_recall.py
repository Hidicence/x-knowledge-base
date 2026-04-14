#!/usr/bin/env python3
"""
gbrain_recall.py — XKB recall bridge for gbrain

Replaces vector_index.json + search_index.json lookup with gbrain hybrid search
(pgvector + RRF + Gemini embedding).

Usage:
    python3 scripts/gbrain_recall.py "agent memory workflow"
    python3 scripts/gbrain_recall.py "SEO 策略" --limit 5 --json
    python3 scripts/gbrain_recall.py "query" --no-semantic   # keyword-only

Environment:
    GEMINI_API_KEY   required for semantic (vector) search
    GBRAIN_DIR       path to cloned+built gbrain repo (default: ~/Desktop/gbrain)
    OPENCLAW_WORKSPACE  (standard XKB env var)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────

GBRAIN_DIR = Path(os.getenv(
    "GBRAIN_DIR",
    str(Path.home() / "Desktop" / "gbrain"),
))
GBRAIN_CLI = GBRAIN_DIR / "src" / "cli.ts"
BUN = "bun"


def _read_openclaw_key(key: str) -> str:
    try:
        cfg_path = Path(os.getenv("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            return cfg.get("env", {}).get(key, "")
    except Exception:
        pass
    return ""


GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or _read_openclaw_key("GEMINI_API_KEY")
)


# ── Core call ─────────────────────────────────────────────────────────────────

def gbrain_query(
    query: str,
    *,
    limit: int = 10,
    no_expand: bool = True,
    semantic: bool = True,
) -> list[dict[str, Any]]:
    """
    Run gbrain hybrid search and return structured results.

    Returns list of dicts with keys: slug, title, type, chunk_text, score, source_url
    """
    if not GBRAIN_CLI.exists():
        raise RuntimeError(
            f"gbrain not found at {GBRAIN_DIR}. "
            "Set GBRAIN_DIR env var or clone gbrain to ~/Desktop/gbrain."
        )

    cmd = [BUN, "run", str(GBRAIN_CLI)]
    cmd += ["query", query, "--json"]
    if no_expand:
        cmd += ["--no-expand"]
    if limit != 20:
        cmd += ["--limit", str(limit)]

    env = {**os.environ}
    if semantic and GEMINI_API_KEY:
        env["GEMINI_API_KEY"] = GEMINI_API_KEY
    elif not semantic:
        # Remove embedding keys to force keyword-only
        env.pop("GEMINI_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=str(GBRAIN_DIR),
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("gbrain query timed out (30s)")
    except FileNotFoundError:
        raise RuntimeError(f"'{BUN}' not found. Install Bun: https://bun.sh")

    if result.returncode != 0:
        raise RuntimeError(f"gbrain error: {result.stderr.strip()[:300]}")

    raw = result.stdout.strip()
    if not raw or raw == "No results.":
        return []

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []

    # Normalise output: extract source_url from chunk_text if present
    results = []
    for item in items:
        chunk = item.get("chunk_text", "")
        source_url = _extract_source_url(chunk)
        results.append({
            "slug": item.get("slug", ""),
            "title": item.get("title", ""),
            "type": item.get("type", ""),
            "chunk_text": chunk,
            "score": item.get("score", 0.0),
            "source_url": source_url,
            "stale": item.get("stale", False),
        })
    return results


def _extract_source_url(text: str) -> str:
    """Try to find source URL from card content."""
    import re
    # Pattern: "- Source URL: https://..."
    m = re.search(r"Source URL:\s*(https?://\S+)", text)
    if m:
        return m.group(1).strip()
    # Pattern: "- 來源: https://..."
    m = re.search(r"來源:\s*(https?://\S+)", text)
    if m:
        return m.group(1).strip()
    return ""


# ── Formatted output (matches XKB recall style) ───────────────────────────────

def format_for_conversation(results: list[dict], query: str) -> str:
    """Format recall results for injecting into conversation context."""
    if not results:
        return ""

    lines = [f"📚 相關既有知識（query: {query!r}）\n"]
    for i, r in enumerate(results[:5], 1):
        title = r["title"] or r["slug"]
        url = r["source_url"]
        snippet = r["chunk_text"][:200].replace("\n", " ").strip()
        score_pct = f"{r['score'] * 100:.1f}%"

        lines.append(f"{i}. **{title}** ({score_pct})")
        if snippet:
            lines.append(f"   {snippet}…")
        if url:
            lines.append(f"   {url}")
        lines.append("")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="XKB gbrain recall bridge")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--json", action="store_true", dest="output_json", help="Output raw JSON")
    parser.add_argument("--no-semantic", action="store_true", help="Keyword-only, skip vector search")
    args = parser.parse_args()

    try:
        results = gbrain_query(
            args.query,
            limit=args.limit,
            semantic=not args.no_semantic,
        )
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(format_for_conversation(results, args.query))


if __name__ == "__main__":
    main()
