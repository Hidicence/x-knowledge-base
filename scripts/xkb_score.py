#!/usr/bin/env python3
"""
跨層分數換算

每個召回層都有自己的計分方式，尺度互不相同：

    wiki        標題×3 + tags×1.5 + body×0.4，沒有上限（實測 0.40 ~ 3.65）
    卡片/書籤   gbrain RRF 或向量餘弦，0 ~ 1
    反例        相關度×0.6 + 信號詞×0.4，0 ~ 1
    可復用資產  命中率 + phrase bonus

router 原本把這些數字直接並排輸出，等於拿公分跟英吋比大小——
「哪個比較相關」其實從來沒有被真正比較過。

這裡做兩件事：

1. **對齊**：用 `raw / (raw + anchor)` 把每層壓進 0~1，anchor 取該層實測的中位數。
   意思是「這一層的中等相關」在每層都會落在 0.5，跨層才有共同基準。
   飽和曲線而不是除以最大值——最大值會被單一離群結果綁架，
   而且高分區本來就該遞減，3.6 分和 3.0 分的差別遠不如 0.4 和 1.0 之間的差別。

2. **加權**：對齊之後乘上來源權威度。wiki 是自己消化過的結論，
   卡片是原始素材；同樣「中等相關」時前者該排前面。

anchor 與權重可用環境變數覆寫（XKB_SCORE_ANCHOR_WIKI 等），
換一個知識庫、分數分佈不同時不必改程式碼。
"""
from __future__ import annotations

import os

# 各層實測中位數（2026-07-28，8 個代表性查詢）。改了計分方式就要重新量。
#
# 這裡的鍵是**尺度**，不是資料種類。呼叫端用 score_scale 指定，沒指定才
# 退回 source_type——因為同一種資料可能走不同的計分方式（wiki 有關鍵字
# 版也有語意版，數字差一個量級）。
DEFAULT_ANCHORS = {
    "wiki": 0.93,
    "memory": 0.93,              # 與 wiki 同一支程式、同一種計分
    # 語意召回回傳的是餘弦相似度（門檻 0.65，實測有效區間約 0.65~0.85），
    # 跟關鍵字那套完全不同尺度，所以另立錨點
    "wiki_semantic": 0.72,
    "memory_semantic": 0.72,
    "card": 0.88,
    "bookmark": 0.88,
    # 卡片被實測成餘弦之後（xkb_relevance 的 rewrite_score）就不再是 RRF，
    # 0.88 那個錨點是照 RRF 量的。餘弦有自己的區間，跟 wiki 語意同一段。
    "card_semantic": 0.72,
    "contrarian": 0.52,
    "action": 0.31,
    # 對話軌跡是關鍵字比對，原始分數是「命中幾個詞」——跟餘弦相似度不同尺度。
    # 呼叫端會先除以查詢詞數變成比例（0~1），這個錨點是對那個比例的。
    "conversation": 0.55,
}

# 來源權威度：消化過的結論 > 原始素材 > 補充提醒
DEFAULT_WEIGHTS = {
    "wiki": 1.0,
    "memory": 1.0,
    "wiki_semantic": 1.0,
    "memory_semantic": 1.0,
    "card": 0.85,
    "bookmark": 0.85,
    # 尺度換了，來源權威度沒換：還是原始素材。
    "card_semantic": 0.85,
    "contrarian": 0.7,
    "action": 0.6,
    # 關鍵字命中是比語意相似弱的證據：詞出現過不代表在講同一件事。
    # 壓在卡片之下，對話才不會因為剛好含到查詢詞就洗掉真正相關的知識。
    "conversation": 0.55,
}

FALLBACK_ANCHOR = 0.5
FALLBACK_WEIGHT = 0.5


def _env_override(prefix: str, source_type: str, default: float) -> float:
    raw = os.getenv(f"{prefix}_{source_type.upper()}")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def anchor_for(source_type: str) -> float:
    return _env_override("XKB_SCORE_ANCHOR", source_type,
                         DEFAULT_ANCHORS.get(source_type, FALLBACK_ANCHOR))


def weight_for(source_type: str) -> float:
    return _env_override("XKB_SCORE_WEIGHT", source_type,
                         DEFAULT_WEIGHTS.get(source_type, FALLBACK_WEIGHT))


def relevance(raw_score: float, source_type: str) -> float:
    """把單層的原始分數壓成 0~1。該層的中等相關會落在 0.5。"""
    if raw_score <= 0:
        return 0.0
    anchor = anchor_for(source_type)
    if anchor <= 0:
        return 1.0
    return raw_score / (raw_score + anchor)


def unified(raw_score: float, source_type: str) -> float:
    """對齊後再乘來源權威度，這才是跨層可比的分數。"""
    return round(relevance(raw_score, source_type) * weight_for(source_type), 4)


def rank(results: list[dict]) -> list[dict]:
    """就地補上 relevance / unified_score，並依 unified_score 由高到低排序。

    原始 score 保留不動：各層的門檻是照自己的尺度調的，改它會牽動一堆判斷。
    """
    for item in results:
        # score_scale 是「這個數字是哪一套尺度算出來的」，由算出它的地方
        # 標上；source_type 是「這筆東西是什麼」，會回給 API 用戶。兩者
        # 常常不同：wiki 的語意召回 source_type 是 wiki、尺度卻是餘弦，
        # 而 wiki 的錨點是照關鍵字尺度（0.40–3.65）量的。用錯錨點就等於
        # 沒有對齊。沒標的沿用舊行為。
        source_type = str(item.get("score_scale") or item.get("source_type", ""))
        raw = float(item.get("score") or 0.0)
        item["relevance"] = round(relevance(raw, source_type), 4)
        # 驗不出相似度的項目在 xkb_relevance.filter_irrelevant 就已經降到
        # 門檻之下了（那裡才知道有沒有驗證過）。這裡不要再降一次：
        # 同一件事兩個定義，正是這個專案反覆犯的那類錯。
        item["unified_score"] = unified(raw, source_type)
    return sorted(results, key=lambda r: r.get("unified_score", 0.0), reverse=True)


if __name__ == "__main__":
    print(f"{'source':12} {'raw':>6} {'relevance':>10} {'unified':>8}")
    samples = [("wiki", 3.65), ("wiki", 0.93), ("wiki", 0.40),
               ("card", 0.91), ("card", 0.88), ("card", 0.50),
               ("contrarian", 0.80), ("contrarian", 0.52),
               ("action", 0.97), ("action", 0.31)]
    for source_type, raw in samples:
        print(f"{source_type:12} {raw:>6.2f} {relevance(raw, source_type):>10.3f} "
              f"{unified(raw, source_type):>8.3f}")
