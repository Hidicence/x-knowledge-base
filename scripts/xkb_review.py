#!/usr/bin/env python3
"""
待審候選的審核入口 — 設計成給 agent 呼叫，讓審核發生在對話裡

為什麼需要這支：原本的流程是「打開 wiki/_staging/ 底下的檔案，逐條把
`[ ] approve` 改成 `[x] approve`，再對每個檔案跑一次 distill_memory_to_wiki --apply」。
2026-04-07 到 07-16 累積了 146 個檔案、470 條候選，其中被勾選過的只有 1 條。

那不是「不夠方便」，是根本不會有人做。閘門沒有壞，但它把所有東西都擋在外面，
於是對話裡談出來的結論三個半月都沒有進到知識庫。

這支把 146 個檔案攤平成一份清單，讓 agent 一次拿幾條唸給使用者聽，
使用者只要回要或不要。決策直接寫回原本的 `[x] approve` 格式——
不另外發明一套決策紀錄，既有的 apply 流程完全不用改。

Usage:
    python3 scripts/xkb_review.py --stats
    python3 scripts/xkb_review.py --list --limit 5
    python3 scripts/xkb_review.py --approve 2026-07-16-evening#1 2026-07-15#2
    python3 scripts/xkb_review.py --skip 2026-07-14-afternoon#3
    python3 scripts/xkb_review.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths

STAGING_DIR = xkb_paths.WIKI_DIR / "_staging"
CANDIDATE_RE = re.compile(r"\n## Candidate (\d+)\n")

# 每日蒸餾容易對同一件事重複產出候選。指紋只取正文前段，
# 讓「只差幾個字」的重複也會被歸為同一組。
FINGERPRINT_CHARS = 120


@dataclass
class Candidate:
    id: str
    file: str
    index: int
    topic: str
    section: str
    confidence: str
    source_date: str
    status: str          # pending | approved | skipped
    text: str
    duplicate_of: str = ""


def _field(block: str, name: str, default: str = "") -> str:
    m = re.search(rf"\*\*{name}:\*\* (.+)", block)
    return m.group(1).strip() if m else default


def _body(block: str) -> str:
    lines: list[str] = []
    collecting = False
    for line in block.splitlines():
        if line.startswith("- **") or line.startswith("---"):
            collecting = False
        if collecting and line.strip():
            lines.append(line)
        if line.startswith("- **Status:**"):
            collecting = True
    return "\n".join(lines).strip()


def _status(block: str) -> str:
    if re.search(r"\*\*Status:\*\*.*\[x\] approve", block, re.IGNORECASE):
        return "approved"
    if re.search(r"\*\*Status:\*\*.*\[x\] skip", block, re.IGNORECASE):
        return "skipped"
    return "pending"


def load_candidates() -> list[Candidate]:
    if not STAGING_DIR.exists():
        return []

    out: list[Candidate] = []
    seen: dict[str, str] = {}

    for path in sorted(STAGING_DIR.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue

        blocks = CANDIDATE_RE.split(content)[1:]
        # split 後是 [編號, 內容, 編號, 內容, ...]
        for number, block in zip(blocks[::2], blocks[1::2]):
            text = _body(block)
            if not text:
                continue
            stem = path.stem.replace("-candidates", "")
            candidate = Candidate(
                id=f"{stem}#{number}",
                file=path.name,
                index=int(number),
                topic=_field(block, "Topic"),
                section=_field(block, "Section", "核心概念"),
                confidence=_field(block, "Confidence", "medium"),
                source_date=_field(block, "Source date", "unknown"),
                status=_status(block),
                text=text,
            )
            fingerprint = hashlib.sha1(
                re.sub(r"\s+", "", text)[:FINGERPRINT_CHARS].encode("utf-8")
            ).hexdigest()
            if fingerprint in seen:
                candidate.duplicate_of = seen[fingerprint]
            else:
                seen[fingerprint] = candidate.id
            out.append(candidate)
    return out


def set_status(ids: list[str], decision: str) -> tuple[int, list[str]]:
    """把決策寫回 staging 檔的 Status 行。回傳 (成功數, 找不到的 id)。"""
    wanted = set(ids)
    changed = 0
    by_file: dict[str, list[Candidate]] = {}
    for candidate in load_candidates():
        if candidate.id in wanted:
            by_file.setdefault(candidate.file, []).append(candidate)

    found = {c.id for group in by_file.values() for c in group}
    missing = sorted(wanted - found)

    marker = "[x] approve  [ ] skip" if decision == "approve" else "[ ] approve  [x] skip"

    for filename, group in by_file.items():
        path = STAGING_DIR / filename
        content = path.read_text(encoding="utf-8")
        parts = CANDIDATE_RE.split(content)
        # parts = [前言, 編號, 內容, 編號, 內容, ...]
        targets = {c.index for c in group}
        for i in range(1, len(parts), 2):
            if int(parts[i]) not in targets:
                continue
            parts[i + 1] = re.sub(
                r"(\*\*Status:\*\*).*",
                lambda m: f"{m.group(1)} {marker}",
                parts[i + 1],
                count=1,
            )
            changed += 1
        rebuilt = parts[0] + "".join(
            f"\n## Candidate {parts[i]}\n{parts[i + 1]}" for i in range(1, len(parts), 2)
        )
        path.write_text(rebuilt, encoding="utf-8")

    return changed, missing


def apply_approved() -> int:
    """對含有已核准候選的檔案跑既有的 apply 流程。"""
    script = xkb_paths.SCRIPTS_DIR / "distill_memory_to_wiki.py"
    if not script.exists():
        print(f"找不到 {script}", file=sys.stderr)
        return 1

    files = sorted({c.file for c in load_candidates() if c.status == "approved"})
    if not files:
        print("沒有已核准的候選，不需要套用。")
        return 0

    failed = 0
    for filename in files:
        path = STAGING_DIR / filename
        print(f"\n── 套用 {filename}")
        result = subprocess.run(
            [sys.executable, str(script), "--apply", "--staging-file", str(path)],
            env=xkb_paths.subprocess_env(),
            text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            failed += 1
            print(f"  失敗（returncode={result.returncode}）", file=sys.stderr)
    return 1 if failed else 0


def print_stats(candidates: list[Candidate]) -> None:
    pending = [c for c in candidates if c.status == "pending"]
    unique = [c for c in pending if not c.duplicate_of]
    dates = sorted({c.source_date for c in pending if c.source_date != "unknown"})

    print(f"待審       {len(pending)} 條（去重後 {len(unique)} 條）")
    print(f"已核准     {sum(1 for c in candidates if c.status == 'approved')} 條")
    print(f"已略過     {sum(1 for c in candidates if c.status == 'skipped')} 條")
    if dates:
        print(f"日期範圍   {dates[0]} ~ {dates[-1]}")

    by_conf: dict[str, int] = {}
    by_topic: dict[str, int] = {}
    for c in unique:
        by_conf[c.confidence] = by_conf.get(c.confidence, 0) + 1
        by_topic[c.topic] = by_topic.get(c.topic, 0) + 1
    if by_conf:
        print("信心       " + " · ".join(f"{k} {v}" for k, v in sorted(by_conf.items())))
    if by_topic:
        print("主題分佈")
        for topic, n in sorted(by_topic.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3}  {topic}")


def main() -> int:
    parser = argparse.ArgumentParser(description="XKB 待審候選審核入口")
    parser.add_argument("--stats", action="store_true", help="待審總覽")
    parser.add_argument("--list", dest="do_list", action="store_true", help="列出待審候選")
    parser.add_argument("--limit", type=int, default=5, help="一次列出幾條（預設 5）")
    parser.add_argument("--topic", help="只看某個主題")
    parser.add_argument("--include-duplicates", action="store_true",
                        help="連重複的也列出來（預設隱藏）")
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--approve", nargs="+", metavar="ID")
    parser.add_argument("--skip", nargs="+", metavar="ID")
    parser.add_argument("--apply", action="store_true", help="把已核准的寫進 wiki")
    args = parser.parse_args()

    if args.approve or args.skip:
        code = 0
        for ids, decision in ((args.approve, "approve"), (args.skip, "skip")):
            if not ids:
                continue
            changed, missing = set_status(ids, decision)
            print(f"{decision}: {changed} 條")
            if missing:
                print(f"  找不到這些 id: {', '.join(missing)}", file=sys.stderr)
                code = 1
        return code

    if args.apply:
        return apply_approved()

    candidates = load_candidates()

    if args.stats or not args.do_list:
        print_stats(candidates)
        return 0

    pending = [c for c in candidates if c.status == "pending"]
    if not args.include_duplicates:
        pending = [c for c in pending if not c.duplicate_of]
    if args.topic:
        pending = [c for c in pending if c.topic == args.topic]
    batch = pending[: args.limit]

    if args.as_json:
        print(json.dumps({
            "pending_total": len(pending),
            "batch": [asdict(c) for c in batch],
        }, ensure_ascii=False, indent=2))
        return 0

    if not batch:
        print("沒有待審候選。")
        return 0

    print(f"待審 {len(pending)} 條，以下 {len(batch)} 條：\n")
    for c in batch:
        print(f"[{c.id}]  {c.topic} § {c.section}  ({c.confidence}, {c.source_date})")
        for line in c.text.splitlines():
            print(f"    {line}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
