#!/usr/bin/env python3
"""One way for a swallowed failure to leave a trace.

Forty-one places in this codebase catch a broad exception and continue. Most
of them are right to: a file that will not read while scanning nine hundred, an
optional enrichment, a telemetry write. XKB must never break a conversation
because a hint could not be produced.

The danger is not that they are silent. It is that they are *identical* from
the outside. "The semantic backend is down" and "there is nothing on this
subject" both arrive as an empty list, so a recall system that had been broken
for twelve weeks kept answering, politely, that it knew nothing. A corrupt
index and an empty index both arrive as `[]`, so a worker re-processes
everything or skips deduplication with no complaint.

The fix is not to raise — that would break the conversation the handler exists
to protect. It is to make the two cases *distinguishable*: keep the fallback,
and say on stderr that it happened. A log line costs nothing when everything
works and is the whole difference when it does not.

Deliberately tiny, and deliberately incapable of raising: a failure reporter
that can itself fail is worse than none.
"""
from __future__ import annotations

import os
import sys

# 同一個地方壞一百次，只需要說一次；掃九百個檔案時，每個都印會把有用的訊息淹掉。
_seen: set[str] = set()

# 說過的和發生過的是兩件事。_seen 管「還要不要再印」，這一份管「這次跑的時候
# 有哪些地方退回了 fallback」——後者要能被附進紀錄，即使一個字都沒印出來
# （XKB_QUIET_FAILURES=1 或同一個地方第二次）。
_degraded: set[str] = set()

QUIET = os.getenv("XKB_QUIET_FAILURES") == "1"


def note(where: str, err: BaseException, *, detail: str = "", repeat: bool = False) -> None:
    """Record that a fallback was taken, and why.

    Args:
        where: 哪個功能退回了 fallback，例如 "semantic recall"。
        err: 被吞掉的例外。
        detail: 補充脈絡，例如出問題的檔名。
        repeat: 預設同一個地方只說一次；批次外層想每次都說時設 True。
    """
    try:
        key = f"{where}:{type(err).__name__}"
        # 先記下來再決定要不要印。印不印是給人看的事，記不記是給紀錄看的事，
        # 而這兩件事原本綁在一起：安靜模式或第二次發生時，連「發生過」都留不下。
        _degraded.add(where)
        if not repeat:
            if key in _seen:
                return
            _seen.add(key)
        if QUIET:
            return
        suffix = f" ({detail})" if detail else ""
        print(f"[xkb] {where} 退回 fallback：{type(err).__name__}: {err}{suffix}",
              file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — 報告失敗的東西自己不能失敗
        pass


def taken() -> list[str]:
    """這個行程到目前為止，哪些地方退回了 fallback。

    stderr 上說一次，對「有人正在看終端機」的情境夠了。召回不是那種情境：
    它跑在 MCP 伺服器或 hook 裡，stderr 沒有人讀，而呼叫端拿到的是一個空
    清單——跟「這個主題確實沒有東西」一模一樣。

    所以 note() 除了印出來，還要留下可以被附進紀錄的痕跡。召回的 telemetry
    帶上這個清單之後，「查無資料」和「語意後端逾時」才在事後分得出來。
    回傳的是 where 而不是例外內容：紀錄要能拿來統計，不是拿來讀堆疊。
    """
    return sorted(_degraded)


def reset() -> None:
    """測試用：清掉「已經說過」與「發生過」的記錄。"""
    _seen.clear()
    _degraded.clear()
