#!/usr/bin/env python3
"""XKB candidate review and conservative governance engine.

Staging Markdown is source evidence. Governance artifacts are additive and
stable: IDs use source path + candidate position, fingerprints use normalized
content, and registry writes are idempotent. Existing Markdown approve/skip
markers remain supported for compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import date, timedelta
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths
import xkb_provenance
from xkb_provenance import annotate

STAGING_DIR = xkb_paths.WIKI_DIR / "_staging"
GOVERNANCE_DIR = xkb_paths.XKB_DATA_DIR / "governance"
TOPICS_DIR = xkb_paths.WIKI_TOPICS_DIR
CANDIDATE_RE = re.compile(r"(?m)^## Candidate (\d+)\s*$")
URL_RE = re.compile(r"https?://[^\s)]+", re.I)
METADATA_RE = re.compile(r"^- \*\*[^:]+:\*\*.*$", re.M)
FINGERPRINT_CHARS = 120


def normalize(text: str) -> str:
    """Normalize candidate content without retaining review metadata."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\*\*Status:\*\*.*", " ", text, flags=re.I)
    text = METADATA_RE.sub(" ", text)
    text = re.sub(r"[`*_>#-]+", " ", text)
    return " ".join(text.casefold().split())


def stable_candidate_id(source_file: str, candidate_number: int) -> str:
    canonical = Path(source_file).as_posix().lstrip("./")
    value = f"{canonical}#{candidate_number}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def stable_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


@dataclass
class Candidate:
    # id is retained as the human-friendly legacy ID (file stem#position).
    id: str
    file: str
    index: int
    topic: str
    section: str
    confidence: str
    source_date: str
    status: str
    text: str
    duplicate_of: str = ""
    candidate_id: str = ""
    fingerprint: str = ""
    normalized_content: str = ""
    source_file: str = ""
    source_position: int = 0
    evidence: list[str] = field(default_factory=list)
    evidence_present: bool = False
    provenance_complete: bool = False
    reusable: bool = False
    relation: str = "unique"
    near_duplicate_of: str = ""
    topic_key: str = ""
    # 被導向 general 時，原本提議的主題名。留著它，日後這些散落的條目
    # 才撈得回來組成那一頁。
    proposed_topic: str = ""
    episode_count: int = 1
    source_count: int = 1


def _field(block: str, name: str, default: str = "") -> str:
    m = re.search(rf"\*\*{re.escape(name)}:\*\*\s*(.+)", block, re.I)
    return m.group(1).strip() if m else default


def _body(block: str) -> str:
    lines: list[str] = []
    collecting = False
    for line in block.splitlines():
        if line.startswith("- **") or line.startswith("---"):
            collecting = False
        if collecting and line.strip():
            lines.append(line)
        if re.match(r"^- \*\*Status:\*\*", line, re.I):
            collecting = True
    return "\n".join(lines).strip()


def _status(block: str) -> str:
    if re.search(r"\*\*Status:\*\*.*\[x\]\s*approve", block, re.I):
        return "approved"
    if re.search(r"\*\*Status:\*\*.*\[x\]\s*skip", block, re.I):
        return "skipped"
    return "pending"


def _split_candidates(content: str) -> list[tuple[int, str]]:
    parts = CANDIDATE_RE.split(content)
    if len(parts) < 3:
        return []
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts), 2)]


