"""Choose proactive evidence for the answering agent.

The agent that answers the turn already reads the whole conversation and is
the strongest judge of whether a piece of knowledge applies. XKB does the one
thing it cannot: search the library. Jev's per-turn relevance verdict and
local rules decide what to show; nothing here calls a generation model.

2026-10-06 取代了「先由 Sonnet 解出這輪需求、再由 Sonnet 挑段落」的做法。
那兩次呼叫每輪多等約 6 秒、每輪都付費，判斷的卻是回答者下一秒自己就會判斷
的事。對昨天 26 輪真實紀錄的重算：有內容的輪次，Sonnet 挑的段落幾乎都在 jev
前三名；它真正多做的是在「好 直接送」這類輪次保持安靜，而多送一兩條給回答者
的代價只是幾百字。
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata

import xkb_score
from xkb_evidence import IDENTITY_PREFIX, bounded_excerpt, fields, identity_key, render_context

DELIVERY_FLOOR = 0.5
MAX_SUGGESTIONS = 3
EXCERPT_CHARS = 900

# 歷史紀錄（每日筆記、其他對話）說的是「當時發生了什麼」，不是可重複使用的
# 知識，而且常常已經過時。2026-10-06 開發 XKB 時送出的 33 條有 27 條是這類：
# 半年前 Telegram 時期討論 XKB 的摘要，跟話題相關，卻在推薦已刪掉的腳本。
# 知識（卡片、wiki）先挑；歷史每輪最多一條，而且要明顯相關才送。用 10/5~10/6
# 的真實紀錄模擬：XKB 開發 43 條降到 17 條（知識 13），影片工作的 wiki 反而
# 多了 6 條，因為原本被對話紀錄占掉的名額空出來了。
HISTORY_TYPES = {"memory_note", "conversation_trace"}
HISTORY_FLOOR = 0.75
MAX_HISTORY = 1


def diagnostic(packet: dict) -> str:
    """Do not confuse unavailable judgement/retrieval with a quiet decision."""
    if ((packet.get("quality") or {}).get("status") == "degraded"
            or (packet.get("delivery") or {}).get("status") == "degraded"):
        return ("<xkb_recall_status>degraded: retrieval or relevance judgement is incomplete. "
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


# 書籤卡片有一批標題是推文 ID（例如 2044072293383712878），回答者光看標題
# 不知道那是什麼。標題沒有任何文字時改用內文第一句。
_SENTENCE_END = re.compile(r"[。！？!?]|\.(?:\s|$)|\n")


# 「Xkb Case 6769746875625f...」「Local Readme 064efe10」這類由前綴加雜湊組成的
# 標題，跟純數字一樣說不出內容。
_ID_TITLE = re.compile(r"[A-Za-z ]{0,16}(?=[0-9A-Fa-f_-]*\d)[0-9A-Fa-f_-]{8,}")


def display_title(record: dict) -> str:
    title, body, _ = fields(record)
    if any(c.isalpha() for c in title) and not _ID_TITLE.fullmatch(title.strip()):
        return title[:120]
    text = re.sub(r"^[#>\-\*\s]+", "", body.strip())
    match = _SENTENCE_END.search(text)
    first = text[:match.start()] if match else text
    return (first.strip()[:60] or title)[:120]


def excerpt(body: str, limit: int = EXCERPT_CHARS) -> str:
    """Whole paragraphs when they fit; otherwise a labelled cut at a sentence."""
    if len(body) <= limit:
        return body
    kept = bounded_excerpt(body, limit)
    if not kept:
        cut = body[:limit]
        ends = [m.end() for m in _SENTENCE_END.finditer(cut)]
        kept = cut[:ends[-1]].rstrip() if ends else cut.rstrip()
    return kept + "\n（節錄）"


def content_fingerprint(body: str) -> str:
    """Version of the delivered knowledge, independent of its identity.

    Taken from the normalized source body, before excerpting, so titles,
    whitespace and the excerpt label cannot lift session deduplication; an
    edited source can. Evidence identity keeps meaning "which document".
    """
    return hashlib.sha256(claim_text(body).encode("utf-8")).hexdigest()[:16]


def _versioned(key: str) -> bool:
    """Cards are retrieved as whichever chunk matched this turn, so their text
    is not a version: two chunks of one unedited card hash differently (11 of 75
    card identities on 2026-10-05). Wiki sections, notes and traces return one
    stable text per identity, and those are what gets edited mid-conversation."""
    return bool(key) and not key.startswith(IDENTITY_PREFIX + '["card"')


def select(records: list[dict], judge: dict, conversation: list[dict], *, query: str = "",
           delivered: frozenset = frozenset()) -> dict:
    """Pick at most three judged-relevant records the session has not seen.

    ``delivered`` holds what this session was already given: ``(key,
    fingerprint)`` pairs for versioned evidence, or a bare key (cards, and turns
    recorded before fingerprints existed) that blocks every version. A partial judgement still delivers its positive verdicts: the
    answering agent filters, and withholding them would make one failed batch
    erase everything. The packet is marked degraded so the gap stays visible.
    No adopted/rejected preference is inferred from presentation or silence.
    """
    # key -> versions given. None means "some version, unknown which": a card,
    # a turn recorded before fingerprints, or a pair whose evidence is now
    # unversioned. An unknown version on either side blocks, so a format
    # change at deploy cannot resend what this session already saw.
    given: dict[str, set] = {}
    for entry in delivered:
        key_, version = entry if isinstance(entry, tuple) else (entry, None)
        given.setdefault(key_, set()).add(version)
    status = judge.get("status")
    active = status in {"judged", "partial"}
    previous = [claim_text(m["content"]) for m in conversation if m["role"] == "assistant"]
    withheld, presented, eligible = [], [], []
    for index, record in enumerate(records):
        key = identity_key(record)
        body = fields(record)[1]
        claim = claim_text(body)
        fingerprint = content_fingerprint(body) if _versioned(key) else None
        in_reply = len(claim) >= 40 and any(claim in text for text in previous)
        if in_reply:
            presented.append(key)
        score = verdict(record) if active else None
        if score is None:
            reason = "unjudged"
        elif score < DELIVERY_FLOOR:
            reason = "weak_relevance"
        elif record.get("excerpt_truncated"):
            reason = "incomplete_source_excerpt"
        elif key in given and (fingerprint is None or None in given[key] or fingerprint in given[key]):
            reason = "delivered_earlier_in_session"
        elif in_reply:
            reason = "already_in_conversation"
        elif record.get("record_type") in HISTORY_TYPES and score < HISTORY_FLOOR:
            reason = "history_below_floor"
        else:
            reason = ""
        if reason:
            withheld.append((index, {"evidence_key": key, "reason": reason}))
            continue
        eligible.append((index, record, key, body, claim, fingerprint))
    # Knowledge first, then at most MAX_HISTORY history items for leftover slots.
    selected, seen, history = [], set(), 0
    for want_history in (False, True):
        for index, record, key, body, claim, fingerprint in eligible:
            is_history = record.get("record_type") in HISTORY_TYPES
            if is_history != want_history:
                continue
            if claim and claim in seen:
                reason = "duplicate_claim"
            elif len(selected) >= MAX_SUGGESTIONS:
                reason = "limit"
            elif is_history and history >= MAX_HISTORY:
                reason = "history_limit"
            else:
                reason = ""
            if reason:
                withheld.append((index, {"evidence_key": key, "reason": reason}))
                continue
            seen.add(claim)
            history += int(is_history)
            shown = {**record, "title": display_title(record), "summary": excerpt(body)}
            if fingerprint:
                shown["content_fingerprint"] = fingerprint
            selected.append(shown)
    withheld = [item for _, item in sorted(withheld, key=lambda pair: pair[0])]
    return {"mode": "suggest" if selected else "background" if records else "none",
            "policy": "jev-direct", "records": selected, "context": render_context(selected),
            "floor": DELIVERY_FLOOR, "limit": MAX_SUGGESTIONS, "withheld": withheld,
            "previously_presented": presented,
            "status": "degraded" if records and status != "judged" else "ready"}
