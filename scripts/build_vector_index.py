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
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from scripts/ directory or skill root
sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths

_SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_DIR))
from tools.embedding_providers import get_provider

WORKSPACE_DIR = xkb_paths.WORKSPACE
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(WORKSPACE_DIR / "memory" / "bookmarks")))
INDEX_FILE = BOOKMARKS_DIR / "search_index.json"
VECTOR_FILE = BOOKMARKS_DIR / "vector_index.json"


# ── Text extraction ───────────────────────────────────────────────────────────

def _resolve_md_path(item: dict) -> Path | None:
    """索引項路徑解析：relative_path 是相對 workspace（如 memory/cards/x.md），
    舊程式誤用 BOOKMARKS_DIR 拼接導致永遠讀不到卡片（2026-07-14 修正）。"""
    abs_path = item.get("path") or ""
    if abs_path and Path(abs_path).exists():
        return Path(abs_path)
    rel = item.get("relative_path") or ""
    if not rel:
        return None
    for base in (WORKSPACE_DIR, BOOKMARKS_DIR):
        p = base / rel
        if p.exists():
            return p
    return None


def _extract_key_points_from_md(md_path: Path) -> str:
    """Extract the 關鍵論點/三個重點 section from a card markdown file.
    （2026-07-14：新版 9-section 卡片段落名為「關鍵論點」，一併支援舊名）"""
    if not md_path.exists():
        return ""
    try:
        content = md_path.read_text(encoding="utf-8")
        m = re.search(
            r'##\s+[^\n]*(?:關鍵論點|三個重點)[^\n]*\n(.+?)(?=\n##|\Z)',
            content, re.DOTALL
        )
        if m:
            text = re.sub(r'^[\-\*\•]\s*', '', m.group(1).strip(), flags=re.MULTILINE)
            return text.strip()
    except Exception:
        pass
    return ""


def _extract_key_point_list(md_path: Path) -> list[str]:
    """論點級 embedding（TODOS 2026-07-13）：回傳「三個重點」逐條列表，
    每條將單獨成向量（鍵 relpath#kpN），召回粒度從卡片級升到論點級。"""
    text = _extract_key_points_from_md(md_path)
    if not text:
        return []
    points = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 10]
    return points[:5]


def extract_card_text(item: dict) -> str:
    """Build embeddable text from a search index item.
    Uses title + summary (already extracted by build_search_index.sh).
    Falls back to reading the .md file if summary is empty.
    """
    title = (item.get("title") or "").strip()
    summary = (item.get("summary") or "").strip()
    key_points = ""

    md_path = _resolve_md_path(item)
    if md_path:
        if not summary:
            summary = _extract_summary_from_md(md_path)
        key_points = _extract_key_points_from_md(md_path)

    parts = [p for p in [title, summary, key_points] if p]
    return ". ".join(parts)[:900]  # cap at 900 chars (expanded for key points)


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


# ── Wiki sections ─────────────────────────────────────────────────────────────

# 一段太短的內容嵌入後幾乎沒有訊息量，只會製造假命中
MIN_SECTION_CHARS = 80
# 一段太長會把主題稀釋掉，超過就截斷
MAX_SECTION_CHARS = 4000


