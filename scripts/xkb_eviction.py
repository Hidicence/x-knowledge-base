#!/usr/bin/env python3
"""讓知識會退場——XKB 的 wiki 原本只進不出。

移植自 Memmy（MIT，`/root/reference/memmy-agent`
`Memory/src/algorithm/plugin-algorithms.ts` 的 computeGain / smoothPolicyGain /
policyStatusAfterGain）。比較文件把「讓知識會退場」列為最高優先，因為 XKB 的
每一層都只會長大：卡片只進不出、wiki 只進不出、筆記只進不出。

**判斷依據是效用，不是時間。** 這一點容易誤解成「很久沒用到就退場」，而實測
XKB 的資料說明為什麼不能那樣做：

    某筆知識   被考慮 236 次、被注入  56 次   命中率 24%
    另一筆     被考慮 267 次、被注入 183 次   命中率 69%

前者非常活躍——每次語意搜尋都被撈出來當候選，一點都不「久沒被使用」。但它
有四分之三的時候被判斷為不該用，也就是它一直在佔召回的名額卻一直沒有貢獻。
按「很久沒動」的規則它永遠不會退場；按 gain 它才是最該退的那一類。反過來，
半年沒被碰過但每次被撈出來都有用的知識，gain 是正的，不該退。

比較文件說「XKB 沒有任務成功訊號所以不能抄」——那一點是錯的，查證後更正：
computeGain 只要求每筆資料有一個 value 數值，不在乎那個值哪來。不能抄的只有
reward pipeline（怎麼從任務結果算出 value）。這裡餵它 knowledge_usage 的
injected_count / considered_count，那張表 2026-09-12 已經累積了 261 筆。

與 Memmy 刻意不同的一處：**退場不是單向的。**

    policyStatusAfterGain 的第一行是 `if archived return archived`——一旦退場
    就不會自己回來。Memmy 有 reward pipeline 可以重新評估，XKB 沒有。而昨天
    在 excluded 旗標上踩過同一個坑：我做成單行道，結果一張被解析 bug 誤排除的
    好卡片救不回來，只能手動改檔案。所以這裡的 archived 會繼續累積使用統計，
    gain 回升就能復活（revive_gain）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ── 門檻 ─────────────────────────────────────────────────────────────────────
# 全部照 Memmy 的預設值搬。它們不是隨手取的：activate 與 archive 之間留 0.07
# 的間隙做遲滯，gain 在 0 附近抖動時不會反覆 activate/archive。
MIN_GAIN = 0.02
ARCHIVE_GAIN = -0.05
# 復活門檻比 activate 再高一些，避免剛退場的東西在邊界上彈回來。
REVIVE_GAIN = 0.05
MIN_SUPPORT = 1
TAU_SOFTMAX = 0.5
EMA_ALPHA = 0.4

# 樣本少於這個數就不用 softmax 加權——加權會被單一離群值主導。
WEIGHTED_MIN_SAMPLES = 3
# 朝先驗收縮的權重。意思是：要有至少這麼多筆對照樣本，估計值才會脫離先驗。
# 這一條是整段裡最容易在重寫時漏掉的，而它的作用是防止 2、3 筆算出誇張的 gain。
PRIOR_WEIGHT = 5
BASELINE_FLOOR = 0.2
BASELINE_CEILING = 0.5


@dataclass(frozen=True)
class Observation:
    """一筆使用觀測。value 是效用，0..1。

    XKB 餵進來的是 injected_count / considered_count——「被撈出來之後真的被用
    上的比例」。Memmy 餵的是任務成功率。computeGain 不在乎這個值哪來。
    """
    key: str
    value: float


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _value_weighted_mean(values: list[float], tau: float) -> float:
    """softmax 加權平均。高分的樣本權重更大。"""
    if not values:
        return 0.0
    top = max(values)
    exps = [math.exp((v - top) / max(tau, 1e-6)) for v in values]
    total = sum(exps) or 1.0
    return sum(v * (e / total) for v, e in zip(values, exps))


def compute_gain(with_obs: list[Observation], without_obs: list[Observation],
                 *, tau: float = TAU_SOFTMAX) -> float:
    """用到這筆知識時的表現，減掉沒用到時的表現。

    照 Memmy 的 computeGain 原樣搬，包含三個容易漏掉的細節：
      1. 樣本 ≥3 才用 softmax 加權，否則普通平均
      2. baseline 夾在 0.2..0.5 之間
      3. 「沒用到」那一側朝 baseline 收縮，權重 5
    """
    with_values = [o.value for o in with_obs]
    without_values = [o.value for o in without_obs]

    weighted_with = _value_weighted_mean(with_values, tau)
    with_mean = _mean(with_values)
    effective_with = (weighted_with if len(with_obs) >= WEIGHTED_MIN_SAMPLES
                      else with_mean)
    if math.isnan(effective_with):
        effective_with = 0.0

    pool_mean = _mean(with_values + without_values)
    baseline = max(BASELINE_FLOOR,
                   min(BASELINE_CEILING,
                       pool_mean if not math.isnan(pool_mean) else BASELINE_CEILING))

    without_mean = _mean(without_values)
    if math.isnan(without_mean):
        without_mean = 0.0
    blended_without = ((without_mean * len(without_obs) + baseline * PRIOR_WEIGHT)
                       / (len(without_obs) + PRIOR_WEIGHT))

    return effective_with - blended_without


def smooth_gain(new_gain: float, current_gain: float, *, is_first: bool,
                alpha: float = EMA_ALPHA) -> float:
    """指數移動平均。第一次沒有歷史可平滑，直接用新值。"""
    if is_first:
        return new_gain
    a = max(0.0, min(1.0, alpha))
    return a * new_gain + (1 - a) * current_gain


def status_after_gain(current_status: str, *, support: int, gain: float,
                      min_support: int = MIN_SUPPORT,
                      min_gain: float = MIN_GAIN,
                      archive_gain: float = ARCHIVE_GAIN,
                      revive_gain: float = REVIVE_GAIN) -> str:
    """candidate / active / archived 之間的狀態轉移。

    跟 Memmy 唯一的差別在 archived 這一支：它那邊是 `if archived return
    archived`，一旦退場就不會回來。Memmy 有 reward pipeline 能重新評估，XKB
    沒有——而單行道的代價昨天剛付過一次（excluded 旗標害一張好卡片救不回來）。

    所以這裡讓 archived 也繼續被評估：gain 回到 revive_gain 以上就復活成
    candidate（不是直接 active，要再證明一次）。revive 門檻比 activate 高，
    避免剛退場的東西在邊界上彈回來。
    """
    if current_status == "archived":
        if gain >= revive_gain and support >= min_support:
            return "candidate"
        return "archived"
    if current_status == "candidate":
        return ("active" if support >= min_support and gain >= min_gain
                else "candidate")
    # active
    if gain < archive_gain or support < min_support:
        return "archived"
    return "active"


def evaluate(key: str, *, with_obs: list[Observation],
             without_obs: list[Observation], current_status: str = "candidate",
             current_gain: float = 0.0, support: int | None = None,
             has_history: bool = False) -> tuple[str, float]:
    """一筆知識的新狀態與平滑後的 gain。

    support 是「有幾個獨立來源支持它」。Memmy 算的是 distinct episodeId；XKB
    這裡預設用觀測筆數，呼叫端有更好的來源就自己傳。
    """
    raw = compute_gain(with_obs, without_obs)
    gain = smooth_gain(raw, current_gain, is_first=not has_history)
    return status_after_gain(current_status,
                             support=len(with_obs) if support is None else support,
                             gain=gain), gain


# ── 第二條規則：反覆被端上來、從來沒被用上 ───────────────────────────────
#
# 上面那套 gain 需要「這一筆 vs 對照組」的兩側觀測，而 XKB 湊不出誠實的對照組
# （三次嘗試都失敗，理由寫在 xkb_evict_report.observations_for 的註解裡）。這
# 條規則不需要對照組：它只問一個可以直接觀測的事實。
#
#     被撈出來 N 次以上，而且一次都沒有通過相關度地板。
#
# 2026-09-12 在 1,677 張卡片上量的分布：
#
#     N=1  26 筆    N=5  5 筆    N=8  1 筆    N=10  0 筆
#
# 而那些「從來沒被用上」的最高相似度全部落在 0.48~0.55，地板是 0.55。也就是
# 它們不是「還沒輪到用」，是每一次都差一點——被錯的問題撈出來的雜訊。
#
# **這條規則在定義上碰不到「存起來很久以後才用」的東西。** 那種卡片的特徵是
# considered_count 低（根本沒被撈出來過），而這條規則要求 considered_count 高。
# 久沒被使用不是退場理由，一直佔名額卻從來沒貢獻才是。
#
# 門檻取 5 而不是 8：8 只抓到 1 筆，等於機制存在但不運作；5 抓到 5 筆，夠小到
# 出錯也看得出來，又真的會動。這個數字跟 cold_knowledge() 的預設一致——那支
# 查詢 2026-08-30 就寫好了，只是從來沒有呼叫端。
DEMOTE_AFTER_CONSIDERED = 5


def is_demoted(considered: int, injected: int,
               *, after: int = DEMOTE_AFTER_CONSIDERED) -> bool:
    """這一筆該不該被降權。

    降權不是移除。被降權的知識還在索引裡、還會被撈出來、還會被量測——只是排
    在所有正常命中之後，不再擠掉更好的候選。

    這個差別決定了它能不能復活。如果降權是「從索引拿掉」，它就再也不會被
    considered，injected_count 永遠停在 0，於是永遠回不來——那又是一條偽裝過的
    單行道（excluded 旗標 2026-09-11 才剛付過這個代價）。因為它繼續被量測，
    哪天真的有一次通過地板，injected_count 變 1，這個函式就回 False，降權自動
    解除。不需要人介入，也沒有狀態要存。
    """
    return injected <= 0 and considered >= max(1, after)
