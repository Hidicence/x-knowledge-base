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


def _mark_unverified(item: dict[str, Any]) -> None:
    """標明這一筆沒有被拿去跟問題比對過。不要動它的分數。

    這裡我改了六次，每一次都是在替「驗不出來的東西該排在哪」挑一個數字：
    釘在門檻減 0.01、等比壓進門檻之下、乾脆不動……每一個都是捏造的測量值，
    而且壓到哪一段都會撞上別層的尺度——壓高壓過 wiki 的真餘弦，壓低掉到
    對話軌跡之下，而後者會讓索引壞掉長得像「知識庫裡沒東西」。

    正確的做法是不要在這裡排序。score 保持是後端自己算的那個數字，
    score_scale 說明它是哪一套尺度；跨層要怎麼比是 xkb_score.rank() 的事，
    而 unverified 這個尺度的錨點與權重就寫在那張表上，一個地方、一個定義。
    """
    item["score_basis"] = "unverified"
    item["score_scale"] = "unverified"
    if item.get("score") is None:
        # 沒有分數的項目補 0.0，不要留 None：下游排序是 item.get("score", 0.0)，
        # 鍵存在時拿到的是 None，同一批裡再有一筆數值分數就丟 TypeError。
        item["score"] = 0.0


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
        # 一個鍵都解不出來（索引壞了、或這批全是網址／semantic: id）。
        # 只標記，不動分數。
        #
        # 這裡我來回改了三次，每次都是在替一個不屬於這裡的問題找位置。
        # 跨層可比是 xkb_score 的工作，而合併點（Store.recall_packet）原本
        # 沒有呼叫它、直接照原始 score 排三種尺度。只要那件事沒修，這裡把
        # 分數壓到哪一段都會撞到別層：壓高壓過 wiki 的真餘弦，壓低掉到
        # 對話軌跡（上限 0.65）之下——後者會讓索引壞掉長得像「知識庫裡
        # 沒東西」，而那正是這個專案最不能有的失敗模式。
        #
        # 合併點修好之後，這裡只做它誠實做得到的事：整批都驗不出來時，
        # 沒有任何比較發生過，就不要假裝比過。標記讓下游知道這件事。
        for item in items:
            _mark_unverified(item)
        # 只有「本來該查得到卻查不到」才算故障。整批都是網址或 semantic: id
        # 時 vector_key 依設計就回空字串，那是正常情況——而 note() 一個行程
        # 只報一次，讓正常情況把那一次用掉，之後真的索引壞掉就沒人說話了。
        if any(keys.values()):
            xkb_failures.note(
                "relevance filter",
                RuntimeError("有可查的鍵卻對不到向量索引——索引可能壞了"))
        return items, 0, {}

    kept: list[dict[str, Any]] = []
    for item in items:
        similarity = scores.get(keys[id(item)])
        if similarity is None:
            # 判斷不出來 → 放行，但要記住它沒有被驗證過。過濾器不能刪掉
            # 它判斷不了的東西，但也不該讓它排在真的比對過的前面。
            item["_unverified"] = True
            if rewrite_score:
                _mark_unverified(item)
            kept.append(item)
            continue
        if similarity < limit:
            continue
        if rewrite_score:
            item["rank_score"] = item.get("score")
            item["score"] = round(similarity, 4)
            # 分數換尺度了就要說。原本改寫成餘弦之後記錄還標著 card，
            # 而那個錨點是照 RRF 量的——同一個標籤底下兩種尺度。
            item["score_scale"] = "card_semantic"
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
