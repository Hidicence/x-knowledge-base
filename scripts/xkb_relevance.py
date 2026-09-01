#!/usr/bin/env python3
"""判斷「這筆結果到底相不相關」——全專案唯一的一份。

為什麼要有這支：

    混合檢索（gbrain / XBrain）回傳的 score 是 **RRF 排名分數**。
    它說的是「這筆排第幾」，不是「這筆多相關」。
    第一名永遠落在 0.88 附近，即使整個知識庫對這個主題一無所知。

    實測（2026-08-02，1,316 張卡片）：
        「今天天氣如何」      → 0.863
        「碳盤查的計算方式」  → 0.862
    分數分不出這兩者，所以拿它當相關度過濾等於沒過濾。

這個教訓在 2026-07 於 recall_router 學過一次、寫成註解，
然後 2026-08 的 knowledge service 又原封不動踩了同一個坑——
因為註解只存在一個檔案裡，下一支腳本不會知道。

所以判斷邏輯與門檻都收在這裡。要改門檻改這裡，不要在呼叫端另立一個。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_failures

# 真實餘弦相似度的門檻。低於它就不值得送到使用者面前。
#
# 0.55 是實測出來的分水嶺：知識庫真的有內容的主題落在 0.60 以上，
# 完全沒有內容的落在 0.55 以下（碳盤查 0.546 vs XKB 架構 0.604）。
# 這個邊界很窄，換一個知識庫或換 embedding 模型都要重新量。
#
# 舊的環境變數名稱一併接受，免得既有部署突然改變行為。
DEFAULT_MIN_SIMILARITY = 0.55


def min_similarity() -> float:
    for name in ("XKB_MIN_SIMILARITY", "XKB_SERVICE_MIN_RELEVANCE",
                 "XKB_CARD_MIN_SIMILARITY", "XKB_ABSORB_MIN_RELEVANCE"):
        raw = os.getenv(name)
        if raw:
            try:
                return float(raw)
            except ValueError:
                continue
    return DEFAULT_MIN_SIMILARITY


def vector_key(record_id: str) -> str:
    """把各種來源的識別字串對到向量索引用的鍵。

    檢索後端回傳 slug（`01-topic/12345`），向量索引用的是卡片相對路徑
    （`01-topic/12345.md`）。網址與合成 id 不對應任何卡片，回空字串代表
    「查不到，不要據此過濾」。
    """
    record_id = (record_id or "").strip()
    if not record_id or record_id.startswith(("http://", "https://", "semantic:")):
        return ""
    return record_id if record_id.endswith(".md") else f"{record_id}.md"


def similarities(query: str, keys: Iterable[str]) -> dict[str, float] | None:
    """回傳每個鍵與問題的真實相似度；None 代表無法判斷。

    無法判斷與「全部不相關」是兩件完全不同的事，呼叫端必須分開處理——
    把兩者混為一談，就會在索引壞掉時把所有結果靜靜刪光。
    """
    keys = [k for k in keys if k]
    if not keys:
        return {}
    try:
        from continuity_recall import card_similarities
    except ImportError:
        return None
    try:
        return card_similarities(query, keys)
    except Exception as err:
        # None 的意思是「算不出來」，呼叫端會因此不做過濾——
        # 那跟「算出來大家都相關」是完全不同的狀況。
        xkb_failures.note("relevance scoring", err)
        return None


def filter_irrelevant(
    query: str,
    items: list[dict[str, Any]],
    *,
    key_of: Callable[[dict[str, Any]], str],
    threshold: float | None = None,
    rewrite_score: bool = True,
) -> tuple[list[dict[str, Any]], int, dict[str, float]]:
    """丟掉真實相似度低於門檻的項目。

    回傳 (保留的項目, 丟掉幾筆, 各鍵的相似度)。

    判斷不出來時原樣放行。索引沒建好或拿不到 embedding 是設定問題，
    不該表現成「你的知識庫裡沒有東西」——那正是這個專案吃過大虧的
    那種靜默失敗。

    `rewrite_score=True` 會把 score 換成實測相似度，原本的排名分數
    移到 `rank_score`，讓呼叫端不會再誤用它。
    """
    if not items:
        return items, 0, {}
    limit = min_similarity() if threshold is None else threshold
    keys = {id(item): vector_key(key_of(item)) for item in items}
    scores = similarities(query, keys.values())
    if not scores:
        return items, 0, {}

    kept: list[dict[str, Any]] = []
    for item in items:
        similarity = scores.get(keys[id(item)])
        if similarity is None:
            # 判斷不出來 → 放行，但要記住它沒有被驗證過。過濾器不能刪掉
            # 它判斷不了的東西，但也不該讓它排在真的比對過的前面。
            item["_unverified"] = True
            if rewrite_score:
                # 降權要放進 score，不能只靠排序。下游 xkb_score.rank() 會
                # 依 score 重新排一次，於是這裡排好的順序在下一個呼叫就沒了
                # ——上次的修法只在這個函式回傳的那一瞬間成立。
                # 放在門檻正下方：不刪掉判斷不了的東西，但也不讓它壓過任何
                # 真的比對過而通過的結果。
                item["rank_score"] = item.get("score")
                item["score"] = round(max(0.0, limit - 0.01), 4)
            kept.append(item)
            continue
        if similarity < limit:
            continue
        if rewrite_score:
            item["rank_score"] = item.get("score")
            item["score"] = round(similarity, 4)
        kept.append(item)
    if rewrite_score:
        # 排序跟著分數走（分數已在上面調整過）。原本大家一起比 score，而那時候「算出來的」
        # 是餘弦（0.6–0.75），「算不出來的」還留著原本的 RRF（約 0.88）或
        # 關鍵字分數——於是一筆索引裡查不到的書籤，會壓過真的以 0.74 命中的
        # 那張卡。今天第六個同型錯誤：一個排序，兩種尺度。
        kept.sort(key=lambda item: (not item.get("_unverified", False),
                                    item.get("score", 0.0)), reverse=True)
    for item in kept:
        item.pop("_unverified", None)
    return kept, len(items) - len(kept), scores


if __name__ == "__main__":
    print(f"min_similarity = {min_similarity()}")
    for sample in ("01-topic/12345", "01-topic/12345.md", "https://x.com/i/status/1", ""):
        print(f"  vector_key({sample!r}) = {vector_key(sample)!r}")
