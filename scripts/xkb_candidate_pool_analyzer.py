#!/usr/bin/env python3
"""Read-only replay analyzer for XKB memory candidates.

Scans daily memory, dreaming, and wiki staging files and writes a report only.
It never modifies source files, MEMORY.md, cards, or wiki topics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Reuse XKB's existing memory-cleaning logic rather than reimplementing it.
_SKILL_SCRIPTS = Path(__file__).resolve().parent
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))
try:
    from distill_memory_to_wiki import extract_conversation_content
except ImportError:  # keeps the report tool usable in minimal copies
    extract_conversation_content = None

DEFAULT_ROOT = Path(__file__).resolve().parents[3]
SYSTEM_TERMS = {
    "cron jobs executed", "cron_jobs_executed", "hn_digest_sent", "self_review_sent",
    "image2_skill_autogrow_failed", "heartbeat", "delivery", "system echo",
}
SYSTEM_MARKERS = (
    "cron:", "heartbeat", "self_review_sent", "hn_digest_sent", "system (", "delivery",
    "assistant turn failed before producing content", "assistant: no_reply", "assistant: heartbeat_ok",
    "write a dream diary entry", "do not run the command again", "async command did not run",
)
ASSISTANT_OUTPUT_MARKERS = (
    "assistant: no_reply", "assistant: heartbeat_ok", "assistant turn failed",
    "assistant: {", "assistant: [", "assistant: ⏰", "assistant: 提醒", "assistant: pan，",
)
PIPELINE_PROMPT_MARKERS = (
    "write a dream diary entry", "an async command did not run",
    "[system] you are a wiki knowledge curator", "[system] you are a knowledge card generator",
)


def files_for(root: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in ("memory/*.md", "memory/dreaming/**/*.md", "memory/x-knowledge-base/wiki/_staging/*.md"):
        paths.extend(root.glob(pattern))
    return sorted({p for p in paths if p.is_file()})


def source_kind(path: Path) -> str:
    s = str(path)
    if "/_staging/" in s:
        return "wiki_staging"
    if "/dreaming/" in s:
        return "dreaming"
    return "daily_memory"


def candidate_blocks(text: str, path: Path) -> list[str]:
    """Reuse existing staging format and dreaming candidate extraction."""
    if path.parent.name == "_staging":
        matches = list(re.finditer(r"(?im)^##\s+Candidate\s+\d+\s*$", text))
        blocks = []
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[match.start():end].strip()
            if len(block) >= 20:
                blocks.append(block)
        return blocks
    cleaned = extract_conversation_content(text) if extract_conversation_content else text
    # Minimal/replay fixtures may not match the full conversation envelope used
    # by the production cleaner. Preserve the raw candidate lines in that case.
    if not re.search(r"(?i)^(?:[-*]\s*)?(?:candidate|topic|possible lasting truth)\s*:", cleaned, re.M):
        cleaned = text
    blocks: list[str] = []
    current: list[str] = []
    for raw_line in cleaned.splitlines():
        stripped = raw_line.strip()
        is_candidate = re.match(
            r"(?i)^(?:[-*]\s*)?(?:candidate|topic|possible lasting truth)\s*:",
            stripped,
        )
        if is_candidate:
            if current and len(current[0]) >= 20:
                blocks.append("\n".join(current))
            current = [stripped]
        elif current and re.match(r"(?i)^\s+(?:episode|session)[ _-]?id\s*[:=]", raw_line):
            current.append(raw_line)
    if current and len(current[0]) >= 20:
        blocks.append("\n".join(current))
    return blocks


def extract_candidate(block: str, path: Path) -> str:
    # For staging records, group by the actual insight, not only its topic.
    if path.parent.name == "_staging":
        lines = []
        for line in block.splitlines():
            stripped = line.strip()
            if (not stripped or stripped.startswith("## Candidate") or
                    re.match(r"^- \*\*(?:Topic|Section|Confidence|Source date|Status):", stripped) or
                    stripped == "---"):
                continue
            lines.append(stripped)
        if lines:
            return " ".join(lines)[:240]
    patterns = [
        r"(?im)^[-*]\s*\*?\*?Candidate\s*:\s*(.+)$",
        r"(?im)^[-*]\s*\*?\*?Topic\s*:\s*(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, block)
        if m:
            value = m.group(1).strip().strip("[]")
            if value:
                value = re.sub(
                    r"\s+(?:episode|session)[ _-]?id\s*[:=]\s*[`\"]?[A-Za-z0-9._:-]+[`\"]?\s*$",
                    "",
                    value,
                    flags=re.I,
                )
                return value[:240]
    return path.stem


def evidence_type(block: str, path: Path) -> str:
    low = block.lower()
    # Pipeline instructions are non-eligible but distinct from system echo.
    # Check these before broad markers such as "delivery" or "heartbeat".
    if any(marker in low for marker in PIPELINE_PROMPT_MARKERS):
        return "pipeline_instruction"
    if any(marker in low for marker in SYSTEM_MARKERS) or any(term in low for term in SYSTEM_TERMS):
        return "system"
    if any(marker in low for marker in ASSISTANT_OUTPUT_MARKERS):
        return "assistant_output"
    if source_kind(path) == "wiki_staging" and re.search(r"(?im)^[-*]\s*\*\*Source date", block):
        return "derived_staging"
    if source_kind(path) == "dreaming":
        return "derived_dreaming"
    return "conversation_or_note"


def extract_episode_id(block: str) -> str | None:
    """Return an explicit episode/session id; never invent one from a filename.

    Memmy's induction gate counts distinct trace episodeId values. A daily or
    Dreaming file is only a container, not an episode, so filename-based
    episode counting is deliberately not used here.
    """
    patterns = (
        r"(?i)\bepisode[_ -]?id\s*[:=]\s*[`\"]?([A-Za-z0-9._:-]+)",
        r"(?i)\bsession[_ -]?id\s*[:=]\s*[`\"]?([A-Za-z0-9._:-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, block)
        if match:
            return match.group(1).rstrip("`\".,)")
    return None


def is_induction_eligible(kind: str) -> bool:
    return kind in {"conversation_or_note"}


def normalize_key(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "_", value, flags=re.UNICODE)
    return value.strip("_")[:160] or "unknown"


def analyze(root: Path) -> dict:
    groups: dict[str, dict] = {}
    scanned = []
    for path in files_for(root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        scanned.append({"path": str(path.relative_to(root)), "sha256": digest, "bytes": len(text.encode("utf-8"))})
        for block in candidate_blocks(text, path):
            candidate = extract_candidate(block, path)
            key = normalize_key(candidate)
            kind = evidence_type(block, path)
            group = groups.setdefault(key, {
                "candidate_key": key,
                "candidate_label": candidate,
                "evidence": [],
                "source_types": set(),
                "system_evidence": 0,
                "direct_or_note_evidence": 0,
                "episode_keys": set(),
                "eligible_episode_keys": set(),
                "provenance_types": set(),
            })
            rel = str(path.relative_to(root))
            # Do not use a source filename as a Memmy episode. Only an explicit
            # episode/session id can satisfy the distinct-episode gate.
            episode = extract_episode_id(block)
            group["source_types"].add(source_kind(path))
            group["provenance_types"].add(kind)
            if kind == "system":
                group["system_evidence"] += 1
            else:
                group["direct_or_note_evidence"] += 1
            if is_induction_eligible(kind) and episode:
                group["eligible_episode_keys"].add(episode)
            excerpt = re.sub(r"\s+", " ", block)[:360]
            evidence_key = (rel, kind, excerpt)
            if not any((e["path"], e["evidence_type"], e["excerpt"]) == evidence_key for e in group["evidence"]):
                group["evidence"].append({
                    "path": rel,
                    "sha256": digest,
                    "evidence_type": kind,
                    "episode_id": episode,
                    "induction_eligible": is_induction_eligible(kind) and bool(episode),
                    "excerpt": excerpt,
                })

    results = []
    for g in groups.values():
        episodes = len(g["eligible_episode_keys"])
        total = len(g["evidence"])
        eligible_evidence = sum(1 for e in g["evidence"] if e["induction_eligible"])
        provenance_types = sorted(g["provenance_types"])
        confidence = round(min(0.99, 0.35 + 0.18 * min(episodes, 4) + 0.06 * min(eligible_evidence, 5)), 2)
        if all(kind in {"system", "assistant_output", "pipeline_instruction", "derived_dreaming", "derived_staging"} for kind in provenance_types):
            decision = "REJECT_SYSTEM_ECHO"
            reason = "no direct conversation evidence eligible for induction"
        elif episodes >= 2 and eligible_evidence >= 2 and confidence >= 0.8:
            decision = "REVIEW_READY"
            reason = "explicit distinct episode IDs meet the Memmy-style evidence gate; human review still required"
        else:
            decision = "HOLD"
            reason = "missing explicit distinct episode IDs or insufficient eligible evidence"
        results.append({
            "candidate_key": g["candidate_key"],
            "candidate_label": g["candidate_label"],
            "episode_count": episodes,
            "evidence_count": total,
            "eligible_evidence_count": eligible_evidence,
            "eligible_episode_count": episodes,
            "direct_or_note_evidence": g["direct_or_note_evidence"],
            "system_evidence": g["system_evidence"],
            "source_types": sorted(g["source_types"]),
            "provenance_types": provenance_types,
            "confidence": confidence,
            "decision": decision,
            "reason": reason,
            "evidence": g["evidence"][:20],
        })
    results.sort(key=lambda x: (-x["evidence_count"], x["candidate_key"]))
    return {
        "schema": "xkb-candidate-pool-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "read_only": True,
        "source_file_count": len(scanned),
        "candidate_count": len(results),
        "summary": {d: sum(1 for r in results if r["decision"] == d) for d in ("REVIEW_READY", "HOLD", "REJECT_SYSTEM_ECHO")},
        "sources": scanned,
        "candidates": results,
    }


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# XKB Candidate Pool Replay Report",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Source files: `{report['source_file_count']}`",
        f"- Candidates: `{report['candidate_count']}`",
        f"- Read-only: `{report['read_only']}`",
        "",
        "## Summary",
    ]
    for key, value in report["summary"].items():
        lines.append(f"- {key}: **{value}**")
    lines += ["", "## Candidates"]
    for item in report["candidates"]:
        lines += [
            "",
            f"### `{item['candidate_label']}`",
            f"- Key: `{item['candidate_key']}`",
            f"- Decision: **{item['decision']}** — {item['reason']}",
            f"- Eligible episodes: `{item['eligible_episode_count']}`; evidence: `{item['evidence_count']}`; eligible evidence: `{item['eligible_evidence_count']}`; confidence: `{item['confidence']}`",
            f"- Direct/note evidence: `{item['direct_or_note_evidence']}`; system evidence: `{item['system_evidence']}`",
        ]
        for ev in item["evidence"][:5]:
            lines.append(f"- Evidence: `{ev['path']}` ({ev['evidence_type']}, episode `{ev['episode_id']}`, eligible `{ev['induction_eligible']}`, sha256 `{ev['sha256'][:16]}…`) — {ev['excerpt']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only XKB candidate pool replay analyzer")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()
    root = args.root.resolve()
    out = (args.out_dir or root / "memory" / "x-knowledge-base" / "reports" / "candidate-pool").resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = analyze(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"candidate-pool-{stamp}.json"
    md_path = out / f"candidate-pool-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(report, md_path)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "summary": report["summary"], "source_files": report["source_file_count"], "candidates": report["candidate_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
