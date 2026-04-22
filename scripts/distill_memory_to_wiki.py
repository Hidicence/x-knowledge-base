#!/usr/bin/env python3
"""
Distill conversation memory into wiki topic pages.

Reads recent memory/YYYY-MM-DD.md files, extracts insights worth
long-term preservation (decisions, workflows, principles), and either
stages them for review or applies approved entries to wiki topics.

Usage:
  python3 distill_memory_to_wiki.py --dry-run [--days 3]
  python3 distill_memory_to_wiki.py --stage   [--days 2]
  python3 distill_memory_to_wiki.py --apply   --staging-file wiki/_staging/2026-04-06-candidates.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
_SKILL_DIR = Path(__file__).resolve().parent.parent
WIKI_DIR = Path(os.getenv("XKB_WIKI_DIR", str(_SKILL_DIR / "wiki")))

# ── Unified LLM helper ────────────────────────────────────────────────────────
sys.path.insert(0, str(_SKILL_DIR / "scripts"))
from _llm import call as _llm_backend
TOPICS_DIR = WIKI_DIR / "topics"
STAGING_DIR = WIKI_DIR / "_staging"
INDEX_PATH = WIKI_DIR / "index.md"
LOG_PATH = WIKI_DIR / "log.md"
MEMORY_DIR = WORKSPACE / "memory"

def load_env_key() -> str:
    return ""  # auth handled by _llm.py via openclaw CLI


def llm_call(system: str, user: str, api_key: str = "") -> str:
    raw = _llm_backend(system, user)
    return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()


def load_topic_slugs() -> list[str]:
    return [p.stem for p in TOPICS_DIR.glob("*.md")] if TOPICS_DIR.exists() else []


def append_log(entry: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = LOG_PATH.read_text() if LOG_PATH.exists() else "# Wiki Log\n---\n"
    LOG_PATH.write_text(existing.rstrip() + "\n" + entry + "\n")


def load_recent_memory(days: int) -> list[tuple[str, str]]:
    """Return [(date_str, content)] for the last N days of memory files."""
    entries = []
    today = datetime.now(timezone.utc).date()
    for i in range(days):
        d = today - timedelta(days=i)
        fname = MEMORY_DIR / f"{d}.md"
        if fname.exists():
            entries.append((str(d), fname.read_text()))
    return entries


EXTRACT_SYSTEM = """You are a wiki knowledge curator for an AI agent system.
Extract insights worth long-term preservation from conversation memory logs.

Inclusion criteria (ALL must be met):
1. Contains a technical decision with reasoning, reusable workflow/command/rule, or confirmed principle/conclusion
2. Adds real new dimension to existing knowledge (not just paraphrasing)
3. Useful 6 months from now (exclude time-sensitive news, cron logs, daily chores)

Exclusion list:
- Daily activity logs
- Cron / heartbeat execution records
- Unresolved discussions
- Emotional reactions or temporary statements
- Anything only meaningful today

