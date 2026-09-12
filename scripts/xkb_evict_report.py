#!/usr/bin/env python3
"""拿 knowledge_usage 算每一筆知識的 gain——先只報告，不動任何東西。

資料來源是 xkb_memory_service 的 knowledge_usage 表，它的語意在那支腳本的註解
裡寫得很清楚：

    considered_count   被語意後端撈出來當候選的次數
    injected_count     其中通過相關性門檻、真的被用上的次數

    「This is the only honest "was it any use" signal XKB has: a card retrieved
    many times that never once clears the floor is not earning its place.」

對照組是**其他知識**，不是同一筆資料的另一半：gain 問的是「用這一筆，比用池子裡
平均一筆，好多少」。gain 為負代表它比平均差——它一直在佔召回名額卻沒有相應的貢獻。

想用同一筆資料湊出 with/without 的兩次嘗試都失敗了，理由寫在 observations_for
的註解裡。那是這個專案的尺度混用 bug 的第四次，只是換了一種形狀。

**這支只報告。** 退場會改變召回看得到什麼，而那是不可逆的方向——照這個專案的
前例（excluded 旗標做成單行道、消化用結論取代筆記），先看數字、確認分類合理，
再決定要不要讓它真的動手。

用法：
    python3 scripts/xkb_evict_report.py
    python3 scripts/xkb_evict_report.py --json
    python3 scripts/xkb_evict_report.py --min-considered 5
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_console
import xkb_eviction as ev
import xkb_paths

xkb_console.use_utf8()

# 被撈出來的次數太少就不判斷。
#
# 不是因為「資料少所以保守」——compute_gain 的先驗收縮已經在做那件事。是因為
# 被撈出來一兩次的知識，它的 injected 比例幾乎是隨機的，而退場是不可逆方向的
# 動作。這個專案在「隨資料量才浮現」的 bug 上犯過三次。
MIN_CONSIDERED = 8


def hit_rate(considered: int, injected: int) -> float:
    """被撈出來之後真的被用上的比例。0..1。"""
    return injected / considered if considered else 0.0


def observations_for(mine: float, others: list[float], weight: int
                     ) -> tuple[list[ev.Observation], list[ev.Observation]]:
    """對照組是「其他知識」，不是同一筆資料的另一半。

    gain 問的是「用這一筆，比用池子裡平均一筆，好多少」。Memmy 的 with/without
    是兩組不同的事件（用了這條知識的任務 vs 沒用的），各自有獨立的結果分數。

    2026-09-12 移植時我連錯兩次，都是想用同一筆資料湊出兩側：

      第一次  with = 命中率、without = 0
              命中率低時 with 也低，但 without 更低，於是 2% 命中率算出 +0.002
              判定 active——最該退的那一類被留下來。同一個量同時扮演兩個方向
              相反的角色，正是這個專案犯過三次的尺度混用。

      第二次  with = 1.0、without = 0
              with 全是滿分，gain 幾乎只反映樣本數：2% 得 +0.98、89% 得 +0.90，
              方向是反的。

    兩次都是假的對照組。真正的對照組在池子裡。
    """
    with_obs = [ev.Observation("self", mine) for _ in range(max(weight, 1))]
    without_obs = [ev.Observation("other", rate) for rate in others]
    return with_obs, without_obs


def load_rows(min_considered: int = MIN_CONSIDERED) -> list[dict]:
    db_path = xkb_paths.SERVICE_DB
    if not db_path.exists():
        return []
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT record_id, considered_count, injected_count, best_similarity,"
            "       last_injected_at"
            "  FROM knowledge_usage"
            " WHERE considered_count >= ?"
            " ORDER BY considered_count DESC",
            (min_considered,),
        ).fetchall()
    except sqlite3.Error as err:
        print(f"讀不到 knowledge_usage：{err}", file=sys.stderr)
        return []
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
    return [dict(r) for r in rows]


def assess(rows: list[dict]) -> list[dict]:
    rates = {row["record_id"]: hit_rate(int(row["considered_count"] or 0),
                                        int(row["injected_count"] or 0))
             for row in rows}
    out = []
    for row in rows:
        considered = int(row["considered_count"] or 0)
        injected = int(row["injected_count"] or 0)
        mine = rates[row["record_id"]]
        others = [r for rid, r in rates.items() if rid != row["record_id"]]
        with_obs, without_obs = observations_for(mine, others, injected)
        status, gain = ev.evaluate(
            row["record_id"], with_obs=with_obs, without_obs=without_obs,
            current_status="active", support=max(injected, 1))
        out.append({
            "record_id": row["record_id"],
            "considered": considered,
            "injected": injected,
            "hit_rate": round(mine, 3),
            "gain": round(gain, 4),
            "verdict": status,
            "best_similarity": round(float(row["best_similarity"] or 0), 3),
            "last_injected_at": row["last_injected_at"],
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-considered", type=int, default=MIN_CONSIDERED)
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()

    rows = load_rows(args.min_considered)
    if not rows:
        print(f"沒有被撈出來 {args.min_considered} 次以上的知識——"
              f"使用統計還不夠判斷退場。")
        return 0

    results = assess(rows)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    archived = [r for r in results if r["verdict"] == "archived"]
    print(f"評估 {len(results)} 筆（被撈出 ≥{args.min_considered} 次）")
    print(f"  建議退場：{len(archived)} 筆")
    print()
    print("  命中率最低的幾筆——被端上來最多、被採用最少：")
    print(f"    {'命中率':>6}  {'撈出':>5} {'用上':>5}  {'gain':>8}  判定")
    for r in sorted(results, key=lambda x: x["hit_rate"])[:args.limit]:
        print(f"    {r['hit_rate']:>6.0%}  {r['considered']:>5} {r['injected']:>5}"
              f"  {r['gain']:>+8.3f}  {r['verdict']}")
    print()
    print("  這支只報告，不會改動任何知識。")
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
