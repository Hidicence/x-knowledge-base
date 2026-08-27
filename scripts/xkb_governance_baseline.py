#!/usr/bin/env python3
"""Produce a deterministic, secret-free XKB governance baseline report.

Read-only: never edits staging, wiki, cards, or indexes.
"""
from __future__ import annotations
import argparse, json, re
from collections import Counter
from pathlib import Path

STATUS = re.compile(r"\*\*Status:\*\*.*?\[x\]\s+(approve|skip)", re.I)
CANDIDATE = re.compile(r"(?m)^## Candidate (\d+)\s*$")
FIELD = lambda name: re.compile(rf"\*\*{re.escape(name)}:\*\*\s*(.+)")

def scan(staging: Path) -> dict:
    files = 0; rows = []
    for path in sorted(staging.glob("*.md")):
        content = path.read_text(encoding="utf-8", errors="replace")
        parts = CANDIDATE.split(content)
        if len(parts) < 3: continue
        files += 1
        for i in range(1, len(parts), 2):
            number, block = parts[i], parts[i + 1]
            status_match = STATUS.search(block)
            status = {"approve": "approved", "skip": "skipped"}.get(status_match.group(1).lower(), "pending") if status_match else "pending"
            def field(name, default="unknown"):
                match = FIELD(name).search(block)
                return match.group(1).strip() if match else default
            rows.append({"file": str(path), "candidate_number": int(number), "status": status,
                         "topic": field("Topic", ""), "confidence": field("Confidence"),
                         "source_date": field("Source date")})
    counts = Counter(row["status"] for row in rows)
    return {"schema": "xkb-governance-baseline.v1", "staging_dir": str(staging),
            "staging_files": files, "candidates": len(rows), "pending": counts["pending"],
            "approved": counts["approved"], "skipped": counts["skipped"],
            "confidence": dict(sorted(Counter(r["confidence"] for r in rows).items())),
            "topics": dict(sorted(Counter(r["topic"] for r in rows).items())),
            "source_dates": sorted(set(r["source_date"] for r in rows)),
            "status_definition": "Status is parsed from existing Markdown; source files are not modified."}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    result = scan(args.staging)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
