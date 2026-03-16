#!/usr/bin/env python3
"""
Build vector index for x-knowledge-base semantic search.

Reads search_index.json, embeds each card's title + summary,
and saves vectors to vector_index.json in BOOKMARKS_DIR.

Usage:
    python3 scripts/build_vector_index.py
    python3 scripts/build_vector_index.py --incremental   # skip already-embedded cards
    python3 scripts/build_vector_index.py --dry-run       # show what would be done

Requires:
    EMBEDDING_PROVIDER=gemini|openai|ollama
    + corresponding API key (see .env.example)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from scripts/ directory or skill root
_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR))
from tools.embedding_providers import get_provider

WORKSPACE_DIR = Path(os.getenv("OPENCLAW_WORKSPACE", os.getenv("WORKSPACE_DIR", str(Path.home() / ".openclaw" / "workspace"))))
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"
VECTOR_FILE = BOOKMARKS_DIR / "vector_index.json"


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_card_text(item: dict) -> str:
    """Build embeddable text from a search index item.
    Uses title + summary (already extracted by build_search_index.sh).
    Falls back to reading the .md file if summary is empty.
    """
    title = (item.get("title") or "").strip()
    summary = (item.get("summary") or "").strip()

    if not summary:
        # Try reading the .md file directly
        rel_path = item.get("relative_path") or item.get("path") or ""
        if rel_path:
            md_path = BOOKMARKS_DIR / rel_path if not rel_path.startswith("/") else Path(rel_path)
            summary = _extract_summary_from_md(md_path)

    parts = [p for p in [title, summary] if p]
    return ". ".join(parts)[:500]  # cap at 500 chars


def _extract_summary_from_md(md_path: Path) -> str:
    if not md_path.exists():
        return ""
    try:
        content = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    # New format: ## 📝 一句話摘要 / Old format: ## 摘要
    for pattern in [
        r"##\s+[^\n]*一句[話话]摘要\s*\n(.+?)(?=\n##|\Z)",
        r"##\s+摘要\s*\n(.+?)(?=\n##|\Z)",
    ]:
        m = re.search(pattern, content, re.DOTALL)
        if m:
            text = m.group(1).strip().replace("\n", " ")
            text = re.sub(r"\s+", " ", text)
            return text[:300]
    return ""


# ── Cosine similarity (pure Python, no numpy) ─────────────────────────────────

def cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    denom = norm_a * norm_b
    return dot / denom if denom > 1e-9 else 0.0


# ── Load / save ───────────────────────────────────────────────────────────────

def load_vector_index(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"meta": {}, "vectors": {}}


def save_vector_index(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Build vector index for x-knowledge-base")
    parser.add_argument("--incremental", action="store_true",
                        help="Skip cards already in vector_index.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without calling embedding API")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--index-file", default=str(INDEX_FILE))
    parser.add_argument("--vector-file", default=str(VECTOR_FILE))
    args = parser.parse_args()

    index_path = Path(args.index_file)
    vector_path = Path(args.vector_file)

    # Load search index
    if not index_path.exists():
        print(f"❌ search_index.json not found: {index_path}", file=sys.stderr)
        return 1

    raw = json.loads(index_path.read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    print(f"📚 Loaded {len(items)} cards from {index_path}")

    # Load existing vectors (for incremental mode)
    existing = load_vector_index(vector_path)
    existing_vectors: dict = existing.get("vectors", {})

    # Determine which cards to embed
    to_embed = []
    for item in items:
        key = item.get("relative_path") or item.get("path") or ""
        if args.incremental and key in existing_vectors:
            continue
        text = extract_card_text(item)
        if text.strip():
            to_embed.append((key, text))
        else:
            print(f"  ⚠️  No text for: {key}")

    skipped = len(items) - len(to_embed)
    print(f"🔢 To embed: {len(to_embed)}  |  Skipped (incremental): {skipped}")

    if args.dry_run:
        print("\n[dry-run] First 3 cards that would be embedded:")
        for key, text in to_embed[:3]:
            print(f"  {key}")
            print(f"    → \"{text[:80]}...\"")
        return 0

    if not to_embed:
        print("✅ Nothing to embed.")
        return 0

    # Init provider
    try:
        provider = get_provider()
        print(f"🤖 Provider: {provider.__class__.__name__} / model: {getattr(provider, 'model', '?')}")
    except EnvironmentError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    # Embed in batches
    keys = [k for k, _ in to_embed]
    texts = [t for _, t in to_embed]
    vectors_list = []

    batch_size = args.batch_size
    total = len(texts)
    for i in range(0, total, batch_size):
        batch_texts = texts[i:i + batch_size]
        batch_keys = keys[i:i + batch_size]
        print(f"  Embedding {i+1}–{min(i+batch_size, total)}/{total}...", end=" ", flush=True)
        try:
            batch_vecs = provider.embed_batch(batch_texts, batch_size=batch_size)
            vectors_list.extend(zip(batch_keys, batch_vecs))
            print("✓")
        except Exception as e:
            print(f"\n❌ Failed at batch {i//batch_size + 1}: {e}", file=sys.stderr)
            return 1

    # Merge with existing vectors
    new_vectors = dict(existing_vectors)
    for key, vec in vectors_list:
        new_vectors[key] = vec

    # Save
    output = {
        "meta": {
            "provider": provider.__class__.__name__.replace("Provider", "").lower(),
            "model": getattr(provider, "model", ""),
            "dims": len(vectors_list[0][1]) if vectors_list else existing.get("meta", {}).get("dims", 0),
            "total": len(new_vectors),
            "built_at": datetime.now(timezone.utc).isoformat(),
        },
        "vectors": new_vectors,
    }
    save_vector_index(output, vector_path)

    print(f"\n✅ Saved {len(new_vectors)} vectors → {vector_path}")
    print(f"   Provider : {output['meta']['provider']} / {output['meta']['model']}")
    print(f"   Dims     : {output['meta']['dims']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
