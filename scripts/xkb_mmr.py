#!/usr/bin/env python3
"""Maximal Marginal Relevance selection for recall results.

Ported from Memmy (MemTensor/memmy-agent, MIT licence) —
``Memory/src/service/retrieval/retrieval-service.ts``, functions
``mmrRecallHits`` and ``recallTextSimilarity``.

Why this is here: wiki recall kept the single highest-scoring section per
topic page. That rule is blunt in both directions — it discards a second
section even when it says something entirely different, and it lets two
near-identical sections through as long as they sit on different pages.
Once governance began giving every promoted claim its own section, a single
page could hold hundreds, and the rule was throwing away nearly all of a
page's usable content on every query.

MMR replaces "one per page" with "keep it if it adds something new": each
pick maximises ``lambda * relevance - (1 - lambda) * redundancy`` measured
against what has already been selected.

Two deliberate departures from the original:

* Memmy skips the redundancy term between two UserMemory hits, because two
  things the user said are never redundant with each other. XKB has no
  equivalent layer, so that special case is dropped rather than guessed at.
* Scores are normalised here. Memmy's channels already produce comparable
  relevance; XKB's callers do not — wiki recall counts token hits (6 per
  title match, 2 per body match, unbounded), while vector recall produces
  cosine in 0..1. Feeding a count of 60 into ``0.7 * score - 0.3 * redundancy``
  makes the redundancy term irrelevant and MMR a no-op. Mixing score scales
  is a mistake this repository has made three times, so the normalisation is
  part of the function rather than left to the caller to remember.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Sequence

# Each Han character is its own term: Chinese is unspaced, and a segmenter
# would be a dependency and a source of disagreement between callers. ASCII
# runs of two or more characters keep identifiers like "gpt-image-2" whole.
_TERM_RE = re.compile(r"[一-鿿]|[a-z0-9_:-]{2,}")

DEFAULT_LAMBDA = 0.7


def text_terms(value: str) -> set[str]:
    return set(_TERM_RE.findall(value.lower()))


def text_similarity(left: str, right: str) -> float:
    """Term overlap over the larger of the two term sets, in 0..1."""
    a = text_terms(left)
    b = text_terms(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def _normalised(scores: Sequence[float]) -> list[float]:
    """Map scores onto 0..1 so the redundancy term can actually compete.

    Anchored at zero rather than at the lowest candidate. Min-max looks like
    the obvious choice and is wrong here: it forces the weakest candidate to
    0 however close it actually was, so three results scoring 60, 58 and 56
    become 1.0, 0.5 and 0.0 and the redundancy term is swamped again — the
    same failure the normalisation exists to prevent, one step further along.
    Anchoring at zero keeps them at 1.0, 0.97 and 0.93, which is what they
    are. The floor moves only when a penalty has pushed a score below zero.
    """
    if not scores:
        return []
    low = min(0.0, min(scores))
    high = max(scores)
    span = high - low
    if span <= 0:
        return [1.0] * len(scores)
    return [(score - low) / span for score in scores]


def mmr_select(
    items: Sequence[Any],
    limit: int,
    *,
    text: Callable[[Any], str],
    score: Callable[[Any], float] = lambda item: float(item["score"]),
    lambda_: float = DEFAULT_LAMBDA,
) -> list[Any]:
    """Pick up to ``limit`` items, trading relevance against redundancy.

    ``lambda_`` of 1.0 is pure relevance — the original ranking. 0.0 is pure
    diversity. The default follows Memmy's 0.7.
    """
    if limit <= 0 or not items:
        return []
    lambda_ = min(1.0, max(0.0, lambda_))

    pool = list(items)
    relevance = dict(zip(map(id, pool), _normalised([float(score(i)) for i in pool])))
    texts = {id(item): text(item) for item in pool}

    selected: list[Any] = []
    while pool and len(selected) < limit:
        best_index = 0
        best_value = float("-inf")
        for index, candidate in enumerate(pool):
            redundancy = max(
                (text_similarity(texts[id(candidate)], texts[id(prior)]) for prior in selected),
                default=0.0,
            )
            value = lambda_ * relevance[id(candidate)] - (1.0 - lambda_) * redundancy
            if value > best_value:
                best_value = value
                best_index = index
        selected.append(pool.pop(best_index))
    return selected
