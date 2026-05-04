#!/bin/bash
# build_search_index.sh - 建立 / 增量更新 search_index.json（加速搜尋）

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
BOOKMARKS_DIR="${BOOKMARKS_DIR:-$WORKSPACE_DIR/memory/bookmarks}"
INDEX_FILE="${INDEX_FILE:-$BOOKMARKS_DIR/search_index.json}"
MODE="${1:-full}"  # full | --incremental

mkdir -p "$(dirname "$INDEX_FILE")"

python3 - "$WORKSPACE_DIR" "$BOOKMARKS_DIR" "$INDEX_FILE" "$MODE" <<'PY'
import json
import re
import sys
from pathlib import Path

workspace = Path(sys.argv[1]).expanduser().resolve()
bookmarks_dir = Path(sys.argv[2]).expanduser().resolve()
index_file = Path(sys.argv[3]).expanduser().resolve()
mode = sys.argv[4]
incremental = mode == "--incremental"
cards_dir = Path(__import__("os").environ.get("CARDS_DIR", str(workspace / "memory" / "cards"))).expanduser().resolve()


def _safe_relative(path: Path, base: Path) -> str | None:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return None


def canonical_path_from_relative(rel: str) -> Path:
    """Resolve all relative_path variants that have existed in XKB indexes.

    Historical index rows used paths relative to memory/bookmarks. Later enriched
    rows used either memory/cards/<id>.md or cards/<id>.md. Incremental rebuilds
    must compare by the actual file path, otherwise valid card rows look removed
    when the scanner is rooted at memory/bookmarks.
    """
    rel = (rel or "").strip()
    if not rel:
        return Path("")
    p = Path(rel)
    if p.is_absolute():
        return p.resolve()
    if rel.startswith("memory/cards/") or rel.startswith("memory/bookmarks/"):
        return (workspace / rel).resolve()
    if rel.startswith("cards/"):
        return (workspace / "memory" / rel).resolve()
    return (bookmarks_dir / rel).resolve()


def canonical_key_for_item(item: dict) -> str:
    raw_path = (item.get("path") or "").strip()
    if raw_path:
        p = Path(raw_path).expanduser()
        if p.is_absolute():
            return str(p.resolve())
        return str((workspace / p).resolve())
    return str(canonical_path_from_relative(item.get("relative_path") or ""))


def relative_for_file(f: Path, root: Path) -> str:
    if root == bookmarks_dir:
        return str(f.relative_to(bookmarks_dir))
    rel = _safe_relative(f, workspace)
    return rel if rel is not None else str(f)