def load_candidates(classify: bool = True) -> list[Candidate]:
    if not STAGING_DIR.exists():
        return []
    out: list[Candidate] = []
    for path in sorted(STAGING_DIR.rglob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for number, block in _split_candidates(content):
            text = _body(block)
            if not text:
                continue
            source_file = path.relative_to(STAGING_DIR).as_posix()
            norm = normalize(text)
            topic = _field(block, "Topic")
            evidence = URL_RE.findall(text)
            candidate = Candidate(
                id=f"{path.stem.replace('-candidates', '')}#{number}",
                file=source_file, index=number, topic=topic,
                section=_field(block, "Section", "核心概念"),
                confidence=_field(block, "Confidence", "medium").casefold(),
                source_date=_field(block, "Source date", "unknown"),
                status=_status(block), text=text,
                candidate_id=stable_candidate_id(source_file, number),
                fingerprint=hashlib.sha256(norm.encode("utf-8")).hexdigest(),
                normalized_content=norm, source_file=source_file,
                source_position=number, evidence=evidence,
                evidence_present=bool(evidence),
                provenance_complete=bool(source_file and number > 0),
                reusable=len(norm) >= 40 and not re.search(r"\b(i think|maybe|unclear)\b", norm),
                topic_key=re.sub(r"^\[NEW:\s*|\]$", "", topic).strip(),
                episode_count=max(1, int(_field(block, "Episode count", "1") or 1)),
                source_count=max(1, int(_field(block, "Source count", "1") or 1)),
            )
            out.append(candidate)
    if classify:
        _classify_relations(out)
    return out


def _classify_relations(candidates: list[Candidate]) -> None:
    positions = {id(candidate): index for index, candidate in enumerate(candidates)}
    by_fp: dict[str, Candidate] = {}
    for candidate in candidates:
        previous = by_fp.get(candidate.fingerprint)
        if previous:
            candidate.duplicate_of = previous.candidate_id
            candidate.relation = "exact_duplicate"
        else:
            by_fp[candidate.fingerprint] = candidate
    # Character-shingle blocking keeps comparisons bounded without assuming
    # candidates share the same prefix; SequenceMatcher is the final threshold.
    buckets: dict[str, list[Candidate]] = {}
    shingle_counts: dict[str, int] = {}
    for candidate in candidates:
        text = candidate.normalized_content
        for shingle in {text[i:i + 3] for i in range(max(0, len(text) - 2))}:
            shingle_counts[shingle] = shingle_counts.get(shingle, 0) + 1
    for candidate in candidates:
        text = candidate.normalized_content
        for shingle in {text[i:i + 3] for i in range(max(0, len(text) - 2))}:
            if shingle_counts[shingle] <= 32:
                buckets.setdefault(shingle, []).append(candidate)
    for i, candidate in enumerate(candidates):
        if candidate.relation != "unique":
            continue
        text = candidate.normalized_content
        nearby = []
        seen: set[int] = set()
        for shingle in {text[j:j + 3] for j in range(max(0, len(text) - 2))}:
            for other in buckets.get(shingle, []):
                if id(other) not in seen:
                    nearby.append(other)
                    seen.add(id(other))
        for other in nearby:
            if other is candidate or positions[id(other)] >= i:
                continue
            if other.relation == "exact_duplicate":
                continue
            ratio = SequenceMatcher(None, candidate.normalized_content, other.normalized_content).ratio()
            if ratio >= 0.86:
                candidate.near_duplicate_of = other.candidate_id
                candidate.relation = "near_duplicate"
                break


def _has_evidence(candidate: Candidate) -> bool:
    """A link is the strongest evidence, but distilled candidates rarely quote one.

    These are conclusions drawn from work sessions, not clippings, so requiring
    an inline URL rejected 1,122 of 1,132 pending candidates for lacking a
    property they could never have had. Naming the staging file, the position
    inside it, and a real date traces the claim back to what was actually said,
    which is what this gate is asking for.
    """
    if candidate.evidence_present:
        return True
    if not candidate.provenance_complete:
        return False
    try:
        date.fromisoformat(candidate.source_date)
    except ValueError:
        return False
    return True


def _expired(candidate: Candidate, ttl_days: int, as_of: date) -> bool:
    if ttl_days <= 0 or candidate.source_date == "unknown":
        return False
    try:
        return date.fromisoformat(candidate.source_date) <= as_of - timedelta(days=ttl_days)
    except ValueError:
        return False


# 一個名字被提議過幾次，才算「重複出現」而值得開一頁。五次是刻意保守的：
# 開一頁很便宜，但一頁只放一條就是把佇列的問題搬進 wiki 裡。
PROMOTE_AFTER = 5
GENERAL_TOPIC = "general"


def _proposed_counts(candidates: list[Candidate]) -> dict[str, int]:
    """每個被提議的新主題名，在這一批裡出現幾次。"""
    counts: dict[str, int] = {}
    for candidate in candidates:
        if candidate.topic.startswith("[NEW:") and candidate.topic_key:
            counts[candidate.topic_key] = counts.get(candidate.topic_key, 0) + 1
    return counts


GENERAL_PAGE = """---
title: 一般
tags: [general, unsorted]
sources: 0
last_updated: {today}
status: seeded
slug: general
---

# 一般

還沒有自己主題頁的知識。每一條後面都留著它當初被提議的主題名稱——同一個名稱
累積到門檻之後，這些散落的條目就可以被撈出來組成那一頁。

這裡不是垃圾場，是候診室。條目長期停在這裡而且從不重複，通常代表它本來就是
一次性的資訊，那也是一個答案。

"""


def _ensure_general_topic() -> bool:
    """確保 general 這一頁存在。回傳它現在是否可用。

    wiki 是資料不是程式，不在版控裡，所以這一頁不會隨 repo 散佈。少了它，
    導向會把每一條提案變成 missing_topic 扣留——提案數歸零、看起來修好了，
    實際上什麼都沒吸收。
    """
    page = TOPICS_DIR / f"{GENERAL_TOPIC}.md"
    if page.exists():
        return True
    try:
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(GENERAL_PAGE.format(today=date.today()), encoding="utf-8")
        return True
    except OSError:
        return False


def _route_new_topics(candidates: list[Candidate], counts: dict[str, int]) -> int:
    """把只出現過一兩次的新主題導向 general，保留原本提議的名字。

    這些條目原本會無限期停在提案區——不是因為內容不好，是因為沒有人替它們
    命名。累積到門檻的仍然留作提案，因為決定要不要開一頁是領域判斷。
    """
    routed = 0
    if any(c.topic.startswith("[NEW:") for c in candidates) and not _ensure_general_topic():
        # 送到一個不存在的頁面，比讓它們留在提案區更糟：看起來被處理了。
        return 0
    for candidate in candidates:
        if not candidate.topic.startswith("[NEW:") or not candidate.topic_key:
            continue
        if counts.get(candidate.topic_key, 0) >= PROMOTE_AFTER:
            continue
        candidate.proposed_topic = candidate.topic_key
        candidate.topic = GENERAL_TOPIC
        candidate.topic_key = GENERAL_TOPIC
        routed += 1
    return routed


def _would_promote(candidate: Candidate, ttl_days: int, as_of: date) -> bool:
    """Whether this candidate would reach a topic page on this run, as configured."""
    return (not _expired(candidate, ttl_days, as_of)
            and candidate.relation == "unique"
            and not candidate.topic.startswith("[NEW:")
            and _safe_promotable(candidate)
            and _topic_available(candidate))


def _safe_promotable(candidate: Candidate) -> bool:
    if candidate.relation != "unique" or not candidate.provenance_complete or not _has_evidence(candidate):
        return False
    if not candidate.reusable or candidate.topic.startswith("[NEW:"):
        return False
    # medium used to additionally require two episodes and two sources. Those
    # counts come from Memmy's induction gate, which reads L1 trace episode
    # ids; staging Markdown has none and deliberately refuses to invent them
    # from a filename, so the fields were always absent and every medium
    # candidate defaulted to 1. The rule could not be satisfied by this data,
    # and 659 candidates sat behind it. Corroboration still belongs in the L1
    # candidate pool, where episodes are real.
    return candidate.confidence in ("high", "medium")


def _topic_available(candidate: Candidate) -> bool:
    """A gated candidate is promotable only when its existing topic exists."""
    return bool(candidate.topic_key and not candidate.topic.startswith("[NEW:")
                and (TOPICS_DIR / f"{candidate.topic_key}.md").exists())


def _promoted_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    promoted = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("candidate_id") and row.get("lifecycle") == "promoted":
            promoted.add(row["candidate_id"])
    return promoted


def _promote_topics(candidates: list[Candidate], snapshot_dir: Path) -> list[dict[str, str]]:
    changes = []
    for candidate in candidates:
        if not _safe_promotable(candidate) or not _topic_available(candidate):
            continue
        topic_path = TOPICS_DIR / f"{candidate.topic_key}.md"
        marker = xkb_provenance.candidate_marker(candidate.candidate_id)
        content = topic_path.read_text(encoding="utf-8")
        if marker in content:
            continue
        backup = snapshot_dir / "topics" / topic_path.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(topic_path, backup)
        # Promotion used to append a bare bullet at end of file, so every
        # promoted claim joined whatever the last section happened to be. The
        # indexer caps a section at 4,000 characters, and two of the target
        # pages were already at that cap: the text reached the wiki and never
        # reached a vector. Giving each claim its own heading makes it its own
        # section and its own vector, which also stops page length from
        # mattering at all.
        heading = re.sub(r"\s+", " ", candidate.text).strip()[:60].rstrip()
        # Distilled from Pan's own notes, so it carries the self-derived
        # marker and takes the recall penalty. Without it these outrank the
        # external sources they were reasoned from.
        provenance = annotate(f"{candidate.source_file}#{candidate.source_position}")
        addition = (
            f"\n\n### {heading or candidate.candidate_id[:12]}\n"
            f"{candidate.text} <!-- {marker} --> {provenance}\n"
        )
        topic_path.write_text(content.rstrip() + addition, encoding="utf-8")
        changes.append({"candidate_id": candidate.candidate_id, "topic": str(topic_path), "snapshot": str(backup)})
    return changes


def rollback_batch(batch_id: str) -> dict[str, Any]:
    manifest_path = GOVERNANCE_DIR / "manifests" / f"{batch_id}.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"unknown governance batch: {batch_id}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored = 0
    for item in manifest.get("rollback", {}).get("restores", []):
        artifact = Path(item["artifact"])
        snapshot = Path(item["snapshot"])
        if item.get("existed") and snapshot.exists():
            artifact.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot, artifact)
        elif not item.get("existed") and artifact.exists():
            artifact.unlink()
        restored += 1
    for item in manifest.get("topic_changes", []):
        snapshot = Path(item["snapshot"])
        topic = Path(item["topic"])
        if snapshot.exists():
            shutil.copy2(snapshot, topic)
            restored += 1
    audit = GOVERNANCE_DIR / "audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "rollback", "batch_id": batch_id,
                             "restored": restored, "source_untouched": True},
                            sort_keys=True) + "\n")
    return {"batch_id": batch_id, "restored": restored, "source_untouched": True}


