#!/usr/bin/env python3
"""
PubMed / PMC Fetcher — XKB knowledge pipeline

從 PubMed Central 搜尋並下載開放取用論文全文（XML API）。
只抓 Open Access 論文，使用 NCBI 官方 API，不需要訂閱。

Usage:
  python3 fetch_pubmed.py "CRISPR gene editing" --limit 20
  python3 fetch_pubmed.py "AI drug discovery" --limit 30 --out /tmp/papers
  python3 fetch_pubmed.py "cancer immunotherapy" --limit 10 --dry-run
  python3 fetch_pubmed.py "alzheimer treatment" --since 2022 --limit 25

After fetching, ingest into XKB:
  python3 pdf_ingest.py /tmp/papers/ --category research --rebuild-index
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

HEADERS = {"User-Agent": "XKB-Research-Fetcher/1.0 (education/research use)"}
RATE_LIMIT = 0.35  # NCBI allows ~3 req/sec


def _get(url: str, params: dict) -> bytes:
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# ── Search ─────────────────────────────────────────────────────────────────────

def search_pmc(query: str, limit: int, since_year: int | None) -> list[str]:
    full_query = f"({query}) AND pmc open access[filter]"
    if since_year:
        full_query += f" AND {since_year}:2030[pdat]"

    print(f"🔍 Searching PMC: {query}")
    data = json.loads(_get(ESEARCH_URL, {
        "db": "pmc",
        "term": full_query,
        "retmax": min(limit * 3, 500),
        "retmode": "json",
        "sort": "relevance",
    }))
    ids = data.get("esearchresult", {}).get("idlist", [])
    total = data.get("esearchresult", {}).get("count", "?")
    print(f"  {total} open-access results found, processing up to {limit}")
    return ids


# ── Full text extraction ───────────────────────────────────────────────────────

def _xml_text(elem) -> str:
    """Recursively get all text from an XML element."""
    parts = []
    if elem.text:
        parts.append(elem.text.strip())
    for child in elem:
        parts.append(_xml_text(child))
        if child.tail:
            parts.append(child.tail.strip())
    return " ".join(p for p in parts if p)


def fetch_fulltext_xml(pmc_id: str) -> dict | None:
    """Fetch full text from PMC using official efetch XML API."""
    try:
        data = _get(EFETCH_URL, {
            "db": "pmc",
            "id": pmc_id,
            "rettype": "full",
            "retmode": "xml",
        })
        root = ET.fromstring(data)
    except Exception as e:
        return None

    # Extract title
    title = ""
    for t in root.iter("article-title"):
        title = _xml_text(t).strip()
        if title:
            break

    # Extract authors
    authors = []
    for contrib in root.iter("contrib"):
        if contrib.get("contrib-type") == "author":
            surname = next(contrib.iter("surname"), None)
            given = next(contrib.iter("given-names"), None)
            if surname is not None:
                name = _xml_text(surname)
                if given is not None:
                    name = f"{_xml_text(given)} {name}"
                authors.append(name)

    # Extract abstract
    abstract_parts = []
    for abs_elem in root.iter("abstract"):
        abstract_parts.append(_xml_text(abs_elem))
    abstract = " ".join(abstract_parts).strip()

    # Extract body sections
    body_parts = []
    for body in root.iter("body"):
        for sec in body.iter("sec"):
            sec_title_elem = sec.find("title")
            sec_title = _xml_text(sec_title_elem).strip() if sec_title_elem is not None else ""
            # Get paragraphs in this section
            paras = []
            for p in sec.iter("p"):
                text = _xml_text(p).strip()
                if len(text) > 30:
                    paras.append(text)
            if paras:
                if sec_title:
                    body_parts.append(f"\n## {sec_title}\n")
                body_parts.extend(paras)

    # If no structured body, get all paragraphs
    if not body_parts:
        for p in root.iter("p"):
            text = _xml_text(p).strip()
            if len(text) > 30:
                body_parts.append(text)

    full_text = f"# {title}\n\n"
    if authors:
        full_text += f"**Authors:** {', '.join(authors[:5])}\n\n"
    if abstract:
        full_text += f"## Abstract\n{abstract}\n\n"
    full_text += "\n".join(body_parts)

    # Clean up whitespace
    full_text = re.sub(r" {2,}", " ", full_text)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)
    full_text = full_text.strip()

    if len(full_text) < 300:
        return None  # Probably empty / not available

    return {
        "pmc_id": pmc_id,
        "title": title or f"PMC{pmc_id}",
        "authors": authors,
        "abstract": abstract,
        "full_text": full_text,
        "char_count": len(full_text),
    }


# ── Save as text file (for pdf_ingest.py to pick up) ──────────────────────────

def save_as_text(article: dict, out_dir: Path) -> Path:
    safe_title = re.sub(r'[^\w\s-]', '', article["title"])[:60].strip().replace(" ", "_")
    filename = f"PMC{article['pmc_id']}_{safe_title}.md"
    dest = out_dir / filename
    dest.write_text(article["full_text"], encoding="utf-8")
    return dest


# ── Main fetch loop ────────────────────────────────────────────────────────────

def fetch(
    query: str,
    limit: int,
    out_dir: Path,
    since_year: int | None,
    dry_run: bool,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    pmc_ids = search_pmc(query, limit, since_year)
    if not pmc_ids:
        print("No results found.")
        return 0

    time.sleep(RATE_LIMIT)

    saved = 0
    failed = 0
    skipped = 0

    for i, pmc_id in enumerate(pmc_ids):
        if saved >= limit:
            break

        # Check if already fetched
        existing = list(out_dir.glob(f"PMC{pmc_id}_*.md"))
        if existing:
            print(f"[{i+1}] PMC{pmc_id} — already fetched, skipping")
            skipped += 1
            saved += 1
            continue

        print(f"[{i+1}/{len(pmc_ids)}] PMC{pmc_id} ", end="", flush=True)

        if dry_run:
            print(f"[dry-run]")
            saved += 1
            continue

        article = fetch_fulltext_xml(pmc_id)
        time.sleep(RATE_LIMIT)

        if not article:
            print(f"✗ No full text available")
            failed += 1
            continue

        dest = save_as_text(article, out_dir)
        print(f"✓ {article['title'][:55]} ({article['char_count']:,} chars)")
        saved += 1

    print(f"\n{'='*55}")
    print(f"✅ Saved:   {saved - skipped}")
    print(f"↩  Skipped: {skipped} (already fetched)")
    print(f"✗  Failed:  {failed} (no full text in PMC)")

    if not dry_run and (saved - skipped) > 0:
        print(f"\n📂 Output: {out_dir}")
        print(f"\nNext — ingest into XKB knowledge base:")
        print(f"  python3 scripts/pdf_ingest.py {out_dir} --category research --rebuild-index")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch open-access full-text papers from PubMed Central")
    parser.add_argument("query", help="Search query (e.g. 'CRISPR cancer 2023')")
    parser.add_argument("--limit", type=int, default=20, help="Max papers to fetch (default: 20)")
    parser.add_argument("--out", default="/tmp/pubmed_papers", help="Output directory")
    parser.add_argument("--since", type=int, dest="since_year", help="Only papers from this year onwards")
    parser.add_argument("--dry-run", action="store_true", help="Preview without downloading")
    args = parser.parse_args()

    return fetch(
        query=args.query,
        limit=args.limit,
        out_dir=Path(args.out),
        since_year=args.since_year,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