def parse_record(f: Path, root: Path):
    text = f.read_text(encoding="utf-8", errors="ignore")

    title = ""
    m = re.search(r'^title:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
        if m:
            title = m.group(1).strip()
    if not title:
        title = f.stem

    category = "general"
    m = re.search(r'^category:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
    if m:
        category = m.group(1).strip()

    tags = []
    m = re.search(r'^tags:\s*\[(.*?)\]\s*$', text, re.MULTILINE)
    if m:
        tags = [t.strip().strip('"\'') for t in m.group(1).split(',') if t.strip()]
    if not tags:
        tags = re.findall(r"#([\w\-\u4e00-\u9fff]+)", text)

    summary = ""
    m = re.search(r'##\s*📌\s*一句話摘要\s*\n+(.+)', text)
    if m:
        summary = m.group(1).strip()
    if not summary:
        m = re.search(r'##\s*📝\s*AI\s*濃縮\s*\n+(.+)', text)
        if m:
            summary = m.group(1).strip()[:200]
    if not summary:
        bilingual = re.search(r"##\s+7\.\s*雙語摘要[^\n]*\n(.+?)(?=\n##|\Z)", text, re.DOTALL)
        if bilingual:
            block = bilingual.group(1)
            parts = []
            for label in [r"(?:ZH|中文)", r"(?:EN|英文)"]:
                mm = re.search(r"^" + label + r"[：:]\s*(.+)$", block, re.MULTILINE)
                if mm and mm.group(1).strip():
                    parts.append(mm.group(1).strip())
            if parts:
                summary = " | ".join(parts)[:300]
    if not summary:
        for pat in [
            r"##\s+1\.\s*核心摘要\s*\n(.+?)(?=\n##|\Z)",
            r"##\s+1\.\s*English Summary\s*\n(.+?)(?=\n##|\Z)",
            r"##\s*📝\s*English Summary\s*\n(.+?)(?=\n##|\Z)",
        ]:
            m = re.search(pat, text, re.DOTALL)
            if m:
                lines = [l.strip().lstrip("-").strip() for l in m.group(1).splitlines() if l.strip()]
                summary = " ".join(lines)[:300]
                break

    source_type = ""
    m = re.search(r'^source_type:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
    if m:
        source_type = m.group(1).strip()
    if not source_type:
        stem = f.stem
        if stem.startswith("github_fork-"):
            source_type = "github_fork"
        elif stem.startswith("github_star-"):
            source_type = "github_star"
        elif stem.startswith("youtube-"):
            source_type = "youtube"
        elif stem.startswith("local-"):
            source_type = "local-paper"
        elif re.fullmatch(r"\d{15,20}", stem):
            source_type = "x-bookmark"
        else:
            source_type = "local"

    # extract source_url at index time (avoids re-reading file at recall time)
    source_url = ""
    for _pat in [
        r'^source_url:\s*"?([^"\n]+)"?\s*$',
        r'^original_url:\s*"?([^"\n]+)"?\s*$',
        r'\*\*原始連結\*\*：\s*(\S+)',
        r'https://x\.com/\S+',
        r'https://twitter\.com/\S+',
    ]:
        _mu = re.search(_pat, text, re.MULTILINE)
        if _mu:
            source_url = (_mu.group(1) if _mu.groups() else _mu.group(0)).strip().strip('"')
            break

    # fallback: derive source_url from tweet_id frontmatter
    if not source_url:
        _tid = re.search(r'^tweet_id:\s*"?([0-9]+)"?\s*$', text, re.MULTILINE)
        if _tid:
            source_url = "https://x.com/i/status/" + _tid.group(1).strip()


    searchable = "\n".join([
        title,
        category,
        " ".join(tags),
        summary,
        text[:2000],
    ])

    st = f.stat()
    return {
        "path": str(f),
        "relative_path": relative_for_file(f, root),
        "title": title,
        "category": category,
        "tags": tags,
        "summary": summary,
        "source_url": source_url,
        "source_type": source_type,
        "enriched": root == cards_dir or bool(summary),
        "searchable": searchable,
        "mtime": int(st.st_mtime),
        "size": int(st.st_size),
    }


def iter_markdown_files(root: Path):
    if not root.exists():
        return
    for f in root.rglob("*.md"):
        if f.name.startswith("."):
            continue
        if f.name in {"INDEX.md"}:
            continue
        if "inbox" in f.parts:
            continue
        yield f


files = []
seen_paths = set()
for root in [bookmarks_dir, cards_dir]:
    for f in iter_markdown_files(root) or []:
        key = str(f.resolve())
        if key in seen_paths:
            continue
        seen_paths.add(key)
        files.append((f, root))

def _mtime_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _size_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


if not incremental or not index_file.exists():
    records = [parse_record(f, root) for f, root in files]
    status = "full"
else:
    old = json.loads(index_file.read_text(encoding="utf-8"))
    old_items = old.get("items", [])
    by_key = {canonical_key_for_item(it): it for it in old_items if canonical_key_for_item(it)}

    current_key_set = set()
    changed = 0

    for f, root in files:
        key = str(f.resolve())
        current_key_set.add(key)
        st = f.stat()
        old_item = by_key.get(key)

        if (
            old_item is None
            or _mtime_int(old_item.get("mtime")) != int(st.st_mtime)
            or _size_int(old_item.get("size")) != int(st.st_size)
        ):
            by_key[key] = parse_record(f, root)
            changed += 1

    # 移除已刪除檔案；以 canonical file path 比對，支援 bookmarks/cards 混合索引。
    removed_keys = [k for k in by_key.keys() if k not in current_key_set]
    for k in removed_keys:
        by_key.pop(k, None)

    records = list(by_key.values())
    status = f"incremental (changed: {changed}, removed: {len(removed_keys)})"

def record_identity(item: dict) -> str:
    rel_or_path = item.get("relative_path") or item.get("path") or ""
    stem = Path(rel_or_path).stem
    if re.fullmatch(r"\d{15,20}", stem):
        return f"x:{stem}"
    source_url = (item.get("source_url") or "").strip()
    if source_url:
        m = re.search(r"/(?:status|i/status)/(\d{15,20})(?:\b|/|\?)?", source_url)
        return f"x:{m.group(1)}" if m else f"url:{source_url}"
    for field in ["id", "tweet_id", "source_id"]:
        value = str(item.get(field) or "").strip()
        if value:
            return f"id:{value}"
    if stem:
        return f"stem:{stem}"
    return f"path:{canonical_key_for_item(item)}"


def record_score(item: dict) -> tuple:
    rel_path = item.get("relative_path") or item.get("path") or ""
    title = (item.get("title") or "").strip()
    summary = (item.get("summary") or "").strip()
    human_title = 0 if re.fullmatch(r"(?:tweet\s+)?\d{15,20}", title.lower()) else 1
    in_cards = 1 if rel_path.startswith("memory/cards/") or "/memory/cards/" in (item.get("path") or "") else 0
    return (
        1 if not item.get("excluded") else 0,
        1 if item.get("enriched") else 0,
        in_cards,
        1 if len(summary) >= 18 else 0,
        human_title,
        min(len(summary), 300),
        min(len(title), 120),
        int(item.get("mtime") or 0),
    )


def dedupe_records(items: list[dict]) -> tuple[list[dict], int]:
    by_identity: dict[str, dict] = {}
    removed = 0
    for item in items:
        ident = record_identity(item)
        current = by_identity.get(ident)
        if current is None or record_score(item) > record_score(current):
            if current is not None:
                removed += 1
            by_identity[ident] = item
        else:
            removed += 1
    return list(by_identity.values()), removed


records, deduped = dedupe_records(records)
records.sort(key=lambda x: x.get("relative_path", ""))
payload = {
    "version": "1.1",
    "bookmarks_dir": str(bookmarks_dir),
    "cards_dir": str(cards_dir),
    "count": len(records),
    "mode": status,
    "dedupe": {"removed": deduped},
    "items": records,
}

index_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"✅ index built: {index_file} ({len(records)} items, {status})")
PY
