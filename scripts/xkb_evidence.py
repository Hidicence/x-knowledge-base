"""Shared evidence identity and display fields across recall clients."""
from __future__ import annotations
import json

IDENTITY_PREFIX = "ev1:"


def identity_key(record: dict) -> str:
    """Stable document/section identity shared by ranking and usage accounting.

    Display titles are never identities. Explicit IDs win over URLs, so two
    distinct records citing one page do not accidentally erase each other.
    Anonymous evidence stays unmerged and is not assigned persistent counters.
    """
    resource = str(record.get("id") or record.get("trace_id") or record.get("source_file")
                   or record.get("relative_path") or record.get("source_url") or record.get("url") or "")
    if not resource:
        return ""
    kind = str(record.get("record_type") or record.get("source_type") or "")
    path = str(record.get("source_file") or resource).replace("\\", "/")
    section = str(record.get("section") or "")
    if record.get("trace_id") or kind in {"conversation_trace", "conversation"}:
        family, resource, section = "conversation", str(record.get("trace_id") or resource), ""
    elif kind in {"wiki_topic", "wiki", "wiki_semantic", "memory_note", "memory", "memory_semantic"} or path.startswith(("wiki/", "memory/")) and not path.startswith("memory/cards/"):
        family = "document"
        resource, _, fragment = path.partition("#")
        section = section or fragment
        if kind in {"wiki_topic", "wiki", "wiki_semantic"} and "/" not in resource:
            resource = "wiki/topics/" + resource.removesuffix(".md") + ".md"
        elif resource.startswith("wiki/topics/") and not resource.endswith(".md"):
            resource += ".md"
    elif kind in {"action", "contrarian"}:
        family = kind
    else:
        family, section = "card", ""
        for prefix in ("memory/cards/", "cards/"):
            if resource.startswith(prefix):
                resource = resource[len(prefix):].removesuffix(".md")
                break
    return IDENTITY_PREFIX + json.dumps([family, record.get("namespace") or "private", resource, section],
                                        ensure_ascii=False, separators=(",", ":"))


def identity_source(key: str) -> str:
    """Recover the original document/trace reference for human-facing reports."""
    return json.loads(key[len(IDENTITY_PREFIX):])[2] if key.startswith(IDENTITY_PREFIX) else key


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
