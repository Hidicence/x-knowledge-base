#!/usr/bin/env python3
"""
跨層召回融合

每條召回腿有自己的計分方式，尺度互不相同：wiki 是關鍵字或餘弦、卡片是
gbrain RRF 或餘弦、對話是折扣過的關鍵字比例、反例、可復用資產各一套。
把這些原始數字並排排序，等於拿公分比英吋。

**rank() 用 reciprocal rank fusion（RRF）**：只看每筆在自己那條腿裡的名次，
不看原始分數的絕對值。BM25 的無上限分數跟餘弦的 0~1 從來不需要被弄成可比，
因為只有名次跨過腿的邊界。

    unified_score = Σ（該腿權重 / (K + 腿內名次)）  ...加上一個小的腿內相關度項

權重（wiki > 卡片 > 對話 > 反例 > 動作 > 驗不出來，擠在 0.80~1.0）是平手時的
傾向，不是碾壓；K=60 是 RRF 慣例值。

XKB 的 wiki 腿只有十幾個 topic，常常只回一個弱命中——純 RRF 會讓那個弱命中拿
腿內第 1、壓過強卡片。所以排序分兩層：先所有「在至少一條腿裡過了相關度地板
（RELEVANCE_FLOOR）」的命中，再所有沒過的，各層照 unified_score。名次本身不動，
分層保證弱命中排在所有上地板命中之後、不管別條腿回了幾筆。

relevance() / unified()（舊的 raw/(raw+anchor)×權重）留著只給 relevance 這個
顯示欄位用；排序不走它。anchor 與權重可用環境變數覆寫（XKB_SCORE_ANCHOR_WIKI）。
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


# 腿內相關度的地板。relevance() 設計成「該腿中位相關」落在 0.5；低於這個地板
# 算明顯偏弱。偏弱的命中名次往後推，排在所有上地板命中之後——這樣「wiki 腿
# 只回一個弱命中」不會因為腿內唯一就搶到第 1 名壓過強卡片（審查 finding 1）。
RELEVANCE_FLOOR = 0.35

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
    """舊的對齊分數（raw/(raw+anchor)×權重）。rank() 已改用 RRF，不再呼叫它；
    留著給還在讀這個欄位的舊呼叫端。"""
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

    unified_score = Σ（該腿權重 / (K + 腿內名次)），對它出現過的每一條腿加總
    （目前每筆只在一條腿；加 BM25 腿後、同一張卡被 BM25 和向量都撈到會有兩項）。

    排序分兩層：先所有「在至少一條腿裡過了相關度地板」的命中（照 unified_score），
    再所有沒過的。XKB 的 wiki 腿只有十幾個 topic、常常只回一個弱命中——分層排序
    保證那個弱命中排在**所有**上地板命中之後，不管別條腿回了幾筆。

    idempotent：matched_by / leg_rank / relevance / unified_score 每次呼叫都重建。
    原始分數 <= 0 或缺分的項目不拿名次，歸到下層、墊底。
    """
    for item in results:
        item["matched_by"] = []
        item["_rrf"] = 0.0
        item["_above_floor"] = False
        item.pop("leg_rank", None)

    legs: dict[str, list[dict]] = {}
    for item in results:
        legs.setdefault(_leg_of(item), []).append(item)

    for leg, group in legs.items():
        # 有分數的才排名次；<=0 或缺分的沉到這條腿的最後。
        group.sort(key=lambda r: (float(r.get("score") or 0.0) > 0.0,
                                  float(r.get("score") or 0.0)), reverse=True)
        w = weight_for(leg)
        for i, item in enumerate(group, 1):
            raw = float(item.get("score") or 0.0)
            rel = relevance(raw, leg) if raw > 0.0 else 0.0
            item["relevance"] = round(rel, 4)
            item["matched_by"].append(leg)
            if raw <= 0.0:
                continue  # 不拿名次；unified_score 留 0、歸下層
            item.setdefault("leg_rank", i)
            item["_rrf"] += w / (K_RRF + i)
            if rel >= RELEVANCE_FLOOR:
                item["_above_floor"] = True

    ranked = sorted(
        results,
        key=lambda r: (0 if r["_above_floor"] else 1, -r["_rrf"]),
    )
    for item in ranked:
        item["unified_score"] = round(item.pop("_rrf"), 6)
        item.pop("_above_floor")
    return ranked


if __name__ == "__main__":
    # 示範 RRF 融合：三條腿合在一起，看排序
    demo = [
        {"title": "wiki 強命中", "score": 0.85, "score_scale": "wiki_semantic"},
        {"title": "wiki 弱命中（腿內唯一）", "score": 0.31, "score_scale": "wiki_semantic"},
        {"title": "卡片 強命中", "score": 0.88, "score_scale": "card_semantic"},
        {"title": "卡片 中命中", "score": 0.66, "score_scale": "card_semantic"},
        {"title": "對話 滿分", "score": 0.65, "score_scale": "conversation"},
        {"title": "驗不出來的網址", "score": 0.9, "score_scale": "unverified"},
        {"title": "動作提示（無分）", "score": 0.0, "source_type": "action"},
    ]
    for r in rank(demo):
        print(f"  {r['unified_score']:.6f}  rel={r['relevance']:.3f}  "
              f"leg#{r.get('leg_rank', '-')}  {r['title']}")
