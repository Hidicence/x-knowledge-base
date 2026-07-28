#!/usr/bin/env python3
"""
召回層回歸測試

分兩組：該召回的必須有結果，該安靜的必須完全沒有結果。

「該安靜」那組比「該召回」更重要——召回層是掛在每一句話上的，
一旦開始對閒聊、翻譯、算數學插嘴，使用者會直接把它關掉。

這組案例對應 2026-07-28 修掉的三個靜默故障：
  - 中文查詢（原本斷詞把整串中文當成一個詞，分數永遠是 0）
  - 卡片來源（原本 router 把卡片結果整包丟掉）
  - hard trigger 的回想問法（原本規則白名單漏接，且不查卡片）

Usage:
    python3 scripts/test_recall_regression.py
    python3 scripts/test_recall_regression.py --verbose   # 印出實際撈到什麼
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROUTER = SCRIPTS_DIR / "recall_router.py"

SHOULD_RECALL = [
    "之前我們怎麼處理碳盤查的",
    "我想做一支產品廣告影片",
    "seedance 工作流程有沒有案例",
    "XKB 下一步是什麼",
    "storyboard 要怎麼分鏡",
    "gpt image 2 的人像 prompt 有什麼技巧",
    "上次那個客戶的報價怎麼算的",
]

SHOULD_STAY_QUIET = [
    "早安",
    "幫我翻譯這段",
    "今天天氣真好想出去走走",
    "幫我算一下 3000 乘以 12",
    "晚點再說吧我先去吃飯",
    "幫我訂一下明天的餐廳",
    "ok 收到",
    "寫一個 function 把字串反轉",
]


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
    args = parser.parse_args()

    failures = 0
    slowest = 0.0

    for label, cases, want_results in (
        ("該召回", SHOULD_RECALL, True),
        ("該安靜", SHOULD_STAY_QUIET, False),
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
            failures += 0 if passed else 1
            slowest = max(slowest, elapsed_ms)

            mark = "ok  " if passed else "FAIL"
            print(f"  {mark}  {elapsed_ms:6.0f}ms  {result['trigger_class']:8} n={count}  {message}")
            if args.verbose and count:
                for r in result["results"]:
                    print(f"           - {r['source_type']:10} {r['source_file']}")
        print()

    total = len(SHOULD_RECALL) + len(SHOULD_STAY_QUIET)
    print(f"{total - failures}/{total} 通過   最慢 {slowest:.0f}ms")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
