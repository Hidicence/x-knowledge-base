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
    # 關鍵字退路的分數是 _keyword_unit_score 的覆蓋率（0~1），既不是 RRF
    # 也不是餘弦。錨點取這個區間的中點——那是這個尺度的定義，不是量出來的
    # 中位數，所以標明清楚：換了計分方式要重量。
    "card_keyword": 0.5,
    "wiki_keyword": 0.5,
    # 沒辦法跟問題比對過的項目（網址、semantic: id、索引讀不到）。
    # 帶的是後端自己的 RRF，沒有被驗證過。這件事以前是靠在 xkb_relevance
    # 裡改寫分數做到的，那等於捏造一個測量值；尺度的事在尺度表裡解決。
    #
    # 目標是「剛好落在驗證過的卡片之下」，不是「掉到所有東西之下」。
    # 我第一版用 1.6/0.5，典型 RRF 只剩 0.1774，反而低於對話軌跡的
    # 0.4033——於是卡片索引壞掉時，整個封包都是對話軌跡、卡片被截光，
    # 正是「索引壞掉長得像知識庫裡沒東西」。調錨點時要算交叉點，
    # 不能只看自己那一層的數字有沒有變小。
    "unverified": 0.59,
    "contrarian": 0.52,
    "action": 0.31,
    # 對話軌跡：呼叫端（Store.recall）已經乘過 KEYWORD_EVIDENCE_DISCOUNT
    # (0.65) 把它放到與餘弦可比的區間了，所以這裡用餘弦的錨點。
    # 原本這個錨點寫的是「對未折扣比例」的 0.55、權重又再壓一次 0.55，
    # 等於同一個「關鍵字證據較弱」的判斷扣了兩遍：實測滿分命中只得
    # 0.2979，低於每一個剛過門檻的卡片與 wiki，於是知識層一填滿 limit，
    # 對話軌跡就被整段截掉——共享對話記憶等於從 /v1/recall 消失。
    "conversation": 0.72,
}

# 來源權威度：消化過的結論 > 原始素材 > 補充提醒。
# RRF 之下這是 RRF 貢獻的乘數，不是正規化分數的乘數——某一層改了計分方式
# 不需要重調這裡，因為那一層只貢獻一個名次。壓縮在 0.5~1.0：權威度是
# 平手時的傾向，不該讓 wiki 第 5 名壓過卡片第 1 名。
DEFAULT_WEIGHTS = {
    # 權威度是**平手時的傾向**，不是跨好幾個名次的碾壓。K=60 之下，相鄰名次
    # 的 RRF 貢獻很接近，所以權重必須擠在一個窄帶裡：wiki 第 2 名壓過卡片
    # 第 1 名可以，wiki 第 7 名壓過卡片第 1 名不行。實測 0.96 對 1.0 的差
    # 大約值「一個名次」。
    "wiki": 1.0,
    "memory": 1.0,
    "wiki_semantic": 1.0,
    "memory_semantic": 1.0,
    "wiki_keyword": 1.0,
    "card": 0.96,
    "bookmark": 0.96,
    "card_semantic": 0.96,
    "card_keyword": 0.96,
    "conversation": 0.93,
    "contrarian": 0.90,
    "action": 0.87,
    # 驗不出來（網址、semantic: id、索引讀不到）：不是零（不代表不相關），
    # 但穩定墊在所有驗證過的東西之下。
    "unverified": 0.80,
}

# RRF 常數。60 是慣例值——讓相鄰名次的貢獻接近，融合才穩。
K_RRF = 60

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


def _leg_of(item: dict) -> str:
    """這筆結果是哪一條召回腿產生的。

    score_scale 是「分數怎麼算的」，由算出它的地方標上；同一個 score_scale
    的兩筆一定來自同一條腿，可以照各自的原始分數排出腿內名次。沒標的退回
    source_type。
    """
    return str(item.get("score_scale") or item.get("source_type", "") or "?")


def rank(results: list[dict]) -> list[dict]:
    """用 reciprocal rank fusion 融合多條召回腿，就地補上 unified_score 並排序。

    舊版把每層的原始分數用手調錨點壓到 0~1 再乘權重——那個設計會因為調一個
    錨點就讓某層靜默壓過另一層（這個 session 為此繞了六輪）。RRF 融合的是
    **名次**，所以 BM25 的無上限分數跟餘弦的 0~1 從來不需要被弄成可比。

    每筆結果的 unified_score = Σ（該腿權重 / (K + 腿內名次)），對它出現過的
    每一條腿加總。目前每筆只在一條腿裡，所以是單項；未來加真 BM25 腿、
    同一張卡被 BM25 和向量都撈到時，兩項會相加。
    """
    legs: dict[str, list[dict]] = {}
    for item in results:
        legs.setdefault(_leg_of(item), []).append(item)

    fused: dict[int, float] = {}
    for leg, group in legs.items():
        group.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
        w = weight_for(leg)
        for i, item in enumerate(group, 1):
            fused[id(item)] = fused.get(id(item), 0.0) + w / (K_RRF + i)
            item.setdefault("leg_rank", i)
            # 這筆是被哪些腿撈到的。目前每筆只在一條腿裡；加了真 BM25 腿之後，
            # 同一張卡被 BM25 和向量都撈到就會是 ["card_keyword", "card_semantic"]。
            # 這 session 的除錯有這個欄位會快很多。
            item.setdefault("matched_by", []).append(leg)
            # relevance 仍照舊算，給顯示層當「這一層裡有多相關」的參考；排序不用它。
            item["relevance"] = round(relevance(float(item.get("score") or 0.0), leg), 4)

    for item in results:
        item["unified_score"] = round(fused.get(id(item), 0.0), 6)
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
