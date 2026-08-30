#!/usr/bin/env python3
"""One definition of "this came from us, not from the world".

Knowledge distilled from Pan's own notes and conversations is down-weighted
at recall, so that collecting an idea, recalling it, discussing it, and
distilling it again cannot quietly turn one opinion into a consensus. The
mechanism has existed since 2026-07-13 and works by looking for a marker in
the text.

It stopped working the moment a third writer appeared. ``distill_memory_to_wiki``
writes ``*(self-derived · memory/2026-08-03.md)*`` and is recognised;
``xkb_review`` promotion wrote ``*(source: 2026-08-02-evening-candidates.md#65)*``
and was not, so 913 self-derived claims went into the wiki competing at full
weight against external evidence — exactly the echo chamber the penalty
exists to prevent.

The writers and the reader now share this module. Adding a fourth writer
means calling ``annotate`` rather than inventing a fourth format, and the
health check fails if a governance-written line is missing the marker.
"""
from __future__ import annotations

import re

MARKER = "self-derived"

# Governance stamps every line it promotes with the candidate's fingerprint, so
# a batch stays reversible. It is bookkeeping, and readers should never see it.
# Excerpts are cut to length before anyone strips them, so the tail of a
# marker can arrive without its closing "-->". Match that too, or the
# reader gets "<!-- xkb-candi" instead of nothing.
CANDIDATE_MARKER_RE = re.compile(r"\s*<!--\s*xkb-candidate:[0-9a-f]*\s*(?:-->|\Z)")


def candidate_marker(candidate_id: str) -> str:
    return f"xkb-candidate:{candidate_id}"


def strip_markers(text: str) -> str:
    """Remove the bookkeeping a person is not meant to read."""
    return CANDIDATE_MARKER_RE.sub("", text or "").rstrip()

# Also matches the older distillation style, which names a memory file
# directly instead of carrying the marker.
SELF_DERIVED_RE = re.compile(r"self-derived|\(memory/\d{4}-\d{2}-\d{2}")


def annotate(source: str) -> str:
    """Render the trailing provenance note for a self-derived wiki line."""
    return f"*({MARKER} · source: {source})*"


def is_self_derived(text: str) -> bool:
    return bool(SELF_DERIVED_RE.search(text or ""))