def wiki_section_docs() -> list[tuple[str, str, str]]:
    """把 wiki 主題頁切段，每段一個向量。回傳 [(key, text, hash)]。

    為什麼要做這件事：向量索引原本只涵蓋卡片。wiki——也就是使用者自己消化過、
    訊號密度最高的那一層——完全沒有語意搜尋，只能靠字串比對撈。
    中文沒有空格，字串比對本來就弱，於是「我們之前怎麼處理碳盤查的」會撈回
    一堆只命中「之前」「怎麼」「處理」的無關結果。那不是門檻調不好，
    是那一層根本沒有語意能力。

    切到「段」而不是整頁：一頁 680K 的主題頁嵌成單一向量，等於什麼都不像。
    """
    topics_dir = xkb_paths.WIKI_TOPICS_DIR
    if not topics_dir.exists():
        return []

    docs: list[tuple[str, str, str]] = []
    for path in sorted(topics_dir.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)

        title = path.stem
        section = ""
        buffer: list[str] = []

        def flush(section_name: str, lines: list[str]) -> None:
            body = "\n".join(lines).strip()
            if len(body) < MIN_SECTION_CHARS:
                return
            text = f"{title} — {section_name}\n{body}"[:MAX_SECTION_CHARS]
            key = f"wiki/topics/{path.name}#{section_name or 'intro'}"
            docs.append((key, text, hashlib.md5(text.encode("utf-8")).hexdigest()[:12]))

        for line in content.splitlines():
            if re.match(r"^#{1,3} .+", line):
                flush(section, buffer)
                section = re.sub(r"^#{1,3} ", "", line).strip()
                buffer = []
            else:
                buffer.append(line)
        flush(section, buffer)

    return docs


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
    existing_hashes: dict = existing.get("text_hashes", {})

    to_embed = []

    # wiki 段落：把消化過的那一層也納入語意搜尋
    for wiki_key, wiki_text, wiki_hash in wiki_section_docs():
        if not (args.incremental and wiki_key in existing_vectors
                and existing_hashes.get(wiki_key) == wiki_hash):
            to_embed.append((wiki_key, wiki_text, wiki_hash))
    print(f"📖 Wiki sections queued: {len(to_embed)}")

    for item in items:
        key = item.get("relative_path") or item.get("path") or ""
        card_text = extract_card_text(item)
        if not card_text.strip():
            print(f"  ⚠️  No text for: {key}")
            continue
        text_hash = hashlib.md5(card_text.encode("utf-8")).hexdigest()[:12]
        if not (args.incremental and key in existing_vectors and existing_hashes.get(key) == text_hash):
            to_embed.append((key, card_text, text_hash))

        # 論點級 embedding（TODOS 2026-07-13）：每條「關鍵論點」單獨成向量
        md_path = _resolve_md_path(item)
        if md_path:
            title = (item.get("title") or "").strip()
            for pi, point in enumerate(_extract_key_point_list(md_path), 1):
                pkey = f"{key}#kp{pi}"
                ptext = (f"{title}. {point}" if title else point)[:500]
                phash = hashlib.md5(ptext.encode("utf-8")).hexdigest()[:12]
                if args.incremental and pkey in existing_vectors and existing_hashes.get(pkey) == phash:
                    continue
                to_embed.append((pkey, ptext, phash))

    # Alias for downstream use
    texts = [t for _, t, _ in to_embed]

    skipped = sum(1 for k in existing_vectors if k not in {e[0] for e in to_embed})
    print(f"🔢 To embed: {len(to_embed)}  |  Skipped (incremental): {skipped}")

    if args.dry_run:
        print("\n[dry-run] First 3 cards that would be embedded:")
        for key, text, _text_hash in to_embed[:3]:
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
    keys = [k for k, _, _ in to_embed]
    hashes = [h for _, _, h in to_embed]
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

    # Merge with existing vectors and hashes
    new_vectors = dict(existing_vectors)
    new_hashes = dict(existing_hashes)
    for (key, vec), h in zip(vectors_list, hashes):
        new_vectors[key] = vec
        new_hashes[key] = h

    # Save
    output = {
        "meta": {
            "provider": provider.__class__.__name__.replace("Provider", "").lower(),
            "model": getattr(provider, "model", ""),
            "dims": len(vectors_list[0][1]) if vectors_list else existing.get("meta", {}).get("dims", 0),
            "total": len(new_vectors),
            "point_vectors": sum(1 for k in new_vectors if "#kp" in k),
            "built_at": datetime.now(timezone.utc).isoformat(),
        },
        "vectors": new_vectors,
        "text_hashes": new_hashes,
    }
    save_vector_index(output, vector_path)

    print(f"\n✅ Saved {len(new_vectors)} vectors → {vector_path}")
    print(f"   Provider : {output['meta']['provider']} / {output['meta']['model']}")
    print(f"   Dims     : {output['meta']['dims']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
