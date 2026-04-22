"""
XKB Session Deduplication
=========================
Tracks which (source_file, section) pairs have already been surfaced
in the current session, so the same content is not pushed repeatedly.

Session = a temp JSON file at /tmp/xkb-session-shown.json.
TTL = 4 hours. After that, the session resets automatically.

Usage:
    from _session_dedup import filter_new, mark_shown, clear_session
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

SESSION_FILE = Path(os.getenv("XKB_SESSION_FILE", "/tmp/xkb-session-shown.json"))
SESSION_TTL_HOURS = int(os.getenv("XKB_SESSION_TTL_HOURS", "4"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> dict:
    """Load session state. Returns empty session if missing or expired."""
    if not SESSION_FILE.exists():
        return {"started_at": _now().isoformat(), "shown": []}
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        started = datetime.fromisoformat(data["started_at"])
        if _now() - started > timedelta(hours=SESSION_TTL_HOURS):
            return {"started_at": _now().isoformat(), "shown": []}
        return data
    except Exception:
        return {"started_at": _now().isoformat(), "shown": []}


def _save(data: dict) -> None:
    try:
        SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # session dedup never breaks the main flow


def _key(source_file: str, section: str) -> str:
    return f"{source_file.split('/')[-1]}::{section}"


def filter_new(results: list) -> tuple[list, list]:
    """
    Split results into (new_results, already_seen_results).
    Works with any objects that have .source_file and .section attributes,
    or dicts with 'source_file' and 'section' keys.
    """
    data = _load()
    shown_keys = {entry["key"] for entry in data.get("shown", [])}

    new_results = []
    already_seen = []
    for r in results:
        if isinstance(r, dict):
            sf = r.get("source_file", "")
            sec = r.get("section", "")
        else:
            sf = getattr(r, "source_file", "")
            sec = getattr(r, "section", "")
        k = _key(sf, sec)
        if k in shown_keys:
            already_seen.append(r)
        else:
            new_results.append(r)

    return new_results, already_seen


def mark_shown(results: list) -> None:
    """Record results as shown in the current session."""
    if not results:
        return
    data = _load()
    shown = data.get("shown", [])
    existing_keys = {entry["key"] for entry in shown}
    ts = _now().isoformat()

    for r in results:
        if isinstance(r, dict):
            sf = r.get("source_file", "")
            sec = r.get("section", "")
        else:
            sf = getattr(r, "source_file", "")
            sec = getattr(r, "section", "")
        k = _key(sf, sec)
        if k not in existing_keys:
            shown.append({"key": k, "ts": ts})
            existing_keys.add(k)

    data["shown"] = shown
    _save(data)


def clear_session() -> None:
    """Force reset the session (e.g. when user starts a new conversation)."""
    _save({"started_at": _now().isoformat(), "shown": []})


def session_stats() -> dict:
    """Return current session info for debugging."""
    data = _load()
    started = datetime.fromisoformat(data["started_at"])
    age_minutes = int((_now() - started).total_seconds() / 60)
    return {
        "started_at": data["started_at"],
        "age_minutes": age_minutes,
        "shown_count": len(data.get("shown", [])),
        "ttl_hours": SESSION_TTL_HOURS,
    }
