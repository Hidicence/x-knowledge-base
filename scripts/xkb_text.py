#!/usr/bin/env python3
"""
XKB 共用斷詞

原本各個 recall 模組各自帶一份 `re.findall(r"[A-Za-z0-9_\\-]{2,}|[\\u4e00-\\u9fff]{1,}", ...)`。
那個規則對英文沒問題，對中文是壞的：`[\\u4e00-\\u9fff]{1,}` 會貪婪地把整串中文吃成
**一個** token。「我想做一支產品廣告影片」變成單一個詞，而知識庫裡不會有任何一段文字
剛好等於這一整串，於是分數是 0——中文問句幾乎不可能命中任何東西。

中文沒有空格，沒有斷詞器就用 n-gram：把中文串切成 2 字與 3 字的滑動視窗。
這是最便宜的做法，不需要額外套件，對「影片」「分鏡」「報價」這種詞的召回已經夠用。

Usage:
    from xkb_text import tokenize
    tokenize("我想做一支產品廣告影片", STOPWORDS)
"""
from __future__ import annotations

import re
from typing import Iterable

_RUN_RE = re.compile(r"[A-Za-z0-9_\-]{2,}|[一-鿿]+")
_CJK_RE = re.compile(r"^[一-鿿]+$")

# 中文 n-gram 的長度。2 字抓得到「影片」「分鏡」，3 字抓得到「工作流」。
CJK_NGRAMS = (2, 3)


def tokenize(text: str, stopwords: Iterable[str] = (), min_len: int = 2) -> list[str]:
    """切成可比對的 token。英文照原樣，中文切 n-gram。

    保持出現順序並去重——呼叫端的 phrase bonus 依賴相鄰順序。
    """
    stops = set(stopwords)
    tokens: list[str] = []

    for run in _RUN_RE.findall(text.lower()):
        if not _CJK_RE.match(run):
            tokens.append(run)
            continue

        if len(run) <= max(CJK_NGRAMS):
            tokens.append(run)
        for n in CJK_NGRAMS:
            for i in range(len(run) - n + 1):
                tokens.append(run[i:i + n])

    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if len(token) < min_len or token in stops or token in seen:
            continue
        # 整個 n-gram 都由停用字組成（「我想」「的是」）：是雜訊，不是主題
        if _CJK_RE.match(token) and all(ch in stops for ch in token):
            continue
        seen.add(token)
        out.append(token)
    return out


def _units(tokens: Iterable[str]) -> set[str]:
    """把 token 換算成計分單位：中文算「字」，英文算「詞」。"""
    units: set[str] = set()
    for token in tokens:
        if _CJK_RE.match(token):
            units.update(token)
        else:
            units.add(token)
    return units


def overlap_score(tokens: list[str], text: str) -> float:
    """查詢有多少比例真的出現在 text 裡，0.0 ~ 1.0。

    不能直接用 `命中數 / token 數`：中文切 n-gram 會產生大量無意義的組合
    （「支產」「品廣」），它們永遠不會命中，卻會把分母撐大，把真正命中的
    「產品」「影片」稀釋到門檻以下——結果就是中文查詢永遠差一點點，
    看起來像「知識庫沒東西」。

    改成用「單位」計算：中文以字為單位，重疊的 n-gram 不會重複計分，
    雜訊 n-gram 也不會膨脹分母。
    """
    if not tokens or not text:
        return 0.0
    text_lower = text.lower()
    matched = [t for t in tokens if t in text_lower]
    if not matched:
        return 0.0
    return len(_units(matched)) / max(len(_units(tokens)), 1)


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:] or ["我想做一支產品廣告影片"]:
        print(f"{arg!r}\n  → {tokenize(arg)}")

# ── Redundancy ───────────────────────────────────────────────────────────────
# A second way to compare text, for a different question. ``tokenize`` asks
# "does this text match the query", and its 2/3-character n-grams are built for
# precision. ``similarity`` asks "has this already been said", where precision
# is the wrong instinct: two rephrasings of one claim are redundant, and the
# n-grams score them at 0.39 while single characters score them at 0.73.
#
# Ported from Memmy's recallTextSimilarity (MemTensor/memmy-agent, MIT). It
# lives here rather than beside its one caller so that the next thing needing
# "are these two texts saying the same thing" finds it instead of writing a
# ninth tokeniser.
_TERM_RE = re.compile(r"[一-鿿]|[a-z0-9_:-]{2,}")


def terms(value: str) -> set[str]:
    """Comparison terms:每個漢字自成一詞, plus ASCII runs of two or more."""
    return set(_TERM_RE.findall(value.lower()))


def similarity(left: str, right: str) -> float:
    """Term overlap over the larger term set, in 0..1."""
    a = terms(left)
    b = terms(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))
