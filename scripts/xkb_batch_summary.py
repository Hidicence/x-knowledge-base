#!/usr/bin/env python3
"""從 worker 的輸出讀出「這一次做了什麼」——而且讀不出來時要說。

原本這件事是 shell 裡的一行 sed：

    done_count=$(sed -n 's/.*done=\\([0-9][0-9]*\\).*/\\1/p' "$OUT" | tail -1)

它抓的是「最後一個含 done= 的數字」，而輸出裡有兩行含 done=：

    ✅ queue synced: ... (1591 items, todo=0, done=1575)   ← 佇列的累計總數
    📊 done=0  skipped=0  failed=9  remaining todo=0        ← 這一次的結果

平常第二行在後面，tail -1 剛好對。但佇列沒東西可做時 worker 印的是
「✅ No todo items found」，根本沒有第二行——於是這一次的產出被讀成 1575，
摘要說「產了 1575 張卡」，而且因為 done_count > 0，還會多跑一次向量索引。
2026-09-10 帳本接上去的第一次實跑就撞到這個。

錯的不只是那個正則，是「讀不出來就當 0」這個預設。讀不出結果和產出 0 是
兩件完全不同的事，一個是壞了、一個是沒事做，而 `${done_count:-0}` 讓它們
長得一模一樣——正是這整條管線八天沒被發現的那個毛病。

所以這裡只認 worker 自己的那行摘要（用 `remaining todo=` 認，那是語意不是
裝飾），並且把三種情況分開回報：

    done=N failed=N outcome=ran        讀到了
    done=0 failed=0 outcome=idle       worker 說沒東西可做
    (exit 2)                           有輸出但讀不出結果——呼叫端要當成故障

用法：
    python3 scripts/xkb_batch_summary.py < worker-output.txt
"""
from __future__ import annotations

import re
import sys

# worker 的摘要行。用 `remaining todo=` 當錨點：佇列同步那行也有 done= 與
# todo=，但沒有 remaining。
_SUMMARY = re.compile(r"remaining\s+todo=")
_DONE = re.compile(r"\bdone=(\d+)")
_FAILED = re.compile(r"\bfailed=(\d+)")

# worker 在無事可做時說的話。這是「這次沒事做」，不是「讀不出來」。
_IDLE = re.compile(r"No todo items found|沒有待處理")


def parse(text: str) -> tuple[int, int, str] | None:
    """回 (done, failed, outcome)；讀不出結果時回 None。"""
    summary = [line for line in text.splitlines() if _SUMMARY.search(line)]
    if summary:
        line = summary[-1]
        done = _DONE.search(line)
        failed = _FAILED.search(line)
        if done:
            return int(done.group(1)), int(failed.group(1)) if failed else 0, "ran"
        return None
    if _IDLE.search(text):
        return 0, 0, "idle"
    return None


def main() -> int:
    parsed = parse(sys.stdin.read())
    if parsed is None:
        print("outcome=unreadable", file=sys.stderr)
        return 2
    done, failed, outcome = parsed
    print(f"done={done} failed={failed} outcome={outcome}")
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
