"""Choose proactive evidence without discarding the searchable candidate packet.

The Knowledge Service owns this decision for HTTP, MCP and the prompt hook.
Retrieval support and current-turn judgement are distinct ranking signals.
"""
from __future__ import annotations

import math
import re
import unicodedata

import xkb_score
import xkb_jev
import xkb_failures
from xkb_evidence import fields, identity_key, render_context
from xkb_recall import contextual_query

# Experimental decision boundary, fixed before held-out conversational testing.
# This is not a calibrated probability or a replacement for the recall floor.
DELIVERY_FLOOR = 0.5
MAX_SUGGESTIONS = 2
MAX_REVIEW = 4


def diagnostic(packet: dict) -> str:
    """Do not confuse unavailable judgement/retrieval with a quiet decision."""
    if ((packet.get("quality") or {}).get("status") == "degraded"
            or (packet.get("delivery") or {}).get("status") == "degraded"):
        return ("<xkb_recall_status>degraded: retrieval, relevance or intervention judgement is incomplete. "
                "Missing suggestions do not establish that no useful knowledge exists. "
                "Candidates may remain available in the service recall packet; this is an internal diagnostic, "
                "not a recommendation to the user.</xkb_recall_status>")
    return ""


def verdict(record: dict) -> float | None:
    value = record.get("judge")
    return float(value) if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 else None


def rank(records: list[dict], judge: dict) -> list[dict]:
    """Fuse every retrieval leg, then order judged slots by current usefulness.

    Unknown verdicts retain their retrieval positions during partial outages.
    The strongest judged representative carries the text and verdict for a fused
    identity. Anonymous evidence remains independent, as in xkb_score.rank.
    """
    active = judge.get("status") in {"judged", "partial"}
    if active:
        # Swap within each identity only. Global sorting here would change RRF
        # ties and move unknown evidence during a partial judge outage.
        records = list(records)
        first, best = {}, {}
        for index, record in enumerate(records):
            key = identity_key(record) or ("anonymous", index)
            first.setdefault(key, index)
            old = best.setdefault(key, index)
            score, previous = verdict(record), verdict(records[old])
            if score is not None and (previous is None or score > previous):
                best[key] = index
        for key, index in first.items():
            other = best[key]
            records[index], records[other] = records[other], records[index]
    fused = xkb_score.rank(records)
    if not active:
        return fused
    judged = iter(sorted((r for r in fused if verdict(r) is not None),
                         key=lambda r: (bool(r.get("demoted")), -verdict(r), -r.get("unified_score", 0))))
    return [next(judged) if verdict(r) is not None else r for r in fused]


def claim_text(value: str) -> str:
    # Exact text normalization only: keep numbers, negations and qualifiers.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def select(records: list[dict], judge: dict, conversation: list[dict], *, query: str) -> dict:
    """Return at most two useful additions; untouched records remain inspectable.

    A previous assistant answer is evidence of presentation, not adoption.
    Nothing here learns a negative preference from silence or skipped delivery.
    """
    selected, withheld, presented = [], [], []
    seen = set()
    previous = [claim_text(m["content"]) for m in conversation if m["role"] == "assistant"]
    active = judge.get("status") in {"judged", "partial"}
    review = [r for r in records if active and verdict(r) is not None and verdict(r) >= DELIVERY_FLOOR][:MAX_REVIEW]
    answers = None
    if query and review:
        try:
            answers = xkb_jev.intervention(contextual_query(query, conversation, for_judge=True),
                                          [{"source": source, "title": title, "text": body}
                                           for title, body, source in (fields(r) for r in review)])
        except Exception as err:  # Retrieval remains available if the intervention judge fails.
            xkb_failures.note("intervention judge", err)
    def answer(key):
        item = answers.get(key) if isinstance(answers, dict) else None
        return verdict({"judge": item.get("noul")}) if isinstance(item, dict) else None
    need = answer("need")
    scores = {id(r): answer(f"use_{i}") for i, r in enumerate(review)}
    applicability = {id(r): answer(f"applies_{i}") for i, r in enumerate(review)}
    positions = {id(r): i for i, r in enumerate(review)}
    unknown = bool(review) and (need is None or any(value is None for value in (*scores.values(), *applicability.values())))
    # Marginal usefulness, rather than relevance alone, determines the scarce
    # delivery slots. Unreviewed candidates remain in the original packet.
    ordered = sorted(records, key=lambda r: -(scores.get(id(r)) or 0))
    for record in ordered:
        body = claim_text(fields(record)[1])
        score = verdict(record) if active else None
        if len(body) >= 40 and any(body in answer for answer in previous):
            presented.append(identity_key(record))
        reason = ""
        if score is None:
            reason = "unjudged"
        elif score < DELIVERY_FLOOR:
            reason = "weak_current_need"
        elif need is None:
            reason = "intervention_unverified"
        elif need < DELIVERY_FLOOR:
            reason = "no_open_need"
        elif id(record) not in positions:
            reason = "review_budget"
        elif scores[id(record)] is None:
            reason = "usefulness_unverified"
        elif scores[id(record)] < DELIVERY_FLOOR:
            reason = "no_added_value"
        elif applicability[id(record)] is None:
            reason = "applicability_unverified"
        elif applicability[id(record)] < DELIVERY_FLOOR:
            reason = "wrong_applicability"
        elif body and body in seen:
            reason = "duplicate_claim"
        elif len(selected) >= MAX_SUGGESTIONS:
            reason = "delivery_budget"
        if not reason:
            for existing in selected:
                a, b = sorted((positions[id(existing)], positions[id(record)]))
                same = answer(f"same_{a}_{b}")
                if same is None:
                    unknown = True
                    reason = "overlap_unverified"
                    break
                if same >= DELIVERY_FLOOR:
                    reason = "redundant_advice"
                    break
        if reason:
            withheld.append({"evidence_key": identity_key(record), "reason": reason})
        else:
            selected.append(record)
            if body:
                seen.add(body)
    return {"mode": "suggest" if selected else "background" if records else "none",
            "records": selected, "context": render_context(selected),
            "floor": DELIVERY_FLOOR, "limit": MAX_SUGGESTIONS, "withheld": withheld,
            "previously_presented": presented,
            "intervention": {"need": need, "reviewed": len(review),
                             "state": ("not_attempted" if not review else "unknown" if need is None
                                       else "open" if need >= DELIVERY_FLOOR else "quiet"),
                             "overlap": [{"left": identity_key(review[j]), "right": identity_key(review[i]),
                                          "score": answer(f"same_{j}_{i}")}
                                         for i in range(len(review)) for j in range(i)],
                             "usefulness": [{"evidence_key": identity_key(r), "score": scores[id(r)],
                                             "applicability": applicability[id(r)]} for r in review]},
            "status": "degraded" if unknown or (records and judge.get("status") != "judged") else "ready"}
