#!/usr/bin/env python3
"""
召回層回歸測試

分兩組，判定標準不同：

「該安靜」——**所有人都適用**，而且是失敗會直接讓人關掉這個工具的那一組。
召回層掛在每一句話上，一旦開始對閒聊、翻譯、算數學插嘴就沒救了。
這組任何一句冒出結果都算失敗。

「該召回」——**取決於知識庫裡有什麼**。預設只回報、不算失敗，
因為剛裝好、還沒吸收任何東西的知識庫本來就撈不到東西，
那不是程式壞掉。要把它當硬性條件請加 --strict。
案例可用 XKB_RECALL_CASES 指向自己的 JSON（{"should_recall": [...]}）。

這組案例對應 2026-07-28 修掉的三個靜默故障：
  - 中文查詢（原本斷詞把整串中文當成一個詞，分數永遠是 0）
  - 卡片來源（原本 router 把卡片結果整包丟掉）
  - hard trigger 的回想問法（原本規則白名單漏接，且不查卡片）

Usage:
    python3 scripts/test_recall_regression.py
    python3 scripts/test_recall_regression.py --strict    # 該召回也當硬性條件
    python3 scripts/test_recall_regression.py --verbose   # 印出實際撈到什麼
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROUTER = SCRIPTS_DIR / "recall_router.py"

# 預設案例貼著作者自己的知識庫（影像製作、視覺 AI、碳盤查）。
# 換個知識庫就該換一組——用 XKB_RECALL_CASES 指到自己的 JSON。
DEFAULT_SHOULD_RECALL = [
    "之前我們怎麼處理碳盤查的",
    "我想做一支產品廣告影片",
    "seedance 工作流程有沒有案例",
    "XKB 下一步是什麼",
    "storyboard 要怎麼分鏡",
    "gpt image 2 的人像 prompt 有什麼技巧",
    "上次那個客戶的報價怎麼算的",
]

# 這組與知識庫內容無關，任何安裝都該通過
DEFAULT_SHOULD_STAY_QUIET = [
    "早安",
    "幫我翻譯這段",
    "今天天氣真好想出去走走",
    "幫我算一下 3000 乘以 12",
    "晚點再說吧我先去吃飯",
    "幫我訂一下明天的餐廳",
    "ok 收到",
    "寫一個 function 把字串反轉",
]


def load_cases() -> tuple[list[str], list[str]]:
    path = os.getenv("XKB_RECALL_CASES")
    if not path:
        return DEFAULT_SHOULD_RECALL, DEFAULT_SHOULD_STAY_QUIET
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return (
        data.get("should_recall", DEFAULT_SHOULD_RECALL),
        data.get("should_stay_quiet", DEFAULT_SHOULD_STAY_QUIET),
    )


def run_one(message: str) -> tuple[dict, float]:
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(ROUTER), "--json", message],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    if proc.returncode != 0:
        raise RuntimeError(f"router exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout), elapsed_ms


def main() -> int:
    parser = argparse.ArgumentParser(description="Recall layer regression test")
    parser.add_argument("--verbose", action="store_true", help="show what each query actually returned")
    parser.add_argument("--strict", action="store_true",
                        help="「該召回」撈不到也算失敗（需要知識庫裡真的有對應內容）")
    args = parser.parse_args()

    should_recall, should_stay_quiet = load_cases()
    failures = 0
    misses = 0
    slowest = 0.0

    for label, cases, want_results in (
        ("該召回（取決於知識庫內容）", should_recall, True),
        ("該安靜（所有安裝都適用）", should_stay_quiet, False),
    ):
        print(f"── {label} ──")
        for message in cases:
            try:
                result, elapsed_ms = run_one(message)
            except Exception as exc:
                print(f"  ERROR  {message}\n         {exc}")
                failures += 1
                continue

            count = len(result["results"])
            passed = (count > 0) if want_results else (count == 0)
            slowest = max(slowest, elapsed_ms)

            if passed:
                mark = "ok  "
            elif want_results and not args.strict:
                mark = "miss"          # 知識庫沒這方面的內容，不算程式壞掉
                misses += 1
            else:
                mark = "FAIL"
                failures += 1

            print(f"  {mark}  {elapsed_ms:6.0f}ms  {result['trigger_class']:8} n={count}  {message}")
            if args.verbose and count:
                for r in result["results"]:
                    print(f"           - {r['source_type']:10} {r['source_file']}")
        print()

    total = len(should_recall) + len(should_stay_quiet)
    print(f"{total - failures - misses}/{total} 通過   最慢 {slowest:.0f}ms")
    if misses:
        print(f"{misses} 句沒撈到——知識庫裡沒有對應內容，不計為失敗（要當硬性條件用 --strict）")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
