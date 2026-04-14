#!/usr/bin/env python3
"""
xbrain_recall.py — XKB hybrid search bridge (XBrain layer)

Provides semantic + keyword hybrid search over the XKB knowledge base
using an embedded vector store (pgvector + RRF + Gemini embedding).

Usage:
    python3 scripts/xbrain_recall.py "agent memory workflow"
    python3 scripts/xbrain_recall.py "SEO 策略" --limit 5 --json
    python3 scripts/xbrain_recall.py "query" --no-semantic   # keyword-only

Environment / config (in priority order):
    GBRAIN_DIR          path to vector store runtime (overrides all)
    GEMINI_API_KEY      required for semantic (vector) search
    OPENCLAW_JSON       path to openclaw.json config (default: ~/.openclaw/openclaw.json)
    ~/.openclaw/openclaw.json  fallback config: { "env": { "GEMINI_API_KEY": "...", "gbrain_dir": "..." } }
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── Config ─────────────────────────────────────────────────────────────────────

def _read_openclaw_env() -> dict[str, str]:
    """Read env dict from ~/.openclaw/openclaw.json (or OPENCLAW_JSON path)."""
    try:
        cfg_path = Path(os.getenv("OPENCLAW_JSON",
                                  str(Path.home() / ".openclaw" / "openclaw.json")))
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8")).get("env", {})
    except Exception:
        pass
    return {}


_OPENCLAW_ENV = _read_openclaw_env()


def _resolve_gbrain_dir() -> Path | None:
    """Find the gbrain runtime directory, in priority order."""
    candidates = [
        os.getenv("GBRAIN_DIR"),
        _OPENCLAW_ENV.get("gbrain_dir"),
        str(Path.home() / "Desktop" / "gbrain"),
        str(Path.home() / "gbrain"),
        "/opt/gbrain",
    ]
    for c in candidates:
        if c and Path(c).joinpath("src", "cli.ts").exists():
            return Path(c)
    return None


GBRAIN_DIR = _resolve_gbrain_dir()
GBRAIN_AVAILABLE = GBRAIN_DIR is not None
BUN = "bun"

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or _OPENCLAW_ENV.get("GEMINI_API_KEY", "")
)


def _make_subprocess_env(semantic: bool) -> dict[str, str]:
    """Build subprocess env, inheriting current env + openclaw overrides."""
    env = {**os.environ}
    # Propagate OPENCLAW_JSON so sub-processes can also find the config
    if "OPENCLAW_JSON" not in env:
        cfg = Path.home() / ".openclaw" / "openclaw.json"
        if cfg.exists():
            env["OPENCLAW_JSON"] = str(cfg)
    if semantic and GEMINI_API_KEY:
        env["GEMINI_API_KEY"] = GEMINI_API_KEY
    elif not semantic:
        env.pop("GEMINI_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)
    return env


# ── Core query ─────────────────────────────────────────────────────────────────

def xbrain_query(
    query: str,
    *,
    limit: int = 10,
    no_expand: bool = True,
    semantic: bool = True,
) -> list[dict[str, Any]]:
    """
    Run hybrid search and return structured results.
    Returns [] if XBrain is unavailable or query fails (never raises).

    Result keys: slug, title, type, chunk_text, score, source_url, stale
    """
    if not GBRAIN_AVAILABLE or GBRAIN_DIR is None:
        return []

    cmd = [BUN, "run", str(GBRAIN_DIR / "src" / "cli.ts")]
    cmd += ["query", query, "--json"]
    if no_expand:
        cmd += ["--no-expand"]
    if limit != 20:
        cmd += ["--limit", str(limit)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_make_subprocess_env(semantic),
            cwd=str(GBRAIN_DIR),
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        # bun not in PATH
        return []
    except Exception:
        return []

    if result.returncode != 0:
        return []

    raw = result.stdout.strip()
    if not raw or raw == "No results.":
        return []

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []

    results = []
    for item in items:
        chunk = item.get("chunk_text", "")
        slug = item.get("slug", "")
        source_url = (
            item.get("source_url")
            or _extract_source_url(chunk)
            or _url_from_slug(slug)
        )
        results.append({
            "slug": slug,
            "title": item.get("title", ""),
            "type": item.get("type", ""),
            "chunk_text": chunk,
            "score": item.get("score", 0.0),
            "source_url": source_url,
            "stale": item.get("stale", False),
        })
    return results


def _extract_source_url(text: str) -> str:
    """Extract source URL from card content (multiple patterns)."""
    import re
    patterns = [
        r"Source URL:\s*(https?://\S+)",
        r"來源:\s*(https?://\S+)",
        r"source_url:\s*(https?://\S+)",
        r"- (?:Tweet|原始來源|Source):\s*(https?://\S+)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).rstrip(".,)")
    return ""


def _url_from_slug(slug: str) -> str:
    """Reconstruct source URL from slug naming conventions."""
    import re
    # Tweet IDs: 15-20 digit slugs → x.com URL
    if re.fullmatch(r"\d{15,20}", slug):
        return f"https://x.com/i/status/{slug}"
    # legacy-* slugs derived from tweet files: extract numeric suffix
    m = re.search(r"(\d{15,20})$", slug)
    if m:
        return f"https://x.com/i/status/{m.group(1)}"
    # YouTube slugs: youtube-{video_id}
    m = re.match(r"youtube-([A-Za-z0-9_\-]{11})$", slug)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    # GitHub slugs: github_fork-owner-repo or github_star-owner-repo
    m = re.match(r"github_(?:fork|star)-(.+)-([^-]+)$", slug)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    return ""


# ── Formatted output ───────────────────────────────────────────────────────────

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


# ── Backward compatibility alias ──────────────────────────────────────────────
# Old scripts that import gbrain_recall can import from here instead.
gbrain_query = xbrain_query


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="XBrain hybrid search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true", dest="output_json")
    parser.add_argument("--no-semantic", action="store_true")
    args = parser.parse_args()

    if not GBRAIN_AVAILABLE:
        print("XBrain not available. Set GBRAIN_DIR or clone to ~/Desktop/gbrain.",
              file=sys.stderr)
        sys.exit(1)

    results = xbrain_query(
        args.query,
        limit=args.limit,
        semantic=not args.no_semantic,
    )

    if args.output_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(format_for_conversation(results, args.query))


if __name__ == "__main__":
    main()
