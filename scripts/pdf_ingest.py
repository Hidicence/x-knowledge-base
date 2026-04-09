#!/usr/bin/env python3
"""
PDF Ingest — XKB Active Recall Layer

批次將 PDF 論文資料夾轉為知識卡片並建立向量索引。
支援多欄位學術論文、自動抽取標題/作者、清除頁首頁尾雜訊。

Usage:
  python3 pdf_ingest.py /path/to/papers/               # 整個資料夾
  python3 pdf_ingest.py paper.pdf                       # 單一檔案
  python3 pdf_ingest.py /path/to/papers/ --dry-run      # 預覽不寫入
  python3 pdf_ingest.py /path/to/papers/ --category research --tag ai
  python3 pdf_ingest.py /path/to/papers/ --rebuild-index # 重建向量索引
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import fitz  # pymupdf
except ImportError:
    print("Error: pymupdf not installed. Run: pip install pymupdf --break-system-packages")
    sys.exit(1)

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
SCRIPTS_DIR = WORKSPACE / "skills" / "x-knowledge-base" / "scripts"
INDEX_PATH = WORKSPACE / "memory" / "x-knowledge-base" / "search-index.json"
INGESTED_LOG = WORKSPACE / "memory" / "x-knowledge-base" / "pdf-ingested.json"

# ── Text cleaning ──────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Remove common academic PDF noise: page numbers, headers, footers, URLs."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        line = line.strip()
        # Skip very short lines (likely page numbers or artifacts)
        if len(line) <= 3:
            continue
        # Skip lines that are just numbers (page numbers)
        if re.fullmatch(r"\d+", line):
            continue
        # Skip common header/footer patterns
        if re.match(r"^(arXiv|doi:|https?://|©|Copyright|All rights reserved)", line, re.IGNORECASE):
            continue
        cleaned.append(line)
    # Collapse multiple blank lines
    text = "\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_title_author(doc: fitz.Document, filename: str) -> tuple[str, str]:
    """Try to extract title and author from PDF metadata, fallback to filename."""
    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip()
    author = (meta.get("author") or "").strip()

    # If no metadata title, try first page heuristic
    if not title and doc.page_count > 0:
        first_page_text = doc[0].get_text("text")
        lines = [l.strip() for l in first_page_text.splitlines() if len(l.strip()) > 10]
        if lines:
            # First substantial line is often the title
            title = lines[0][:150]

    # Fallback to filename
    if not title:
        title = Path(filename).stem.replace("_", " ").replace("-", " ")

    return title, author


# ── PDF extraction ─────────────────────────────────────────────────────────────

def extract_pdf(path: Path) -> Optional[dict]:
    """Extract text and metadata from a PDF. Returns None if extraction fails."""
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        print(f"  ✗ Failed to open {path.name}: {e}")
        return None

    if doc.page_count == 0:
        print(f"  ✗ Empty PDF: {path.name}")
        return None

    # Extract full text
    pages_text = []
    for page in doc:
        pages_text.append(page.get_text("text"))
    full_text = "\n\n".join(pages_text)
    full_text = _clean_text(full_text)

    if len(full_text) < 200:
        print(f"  ✗ Too little text extracted from {path.name} ({len(full_text)} chars)")
        return None

    title, author = _extract_title_author(doc, path.name)
    doc.close()

    # Generate a stable ID from file path
    file_id = hashlib.md5(str(path).encode()).hexdigest()[:12]

    # Summary = first 500 chars of cleaned text (intro/abstract area)
    summary = full_text[:500].replace("\n", " ").strip()
    if len(summary) > 480:
        summary = summary[:480] + "…"

    return {
        "id": file_id,
        "title": title,
        "author": author,
        "source_file": str(path),
        "filename": path.name,
        "page_count": len(pages_text),
        "char_count": len(full_text),
        "full_text": full_text,
        "summary": summary,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Knowledge card builder ─────────────────────────────────────────────────────

def _build_card(extracted: dict, category: str, tags: list[str]) -> dict:
    """Convert extracted PDF data into XKB knowledge card format."""
    return {
        "id": extracted["id"],
        "title": extracted["title"],
        "url": f"file://{extracted['source_file']}",
        "source_url": extracted["source_file"],
        "source_type": "pdf",
        "summary": extracted["summary"],
        "full_text": extracted["full_text"][:8000],  # Cap for index size
        "category": category,
        "tags": tags + (["author:" + extracted["author"]] if extracted["author"] else []),
        "author": extracted["author"],
        "page_count": extracted["page_count"],
        "fetched_at": extracted["extracted_at"],
        "quality_score": min(1.0, extracted["char_count"] / 10000),
    }


# ── Index management ───────────────────────────────────────────────────────────

def _load_index() -> list:
    if INDEX_PATH.exists():
        try:
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _save_index(cards: list) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_ingested_log() -> set:
    if INGESTED_LOG.exists():
        try:
            return set(json.loads(INGESTED_LOG.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_ingested_log(ingested: set) -> None:
    INGESTED_LOG.parent.mkdir(parents=True, exist_ok=True)
    INGESTED_LOG.write_text(json.dumps(sorted(ingested), ensure_ascii=False, indent=2), encoding="utf-8")


# ── Vector index rebuild ───────────────────────────────────────────────────────

def _rebuild_vector_index() -> None:
    script = SCRIPTS_DIR / "build_vector_index.py"
    if not script.exists():
        print("  ⚠ build_vector_index.py not found, skipping vector index")
        return
    print("\n🔢 Rebuilding vector index (Gemini embeddings)...")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=False,
        env={**os.environ, "OPENCLAW_WORKSPACE": str(WORKSPACE)},
    )
    if result.returncode == 0:
        print("  ✓ Vector index rebuilt")
    else:
        print("  ✗ Vector index rebuild failed (check GEMINI_API_KEY)")


# ── Main ───────────────────────────────────────────────────────────────────────

def ingest(
    path: Path,
    category: str = "research",
    tags: list[str] | None = None,
    dry_run: bool = False,
    rebuild_index: bool = False,
    force: bool = False,
) -> int:
    tags = tags or []
    pdf_files: list[Path] = []

    if path.is_dir():
        pdf_files = sorted(path.glob("**/*.pdf"))
        print(f"📂 Found {len(pdf_files)} PDF files in {path}")
    elif path.suffix.lower() == ".pdf":
        pdf_files = [path]
    else:
        print(f"Error: {path} is not a PDF or directory")
        return 1

    if not pdf_files:
        print("No PDF files found.")
        return 0

    ingested_ids = _load_ingested_log()
    existing_index = _load_index()
    existing_ids = {c.get("id") for c in existing_index}

    new_cards = []
    skipped = 0
    failed = 0

    for i, pdf_path in enumerate(pdf_files, 1):
        file_id = hashlib.md5(str(pdf_path).encode()).hexdigest()[:12]
        print(f"\n[{i}/{len(pdf_files)}] {pdf_path.name}")

        if not force and file_id in ingested_ids:
            print(f"  ↩ Already ingested, skipping (use --force to re-ingest)")
            skipped += 1
            continue

        extracted = extract_pdf(pdf_path)
        if not extracted:
            failed += 1
            continue

        card = _build_card(extracted, category, tags)
        print(f"  ✓ {extracted['title'][:70]}")
        print(f"    {extracted['page_count']} pages | {extracted['char_count']:,} chars | score={card['quality_score']:.2f}")

        if not dry_run:
            new_cards.append(card)
            ingested_ids.add(file_id)

    if dry_run:
        print(f"\n[dry-run] Would add {len(pdf_files) - skipped - failed} cards")
        print(f"  Skipped (already ingested): {skipped}")
        print(f"  Failed: {failed}")
        return 0

    if new_cards:
        # Merge into existing index (update if same id)
        updated_index = [c for c in existing_index if c.get("id") not in {c["id"] for c in new_cards}]
        updated_index.extend(new_cards)
        _save_index(updated_index)
        _save_ingested_log(ingested_ids)
        print(f"\n✅ Added {len(new_cards)} cards to index (total: {len(updated_index)})")
    else:
        print(f"\n⚠ No new cards added (skipped={skipped}, failed={failed})")

    if rebuild_index or new_cards:
        _rebuild_vector_index()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF Ingest — XKB knowledge pipeline")
    parser.add_argument("path", help="PDF file or folder of PDFs")
    parser.add_argument("--category", default="research", help="Category tag (default: research)")
    parser.add_argument("--tag", action="append", dest="tags", default=[], help="Additional tags (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--force", action="store_true", help="Re-ingest already processed files")
    parser.add_argument("--rebuild-index", action="store_true", help="Force rebuild vector index after ingest")
    args = parser.parse_args()

    return ingest(
        path=Path(args.path),
        category=args.category,
        tags=args.tags,
        dry_run=args.dry_run,
        rebuild_index=args.rebuild_index,
        force=args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())