Important output contract:
- Return ONLY a JSON object with one top-level key: \"insights\"
- Do NOT write prose summary, markdown, explanation, or any extra fields
- Each insight must be concrete and non-empty; do not emit placeholder items
- If nothing qualifies, return exactly {\"insights\": []}

Output ONLY valid JSON, no markdown fences, no explanation."""

EXTRACT_USER_TMPL = """Memory file date: {date}

Content:
{content}

Existing wiki topics:
{topics}

Task:
- Extract only wiki-worthy insights that satisfy the system criteria.
- Do NOT write a general summary of the chunk.
- Do NOT optimize for brevity over structure; fill the JSON fields directly.

Extract wiki-worthy insights as JSON:
{{
  "insights": [
    {{
      "topic_slug": "most relevant existing topic slug (from list above), or null if none fits",
      "topic_suggestion": "if topic_slug is null, suggest new topic name in kebab-case",
      "section": "target section: 核心概念 / 做法-Workflow / 案例 / 矛盾與未解問題",
      "content": "synthesized insight (1-3 sentences, not a log entry)",
      "confidence": "high / medium / low",
      "source_date": "{date}"
    }}
  ]
}}

If nothing qualifies, output: {{"insights": []}}"""


def load_todays_staged_content(date_str: str) -> str:
    """Return already-staged insight summaries for today (for dedup context)."""
    if not STAGING_DIR.exists():
        return ""
    parts = []
    for f in sorted(STAGING_DIR.glob(f"{date_str}-*-candidates.md")):
        # Extract just the content lines from each candidate block
        text = f.read_text(encoding="utf-8")
        # Grab lines after "Status:" lines (the actual insight text)
        in_content = False
        for line in text.splitlines():
            if line.startswith("- **Status:**"):
                in_content = True
                continue
            if in_content:
                if line.startswith("- **") or line.startswith("---") or line.startswith("##"):
                    in_content = False
                elif line.strip():
                    parts.append(line.strip())
    return "\n".join(parts)


LOW_SIGNAL_PATTERNS = [
    re.compile(r"^Conversation info \(untrusted metadata\):"),
    re.compile(r"^Sender \(untrusted metadata\):"),
    re.compile(r"^System \(untrusted\):"),
    re.compile(r"^An async command you ran earlier has completed"),
    re.compile(r"^Current time:"),
    re.compile(r"^User:\s*\[SYSTEM\]"),
    re.compile(r"^User:\s*Read HEARTBEAT\.md"),
    re.compile(r"^User:\s*\[cron:"),
    re.compile(r"^Assistant:\s*HEARTBEAT_OK\s*$"),
    re.compile(r"^Assistant:\s*NO_REPLY\s*$"),
    re.compile(r"^Assistant:\s*OK\s*$"),
    re.compile(r"^Assistant:\s*\{\s*\"insights\""),
    re.compile(r"^Reflections:\s*Theme:"),
    re.compile(r"^Possible Lasting Truths:"),
    re.compile(r"^## Apply Status"),
    re.compile(r"^## Review Instructions"),
    re.compile(r"^- Mark each item:"),
    re.compile(r"^- Run:\s*`?python3 distill_memory_to_wiki\.py"),
    re.compile(r"^Generated:\s*\d{4}-\d{2}-\d{2}"),
    re.compile(r"^Insights found:\s*\d+"),
    re.compile(r"^> ✅ Applied \d+ candidate"),
    re.compile(r"^User:\s*\{\s*$"),
]

HIGH_SIGNAL_KEYWORDS = [
    "postgres", "pgvector", "pglite", "minions", "gbrain", "migration",
    "upgrade", "recall", "playbook", "roadmap", "architecture", "root cause",
    "permanent fix", "init", "queue", "worker", "xkb", "wiki", "distill",
]

TECHNICAL_SIGNAL_KEYWORDS = [
    "postgres", "pgvector", "pglite", "minions", "gbrain", "migration", "upgrade",
    "merge upstream", "recall", "search_mode", "playbook", "roadmap", "architecture",
    "root cause", "fix", "worker", "queue", "daemon", "timeout", "retry", "backoff",
    "pm2", "commit", "branch", "wiki", "graph", "structured knowledge", "xkb",
]

OPERATIONAL_NOISE_PATTERNS = [
    re.compile(r"x 推薦 top\s*5", re.IGNORECASE),
    re.compile(r"索引更新完成"),
    re.compile(r"同步\s*\d+\s*個 artifacts"),
    re.compile(r"hn digest sent", re.IGNORECASE),
    re.compile(r"self_review_sent", re.IGNORECASE),
    re.compile(r"重要規則："),
]


def is_low_signal_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped in {"```", "json", "NO_REPLY", "HEARTBEAT_OK", "OK"}:
        return True
    if stripped.startswith("{") and stripped.endswith("}") and len(stripped) < 400:
        return True
    if stripped.startswith("[") and stripped.endswith("]") and len(stripped) < 400:
        return True
    if stripped.startswith("```json") or stripped.startswith("```"):
        return True
    if any(p.search(stripped) for p in LOW_SIGNAL_PATTERNS):
        return True
    return False



def has_technical_signal(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in TECHNICAL_SIGNAL_KEYWORDS)



def extract_conversation_content(content: str) -> str:
    """
    Strip dreaming metadata and low-value operational noise from memory files,
    keeping only durable conversation content likely to contain reusable decisions.
    """
    lines = content.splitlines()
    result = []
    skip_next_metadata = False
    skip_multiline_noise = False

    for line in lines:
        stripped = line.strip()

        if stripped in {"### Reflections", "### Possible Lasting Truths"}:
            skip_multiline_noise = True
            continue
        if skip_multiline_noise:
            if stripped.startswith("<!-- openclaw:dreaming") or stripped.startswith("## "):
                skip_multiline_noise = False
            else:
                continue

        # Skip dreaming metadata lines that follow a Candidate entry
        if skip_next_metadata:
            if stripped.startswith("- confidence:") or \
               stripped.startswith("- evidence:") or \
               stripped.startswith("- recalls:") or \
               stripped.startswith("- status:") or \
               stripped.startswith("- note:"):
                continue
            else:
                skip_next_metadata = False

        # Keep Candidate lines but strip the "- Candidate: " prefix
        if stripped.startswith("- Candidate:"):
            text = stripped[len("- Candidate:"):].strip()
            if text and not is_low_signal_line(text):
                normalized = re.sub(r"^(User|Assistant):\s*", "", text)
                if has_technical_signal(normalized) and not any(p.search(normalized) for p in OPERATIONAL_NOISE_PATTERNS):
                    result.append(normalized)
            skip_next_metadata = True
            continue

        # Skip dreaming block markers and low-value headers
        if stripped.startswith("<!-- openclaw:dreaming") or \
           stripped in ("## Light Sleep", "## REM Sleep", "### Reflections", "### Possible Lasting Truths"):
            continue

        if is_low_signal_line(stripped):
            continue

        normalized = re.sub(r"^(User|Assistant):\s*", "", stripped)
        if stripped.startswith(("User:", "Assistant:")):
            if not has_technical_signal(normalized):
                continue
            if any(p.search(normalized) for p in OPERATIONAL_NOISE_PATTERNS):
                continue
            result.append(normalized)
            continue

        if any(p.search(stripped) for p in OPERATIONAL_NOISE_PATTERNS):
            continue

        # Keep everything else (plain text sections, non-dreaming headers)
        result.append(line)

    cleaned = "\n".join(result)
    cleaned = re.sub(r"Assistant:\s*\{\s*\"insights\".*", "", cleaned)
    cleaned = re.sub(r"Conversation info \(untrusted metadata\):.*", "", cleaned)
    cleaned = re.sub(r"Sender \(untrusted metadata\):.*", "", cleaned)
    cleaned = re.sub(r"System \(untrusted\):.*", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


CHUNK_SIZE = 4000  # chars per LLM call


def score_insight(ins: dict) -> int:
    score = 0
    confidence = (ins.get("confidence") or "").lower()
    section = (ins.get("section") or "").lower()
    text = f"{ins.get('topic_slug','')} {ins.get('topic_suggestion','')} {ins.get('content','')}".lower()

    if confidence == "high":
        score += 4
    elif confidence == "medium":
        score += 2

    if "workflow" in section:
        score += 2
    if "核心概念" in ins.get("section", ""):
        score += 1

    keyword_hits = sum(1 for kw in HIGH_SIGNAL_KEYWORDS if kw in text)
    score += min(keyword_hits, 6)

    if any(word in text for word in ["decision", "root cause", "migration", "upgrade", "fix", "playbook"]):
        score += 3

    if len(ins.get("content", "")) >= 80:
        score += 1

    return score



def rerank_and_consolidate_insights(insights: list[dict], max_per_day: int = 8) -> list[dict]:
    if not insights:
        return []

    grouped: dict[str, list[dict]] = {}
    for ins in insights:
        date = ins.get("source_date") or "unknown"
        grouped.setdefault(date, []).append(ins)

    final: list[dict] = []
    for date, group in grouped.items():
        seen_content = set()
        slug_counter = Counter((ins.get("topic_slug") or ins.get("topic_suggestion") or "unknown") for ins in group)
        ranked = sorted(
            group,
            key=lambda ins: (score_insight(ins), slug_counter[ins.get("topic_slug") or ins.get("topic_suggestion") or "unknown"], len(ins.get("content", ""))),
            reverse=True,
        )
        kept = 0
        for ins in ranked:
            content = re.sub(r"\s+", " ", ins.get("content", "")).strip()
            key = content[:160].lower()
            if not key or key in seen_content:
                continue
            seen_content.add(key)
            final.append(ins)
            kept += 1
            if kept >= max_per_day:
                break

    return final



def extract_insights(entries: list[tuple[str, str]], api_key: str, verbose: bool = False,
                     already_staged: str = "") -> list[dict]:
    existing_topics = load_topic_slugs()
    topics_str = "\n".join(f"- {s}" for s in existing_topics) if existing_topics else "(no topics yet)"

    all_insights = []
    for date, content in entries:
        cleaned = extract_conversation_content(content)
        print(f"[distill] source={date} raw_chars={len(content)} cleaned_chars={len(cleaned)}")

        # Chunk into CHUNK_SIZE segments so the full file is always processed
        chunks = [cleaned[i:i + CHUNK_SIZE] for i in range(0, max(len(cleaned), 1), CHUNK_SIZE)]
        print(f"[distill] source={date} chunk_count={len(chunks)}")
        seen_contents: set[str] = set()

        for chunk_i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            print(f"[distill] source={date} chunk={chunk_i + 1}/{len(chunks)} chunk_chars={len(chunk)} llm_start")
            user_prompt = EXTRACT_USER_TMPL.format(date=date, content=chunk, topics=topics_str)
            if already_staged:
                user_prompt += f"\n\nAlready staged today (SKIP these, do not re-surface):\n{already_staged[:800]}"
            try:
                raw = llm_call(EXTRACT_SYSTEM, user_prompt, api_key)
                print(f"[distill] source={date} chunk={chunk_i + 1}/{len(chunks)} llm_done raw_chars={len(raw)}")
                raw = re.sub(r"^```(?:json)?\s*", "", raw).rstrip("` \n")
                parsed = json.loads(raw)
                insights = parsed.get("insights", [])
                # Dedup by content to avoid the same insight surfacing across chunks
                for ins in insights:
                    key = ins.get("content", "")[:120]
                    if key and key not in seen_contents:
                        seen_contents.add(key)
                        all_insights.append(ins)
                print(f"[distill] source={date} chunk={chunk_i + 1}/{len(chunks)} insights={len(insights)} accumulated={len(all_insights)}")
            except Exception as e:
                print(f"[distill][WARN] source={date} chunk={chunk_i + 1}/{len(chunks)} error={e}")

    reranked = rerank_and_consolidate_insights(all_insights)
    print(f"[distill] rerank input={len(all_insights)} output={len(reranked)}")
    return reranked


def write_staging(insights: list[dict], date_str: str, label: str = "") -> Path:
    """Write staging file. label (e.g. 'morning', 'evening') prevents same-day overwrites."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"-{label}" if label else ""
    out_path = STAGING_DIR / f"{date_str}{suffix}-candidates.md"

    lines = [
        f"# Memory Distillation Candidates — {date_str}",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Insights found: {len(insights)}",
        "",
        "---",
        "",
        "## Review Instructions",
        "- Mark each item: `[x] approve` or leave `[ ] skip`",
        "- Run: `python3 distill_memory_to_wiki.py --apply --staging-file <this file>`",
        "",
    ]

    for i, ins in enumerate(insights, 1):
        slug = ins.get("topic_slug") or f"[NEW: {ins.get('topic_suggestion', '?')}]"
        lines += [
            f"## Candidate {i}",
            f"- **Topic:** {slug}",
            f"- **Section:** {ins.get('section', '核心概念')}",
            f"- **Confidence:** {ins.get('confidence', 'medium')}",
            f"- **Source date:** {ins.get('source_date', '')}",
            f"- **Status:** [ ] approve  [ ] skip",
            "",
            ins.get("content", ""),
            "",
            "---",
            "",
        ]

    out_path.write_text("\n".join(lines))
    return out_path


def apply_staging_file(
    staging_path: Path,
    approve_all: bool = False,
    auto_approve_high: bool = False,
) -> tuple[int, int, list[str]]:
    """
    Apply approved candidates from staging file to wiki topics.
    Returns (applied, skipped, updated_slugs).
    - approve_all: treat every candidate as approved regardless of status/confidence
    - auto_approve_high: automatically approve candidates with confidence=high;
      others still require manual [x] approve in the staging file
    """
    content = staging_path.read_text()
    blocks = re.split(r"\n## Candidate \d+\n", content)[1:]
    applied = 0
    skipped = 0
    updated_slugs: list[str] = []

    for block in blocks:
        topic_m = re.search(r"\*\*Topic:\*\* (.+)", block)
        section_m = re.search(r"\*\*Section:\*\* (.+)", block)
        status_m = re.search(r"\*\*Status:\*\* \[x\] approve", block, re.IGNORECASE)
        source_m = re.search(r"\*\*Source date:\*\* (.+)", block)
        conf_m = re.search(r"\*\*Confidence:\*\* (\w+)", block)
        confidence = conf_m.group(1).strip() if conf_m else "medium"

        is_auto_approved = auto_approve_high and confidence == "high"
        if not status_m and not approve_all and not is_auto_approved:
            skipped += 1
            continue

        slug = (topic_m.group(1).strip() if topic_m else "").strip()
        section = section_m.group(1).strip() if section_m else "核心概念"
        source_date = source_m.group(1).strip() if source_m else "unknown"

        content_lines = []
        in_content = False
        for line in block.splitlines():
            if line.startswith("- **") or line.startswith("---"):
                in_content = False
            if in_content and line.strip():
                content_lines.append(line)
            if line.startswith("- **Status:**"):
                in_content = True

        entry_text = "\n".join(content_lines).strip()
        if not entry_text or not slug or slug.startswith("[NEW:"):
            print(f"  [SKIP] Missing slug or content")
            skipped += 1
            continue

        upsert_wiki_section(slug, section, entry_text, source_date)
        applied += 1
        if slug not in updated_slugs:
            updated_slugs.append(slug)

    # Mark staging file as applied
    if applied > 0:
        applied_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header_addition = f"\n> ✅ Applied {applied} candidate(s) at {applied_at}\n"
        staging_path.write_text(content.replace(
            "## Review Instructions",
            f"## Apply Status{header_addition}\n## Review Instructions",
            1,
        ))

    return applied, skipped, updated_slugs


def upsert_wiki_section(slug: str, section: str, entry: str, source_date: str) -> None:
    topic_path = TOPICS_DIR / f"{slug}.md"
    if not topic_path.exists():
        print(f"  [SKIP] Topic {slug} does not exist")
        return

    content = topic_path.read_text(encoding="utf-8", errors="replace")
    section_header = f"## {section}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if section_header in content:
        content = content.replace(
            section_header,
            f"{section_header}\n\n- {entry} *(memory/{source_date}.md)*",
            1,
        )
    else:
        content = content.rstrip() + f"\n\n{section_header}\n\n- {entry} *(memory/{source_date}.md)*\n"

    content = re.sub(r"(last_updated:\s*)\S+", f"\\g<1>{today}", content)
    topic_path.write_text(content)
    print(f"  [OK] {slug} / {section}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Distill memory into wiki")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--stage", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--label", default="",
                        help="Staging filename suffix to avoid overwriting (e.g. 'morning', 'evening')")
    parser.add_argument("--input", metavar="TEXT",
                        help="Distill inline text instead of daily memory files")
    parser.add_argument("--input-file", metavar="PATH",
                        help="Distill content from a file instead of daily memory files")
    parser.add_argument("--staging-file")
    parser.add_argument("--approve-all", action="store_true",
                        help="Approve all candidates without checking [x] marks")
    parser.add_argument("--auto-apply-high", action="store_true",
                        help="After --stage, automatically apply high-confidence candidates to wiki "
                             "without manual review. Low/medium candidates remain in staging.")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.apply:
        if not args.staging_file:
            parser.error("--apply requires --staging-file")
        staging_path = Path(args.staging_file)
        applied, skipped, slugs = apply_staging_file(
            staging_path,
            approve_all=args.approve_all,
            auto_approve_high=getattr(args, 'auto_approve_high', False),
        )
        print(f"\nApply results:")
        print(f"  Applied : {applied}")
        print(f"  Skipped : {skipped}")
        if slugs:
            print(f"  Topics updated: {', '.join(slugs)}")
        if applied > 0:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            append_log(
                f"\n## [{now}] apply-staging | {applied} candidate(s) applied"
                f" from {staging_path.name} → {', '.join(slugs)}"
            )
            print(f"  Log updated.")
        return

    # Determine input source: --input TEXT, --input-file PATH, or daily memory files
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.input:
        entries = [(today_str, args.input)]
        print(f"Input mode: inline text ({len(args.input)} chars)")
    elif args.input_file:
        input_path = Path(args.input_file)
        if not input_path.exists():
            print(f"[ERROR] --input-file not found: {input_path}")
            return
        content = input_path.read_text(encoding="utf-8")
        entries = [(today_str, content)]
        print(f"Input mode: file {input_path.name} ({len(content)} chars)")
    else:
        entries = load_recent_memory(args.days)
        if not entries:
            print(f"No memory files found for last {args.days} days.")
            return
        print(f"Loaded {len(entries)} file(s): {[e[0] for e in entries]}")

    if args.no_llm:
        for date, content in entries:
            cleaned = extract_conversation_content(content)
            print(f"\n=== {date} raw={len(content)} chars cleaned={len(cleaned)} chars ===")
            print(cleaned[:1200] + "..." if len(cleaned) > 1200 else cleaned)
        return

    api_key = ""  # auth handled by _llm.py via openclaw CLI

    # Load today's already-staged content for dedup (only for memory-based runs, not --input)
    already_staged = ""
    if not args.input and not args.input_file:
        already_staged = load_todays_staged_content(today_str)
        if already_staged:
            print(f"Dedup context: found existing staging for {today_str}, will skip duplicates")

    insights = extract_insights(entries, api_key, verbose=args.verbose, already_staged=already_staged)
    high = [i for i in insights if i.get("confidence") == "high"]
    print(f"\nInsights found: {len(insights)} ({len(high)} high-confidence)")

    if args.dry_run:
        for ins in insights:
            slug = ins.get("topic_slug") or f"[NEW: {ins.get('topic_suggestion')}]"
            print(f"\n  [{ins.get('confidence','?')}] {slug} / {ins.get('section','?')}")
            print(f"  {ins.get('content','')}")
        return

    if args.stage:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Auto-label inline/file input so it doesn't collide with memory-based staging files
        label = args.label
        if not label and (args.input or args.input_file):
            label = "input"
        out = write_staging(insights, today_str, label=label)
        print(f"\nStaged to: {out}")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        source_desc = "inline input" if (args.input or args.input_file) else f"last {args.days}d"
        append_log(f"\n## [{now}] ingest-memory | staged {len(insights)} candidates from {source_desc} → {out.name}")

        # Auto-apply high-confidence candidates immediately after staging
        if getattr(args, 'auto_apply_high', False):
            high_count = sum(1 for ins in insights if ins.get("confidence") == "high")
            print(f"\nAuto-applying {high_count} high-confidence candidate(s)...")
            applied, skipped, slugs = apply_staging_file(out, auto_approve_high=True)
            print(f"  Applied : {applied}")
            print(f"  Skipped : {skipped} (medium/low — review manually)")
            if slugs:
                print(f"  Topics updated: {', '.join(slugs)}")
            if applied > 0:
                append_log(
                    f"\n## [{now}] auto-apply-high | {applied} high-confidence candidate(s) "
                    f"applied from {out.name} → {', '.join(slugs)}"
                )
        else:
            print(f"Run: python3 distill_memory_to_wiki.py --apply --staging-file {out}")


if __name__ == "__main__":
    main()