def write_registry(candidates: list[Candidate], path: Path, promoted_ids: set[str] | None = None) -> dict[str, Any]:
    """Append only unseen stable records; never duplicate a registry row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    existing_lifecycle: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("candidate_id"):
                    existing.add(row["candidate_id"])
                    existing_lifecycle[row["candidate_id"]] = row.get("lifecycle", "")
            except json.JSONDecodeError:
                continue
    added = 0
    with path.open("a", encoding="utf-8") as fh:
        for candidate in candidates:
            if candidate.candidate_id in existing:
                if promoted_ids and candidate.candidate_id in promoted_ids and existing_lifecycle.get(candidate.candidate_id) != "promoted":
                    row = asdict(candidate)
                    row.pop("text", None)
                    row["lifecycle"] = "promoted"
                    row["source_evidence"] = {"file": candidate.source_file, "position": candidate.source_position}
                    fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    existing_lifecycle[candidate.candidate_id] = "promoted"
                continue
            row = asdict(candidate)
            row.pop("text", None)
            row["lifecycle"] = (
                "promoted" if promoted_ids and candidate.candidate_id in promoted_ids
                else "retained"
            )
            if row["lifecycle"] == "retained" and not _topic_available(candidate):
                row["retained_reason"] = "missing_topic"
            row["source_evidence"] = {"file": candidate.source_file, "position": candidate.source_position}
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            existing.add(candidate.candidate_id)
            added += 1
    return {"added": added, "existing": len(candidates) - added}


def governance_batch(limit: int = 50, dry_run: bool = True, ttl_days: int = 30) -> dict[str, Any]:
    registry = GOVERNANCE_DIR / "candidate-registry.jsonl"
    # Classify against the complete pending pool, not only this batch.  A
    # bounded batch must still detect duplicates that arrived in different
    # runs.  Registry-known candidates are already governed; excluding them
    # lets successive daily runs advance through the backlog instead of
    # selecting the same first N pending Markdown blocks forever.
    candidates = load_candidates(classify=False)
    pending = [c for c in candidates if c.status == "pending"]
    _classify_relations(pending)
    # _eligible 自己會算一次，但下面還有兩處要用同一份名單。
    already_promoted = _promoted_ids(registry)
    eligible = _eligible(pending, registry)
    # A bounded batch taken in staging-file order can spend weeks registering
    # candidates it will not promote before reaching one it would. Sorting is
    # stable and keyed on the outcome under the TTL actually in force, so a
    # normal run — where nothing old enough to matter qualifies — keeps its
    # existing order, while a backlog pass with a relaxed TTL reaches the
    # candidates it was run for.
    as_of = date.today()

    # 導向要在排序之前。_would_promote 對 [NEW: x] 一律回 False，所以還沒
    # 導向的話，這些候選會被排到最後，永遠進不了 bounded 批次——那正是
    # 212 條提案卡了幾個月的原因。計數用全部 eligible，不是這一批，
    # 否則「重複出現」會被批次大小切碎。
    proposed_counts = _proposed_counts(eligible)
    routed_to_general = _route_new_topics(eligible, proposed_counts)

    eligible.sort(key=lambda c: not _would_promote(c, ttl_days, as_of))
    bounded = eligible[: max(0, limit)]
    topic_groups: dict[str, list[Candidate]] = {}
    stats = {"discovered": len(bounded), "new": len(bounded), "promoted": 0,
             "approved": 0, "skipped": 0, "retained": 0, "ttl": 0,
             "quarantine": 0, "review_queue": 0, "proposal_queue": 0,
             "duplicates": 0, "near_duplicates": 0,
             "routed_to_general": routed_to_general}
    queues: dict[str, list[dict[str, Any]]] = {"review_queue": [], "proposal_queue": [], "quarantine": []}
    topic_changes: list[dict[str, str]] = []
    for candidate in bounded:
        if _expired(candidate, ttl_days, as_of):
            stats["ttl"] += 1
            stats["quarantine"] += 1
            record = asdict(candidate)
            record["lifecycle"] = "quarantined"
            record["quarantine_reason"] = f"source_date older than {ttl_days} days"
            queues["quarantine"].append(record)
            continue
        if candidate.relation == "exact_duplicate":
            stats["duplicates"] += 1
            stats["skipped"] += 1
            continue
        if candidate.relation == "near_duplicate":
            stats["near_duplicates"] += 1
            stats["review_queue"] += 1
            queues["review_queue"].append(asdict(candidate))
            continue
        if candidate.topic.startswith("[NEW:"):
            stats["proposal_queue"] += 1
            queues["proposal_queue"].append(asdict(candidate))
            continue
        if (_safe_promotable(candidate) and _topic_available(candidate)
                and candidate.candidate_id not in already_promoted):
            stats["promoted"] += 1
            stats["approved"] += 1
        else:
            stats["review_queue"] += 1
            stats["retained"] += 1
            queues["review_queue"].append(asdict(candidate))

    for candidate in bounded:
        if candidate.topic_key and not candidate.topic.startswith("[NEW:"):
            topic_groups.setdefault(candidate.topic_key, []).append(candidate)
    topic_suggestions = []
    for topic, group in sorted(topic_groups.items()):
        if len(group) > 1:
            topic_suggestions.append({"action": "cluster", "topic": topic,
                                      "candidate_ids": [c.candidate_id for c in group],
                                      "reason": "same normalized topic key"})
            topic_suggestions.append({"action": "merge_suggestion", "topic": topic,
                                      "candidate_ids": [c.candidate_id for c in group],
                                      "reason": "same topic key; review as one topic membership set"})
    new_topics = sorted({c.topic_key for c in bounded if c.topic.startswith("[NEW:")})
    topic_suggestions.extend({"action": "proposal", "topic": topic,
                              "candidate_ids": [c.candidate_id for c in bounded if c.topic_key == topic],
                              "proposed_count": proposed_counts.get(topic, 0),
                              "reason": f"proposed {proposed_counts.get(topic, 0)}x "
                                        f"(>= {PROMOTE_AFTER}); 開不開這一頁是領域判斷"}
                             for topic in new_topics)
    if not dry_run:
        batch_key = "\n".join(f"{c.candidate_id}:{int(_topic_available(c))}" for c in bounded)
        batch_id = hashlib.sha256(batch_key.encode("utf-8")).hexdigest()[:16]
        registry_result = {"added": 0, "existing": 0}
        manifest_dir = GOVERNANCE_DIR / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        snapshot_dir = GOVERNANCE_DIR / "snapshots" / batch_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{batch_id}.json"
        if manifest_path.exists():
            stats["promoted"] = 0
            stats["approved"] = 0
            registry_result["batch_id"] = batch_id  # type: ignore[index]
            registry_result["idempotent_replay"] = True
            return {"dry_run": dry_run, "limit": limit, "batch_id": batch_id, "stats": stats,
                    "registry": registry_result, "queues": queues,
                    "topic_suggestions": topic_suggestions,
                    "source_dir": str(STAGING_DIR), "ttl_days": ttl_days, "as_of": as_of.isoformat(),
                    "topic_changes": []}
        audit = GOVERNANCE_DIR / "audit.jsonl"
        source_hashes = {c.source_file: hashlib.sha256((STAGING_DIR / c.source_file).read_bytes()).hexdigest()
                         for c in bounded if (STAGING_DIR / c.source_file).exists()}
        for source_file in sorted(source_hashes):
            source = STAGING_DIR / source_file
            target = snapshot_dir / source_file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        pre_write = []
        for artifact in (registry, audit):
            snapshot = snapshot_dir / "artifacts" / artifact.name
            if artifact.exists():
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(artifact, snapshot)
                pre_write.append({"artifact": str(artifact), "snapshot": str(snapshot), "existed": True})
            else:
                pre_write.append({"artifact": str(artifact), "snapshot": str(snapshot), "existed": False})
        # _expired 必須在這裡再檢查一次。上面的迴圈對過期候選 continue，
        # 所以乾跑看起來有擋住；實跑走的是這條路，而這裡原本沒有過濾——
        # 同一批統計會同時說「隔離 1 條」和「放行 1 條」，指的是同一條。
        # 一個永遠攔不下東西的閘門，比沒有閘門更糟：它讓人以為擋住了。
        topic_changes = _promote_topics(
            [c for c in bounded if _safe_promotable(c)
             and not _expired(c, ttl_days, as_of)
             and c.candidate_id not in already_promoted], snapshot_dir
        )
        promoted_ids = {item["candidate_id"] for item in topic_changes}
        # Registry lifecycle follows the topic write, never the gate alone.
        registry_result = write_registry(bounded, registry, promoted_ids)
        stats["promoted"] = len(promoted_ids)
        stats["approved"] = len(promoted_ids)
        stats["retained"] += sum(
            1 for c in bounded if _safe_promotable(c) and c.candidate_id not in promoted_ids
        )
        manifest = {"schema": "xkb-governance-batch.v1", "batch_id": batch_id,
                    "candidate_ids": [c.candidate_id for c in bounded], "stats": stats,
                    "dry_run": False, "source_dir": str(STAGING_DIR),
                    "source_hashes": source_hashes,
                    "artifact_paths": {"registry": str(registry), "manifest": str(manifest_path),
                                       "audit": str(audit), "snapshot": str(snapshot_dir)},
                    "completion_status": "completed", "rollback": {"snapshot_dir": str(snapshot_dir),
                                                                         "restores": pre_write,
                                                                         "source_untouched": True}}
        manifest["topic_changes"] = topic_changes
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "governance_batch", "batch_id": batch_id,
                                 "stats": stats, "candidate_ids": manifest["candidate_ids"],
                                 "source_hashes": source_hashes, "completion_status": "completed",
                                 "pre_write_snapshots": pre_write},
                                ensure_ascii=False, sort_keys=True) + "\n")
        registry_result["batch_id"] = batch_id  # type: ignore[index]
    else:
        registry_result = {"added": 0, "existing": 0, "dry_run": True}
    return {"dry_run": dry_run, "limit": limit, "batch_id": locals().get("batch_id", ""), "stats": stats,
            "registry": registry_result, "queues": queues,
            "topic_suggestions": topic_suggestions,
            "source_dir": str(STAGING_DIR), "ttl_days": ttl_days, "as_of": as_of.isoformat(),
            "topic_changes": topic_changes}


def _registry_state(registry: Path) -> tuple[set[str], set[str], dict[str, str]]:
    """(已放行, 已登記, 每一筆的扣留理由)。"""
    promoted = _promoted_ids(registry)
    registered: set[str] = set()
    reasons: dict[str, str] = {}
    if registry.exists():
        for line in registry.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("candidate_id"):
                registered.add(row["candidate_id"])
                reasons[row["candidate_id"]] = row.get("retained_reason", "")
    return promoted, registered, reasons


def _eligible(candidates: list[Candidate], registry: Path) -> list[Candidate]:
    """治理下一輪真的會處理的那些。

    「還沒被登記過」才算待處理。治理刻意不動 staging，所以一個已經被看過、
    判定證據或信心不足而扣住的候選，在 staging 裡永遠還是 pending——把它算成
    積壓，會得到一個永遠亮著的紅燈，而永遠亮著的紅燈跟永遠不亮的綠燈一樣，
    看久了就不看了。

    兩種例外會重新進場：主題後來出現了，以及主題頁需要先消化。
    """
    promoted, registered, reasons = _registry_state(registry)
    return [c for c in candidates if c.candidate_id not in promoted and (
        c.candidate_id not in registered
        or reasons.get(c.candidate_id) == "missing_topic"
        or reasons.get(c.candidate_id) == "topic_needs_synthesis"
    )]


def governance_health_counts(ttl_days: int = 30) -> dict[str, int]:
    """Return secret-free actionable counts without writing governance artifacts."""
    # A promoted candidate is still marked pending in its staging file, because
    # governance never edits the source. Counting it as outstanding work would
    # keep the alert red no matter how much was absorbed, and an alert that can
    # never clear stops being read.
    registry = GOVERNANCE_DIR / "candidate-registry.jsonl"
    staged = [c for c in load_candidates(classify=False) if c.status == "pending"]
    candidates = _eligible(staged, registry)
    _classify_relations(candidates)
    # 導向要在計數之前，跟 governance_batch 同一個順序。少了這一步，一個
    # 會被導向 general 的候選在這裡仍算成提案——報 1，實際 0。
    _route_new_topics(candidates, _proposed_counts(candidates))
    promoted, registered, _ = _registry_state(registry)
    result = {"pending": len(candidates), "high": 0, "medium": 0, "low": 0,
              "proposal": 0, "quarantine": 0, "overdue": 0, "safe_promotion": 0,
              # 已經看過、判定不放行而扣住的。它們不是待辦，但也沒有被丟掉。
              # 要扣掉 eligible——被扣住卻又重新進場的候選（missing_topic）
              # 兩邊都算的話，同一批東西會用兩個互相矛盾的標籤各印一次。
              "held": sum(1 for c in staged
                          if c.candidate_id in registered
                          and c.candidate_id not in promoted
                          and c not in candidates)}
    today = date.today()
    for candidate in candidates:
        result[candidate.confidence] = result.get(candidate.confidence, 0) + 1
        if candidate.topic.startswith("[NEW:"):
            result["proposal"] += 1
        if _expired(candidate, ttl_days, today):
            result["quarantine"] += 1
            result["overdue"] += 1
        elif _safe_promotable(candidate):
            result["safe_promotion"] += 1
    return result


def set_status(ids: list[str], decision: str) -> tuple[int, list[str]]:
    wanted = set(ids); changed = 0; by_file: dict[str, list[Candidate]] = {}
    for candidate in load_candidates():
        if candidate.id in wanted or candidate.candidate_id in wanted:
            by_file.setdefault(candidate.file, []).append(candidate)
    found = {c.id for group in by_file.values() for c in group} | {c.candidate_id for group in by_file.values() for c in group}
    missing = sorted(wanted - found)
    marker = "[x] approve  [ ] skip" if decision == "approve" else "[ ] approve  [x] skip"
    for filename, group in by_file.items():
        path = STAGING_DIR / filename
        parts = CANDIDATE_RE.split(path.read_text(encoding="utf-8"))
        targets = {c.index for c in group}
        for i in range(1, len(parts), 2):
            if int(parts[i]) in targets:
                parts[i + 1] = re.sub(r"(\*\*Status:\*\*).*", lambda m: f"{m.group(1)} {marker}", parts[i + 1], count=1)
                changed += 1
        path.write_text(parts[0] + "".join(f"\n## Candidate {parts[i]}\n{parts[i + 1]}" for i in range(1, len(parts), 2)), encoding="utf-8")
    return changed, missing


def apply_approved() -> int:
    script = xkb_paths.SCRIPTS_DIR / "distill_memory_to_wiki.py"
    if not script.exists():
        print(f"找不到 {script}", file=sys.stderr); return 1
    files = sorted({c.file for c in load_candidates() if c.status == "approved"})
    if not files:
        print("沒有已核准的候選，不需要套用。"); return 0
    failed = 0
    for filename in files:
        result = subprocess.run([sys.executable, str(script), "--apply", "--staging-file", str(STAGING_DIR / filename)], env=xkb_paths.subprocess_env(), text=True, encoding="utf-8", errors="replace")
        if result.returncode: failed += 1
    return 1 if failed else 0


def print_stats(candidates: list[Candidate]) -> None:
    pending = [c for c in candidates if c.status == "pending"]
    print(f"待審       {len(pending)} 條（去重後 {sum(not c.duplicate_of for c in pending)} 條）")
    print(f"已核准     {sum(c.status == 'approved' for c in candidates)} 條")
    print(f"已略過     {sum(c.status == 'skipped' for c in candidates)} 條")


def main() -> int:
    parser = argparse.ArgumentParser(description="XKB 候選審核與治理")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--list", dest="do_list", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--topic")
    parser.add_argument("--include-duplicates", action="store_true")
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--approve", nargs="+")
    parser.add_argument("--skip", nargs="+")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--governance", action="store_true", help="bounded governance batch")
    parser.add_argument("--dry-run", action="store_true", help="governance preview; no artifact writes")
    parser.add_argument("--write-governance", action="store_true", help="write additive registry governance artifacts")
    parser.add_argument("--rollback", metavar="BATCH_ID", help="restore exactly one governance batch")
    parser.add_argument("--ttl-days", type=int,
                        default=int(os.getenv("XKB_GOVERNANCE_TTL_DAYS", "30")),
                        help="quarantine candidates whose source date is older than this "
                             "(default 30, or XKB_GOVERNANCE_TTL_DAYS)")
    args = parser.parse_args()
    if args.approve or args.skip:
        code = 0
        for ids, decision in ((args.approve, "approve"), (args.skip, "skip")):
            if ids:
                changed, missing = set_status(ids, decision); print(f"{decision}: {changed} 條")
                if missing: print(f"找不到: {', '.join(missing)}", file=sys.stderr); code = 1
        return code
    if args.apply: return apply_approved()
    if args.rollback:
        print(json.dumps(rollback_batch(args.rollback), ensure_ascii=False, indent=2)); return 0
    candidates = load_candidates()
    if args.governance:
        print(json.dumps(governance_batch(args.limit, not args.write_governance, args.ttl_days),
                         ensure_ascii=False, indent=2)); return 0
    if args.stats or not args.do_list: print_stats(candidates); return 0
    pending = [c for c in candidates if c.status == "pending" and (args.include_duplicates or not c.duplicate_of) and (not args.topic or c.topic == args.topic)]
    batch = pending[:args.limit]
    if args.as_json: print(json.dumps({"pending_total": len(pending), "batch": [asdict(c) for c in batch]}, ensure_ascii=False, indent=2)); return 0
    for c in batch: print(f"[{c.id}] {c.topic} § {c.section} ({c.confidence}, {c.source_date})\n    {c.text}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
