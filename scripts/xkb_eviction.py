"""Conservative demotion from explicit relevance verdicts, never age.

The experimental gain/EMA policy had no production caller or valid control
group and was retired. Git preserves that experiment. A relevant verdict can
revive a record even if the response limit prevented returning it this time.
"""
from __future__ import annotations

DEMOTE_AFTER_CONSIDERED = 5


def is_demoted(judged: int, relevant: int,
               *, after: int = DEMOTE_AFTER_CONSIDERED) -> bool:
    """Repeated negative verdicts without a positive verdict permit demotion."""
    return relevant <= 0 and judged >= max(1, after)
