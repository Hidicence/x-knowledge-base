#!/usr/bin/env python3
"""讓腳本的輸出在任何主控台都印得出來。

XKB 的輸出到處都有 emoji（📚 ✅ ❌）。在 UTF-8 的終端機沒問題，在 Windows 的
cp950 主控台會直接拋 UnicodeEncodeError——而拋出的位置是 print，也就是腳本做完
了事情、正要回報的時候。結果是工作做完了卻以例外結束。

2026-09-11 實測：`build_vector_index.py` 在不帶 PYTHONUTF8 的 Windows 主控台上
就是這樣死在第 353 行的 `print(f"📚 Loaded ...")`。

這件事被當成「環境問題」很久了，做法是要求每個呼叫端帶 PYTHONUTF8=1。那不是
修好，那是把責任推給呼叫端——而且沒帶的人得到的不是「請設環境變數」，是一個
看起來像程式壞掉的 traceback。

呼叫端該做的只有一行：

    import xkb_console; xkb_console.use_utf8()

errors="replace" 是刻意的：印不出來的字元換成替代字元，不要讓一個字元終止整個
回報。回報本身不該是可以失敗的東西。
"""
from __future__ import annotations

import sys


def use_utf8() -> None:
    """把 stdout / stderr 轉成 UTF-8。已經是 UTF-8 或不支援時安靜跳過。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # 被包起來的 stream（測試、pipe）不一定有這個方法
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # 轉不過去就算了。這支模組的職責是讓輸出能印出來，
            # 不是在輸出之前先失敗一次。
            pass
