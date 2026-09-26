"""Shared evidence identity and display fields across recall clients."""
from __future__ import annotations


def record_id(record: dict) -> str:
    return str(record.get("id") or record.get("trace_id") or record.get("source_file")
               or record.get("relative_path") or record.get("source_url") or "")


def fields(record: dict) -> tuple[str, str, str]:
    title = str(record.get("title") or record.get("query") or record.get("section")
                or record_id(record)).strip()
    body = str(record.get("summary") or record.get("answer") or record.get("excerpt") or "")
    source = str(record.get("source_url") or record.get("url") or record.get("trace_id")
                 or record.get("source_file") or record.get("relative_path") or record_id(record)).strip()
    return title, body, source


def render_context(records: list[dict]) -> str:
    entries = []
    for record in records:
        title, body, _ = fields(record)
        entries.append(f"[{record.get('record_type', 'knowledge')}] {title}\n{body}")
    return "\n\n".join(entries)
