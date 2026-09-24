#!/usr/bin/env python3
"""餘弦跟 jev 在哪裡不一致——影子模式的讀取端。

`_shadow_compare` 每次召回都把兩邊的判斷存進 relevance_shadow：餘弦的分數、
餘弦當下的決定（kept），以及 jev 對同一批候選的判斷。這支把那些資料讀出來。

**沒有讀取端的觀測資料等於不存在。** cold_knowledge() 在這個專案裡存在了兩週
沒有任何呼叫端，結果是「看得到、不會動」。所以這支跟著影子模式一起上。

這支不建議門檻，它只把不一致攤開來。要選門檻得判斷「誰對」，而那要看個案——
報告把個案排好，讓人或模型看得下去。自動挑一個讓兩邊最像的數字是反過來的：
影子模式的目的不是複製餘弦，是找出餘弦錯在哪。

用法：
    python3 scripts/xkb_jev_shadow_report.py
    python3 scripts/xkb_jev_shadow_report.py --json
    python3 scripts/xkb_jev_shadow_report.py --limit 40
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_console
import xkb_paths

xkb_console.use_utf8()

# 「jev 覺得這筆有回答問題」的暫定分界。**這不是門檻**，只是把報告分成兩半好
# 閱讀的一條線。2026-09-24 的抽樣裡正面回答落在 0.30~0.54、不相關落在 0.01~0.02，
# 所以中間取 0.15 能把兩群分開。真正的門檻要等資料夠多再定。
READ_SPLIT = 0.15


def load_rows(limit: int = 0) -> list[dict]:
    db_path = xkb_paths.SERVICE_DB
    if not db_path.exists():
        return []
    db = None
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        sql = ("SELECT at, query, record_id, cosine, jev, kept, floor"
               "  FROM relevance_shadow WHERE jev IS NOT NULL ORDER BY id DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = db.execute(sql).fetchall()
    except sqlite3.Error as err:
        print(f"讀不到 relevance_shadow：{err}", file=sys.stderr)
        return []
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
    return [dict(r) for r in rows]


def agreement(rows: list[dict], split: float = READ_SPLIT) -> dict[str, list[dict]]:
    """把每一筆分到四格：兩邊都說要 / 都說不要 / 各說各話。

    「各說各話」的兩格才是這份報告的重點。兩邊都同意的那些，換不換模型都一樣。
    """
    buckets: dict[str, list[dict]] = {
        "both_yes": [], "both_no": [],
        "cosine_only": [],   # 餘弦留下、jev 說沒回答問題
        "jev_only": [],      # 餘弦丟掉、jev 說有回答問題
    }
    for row in rows:
        cosine_yes = bool(row.get("kept"))
        jev_yes = float(row.get("jev") or 0.0) >= split
        if cosine_yes and jev_yes:
            buckets["both_yes"].append(row)
        elif not cosine_yes and not jev_yes:
            buckets["both_no"].append(row)
        elif cosine_yes:
            buckets["cosine_only"].append(row)
        else:
            buckets["jev_only"].append(row)
    return buckets


def spread(rows: list[dict], field: str) -> tuple[float, float, float]:
    """(最小, 中位, 最大)。空的回 (0,0,0)。"""
    values = sorted(float(r.get(field) or 0.0) for r in rows)
    if not values:
        return 0.0, 0.0, 0.0
    return values[0], values[len(values) // 2], values[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=0,
                        help="只看最近 N 筆觀測（預設全部）")
    parser.add_argument("--show", type=int, default=12, help="每一格列幾個例子")
    parser.add_argument("--split", type=float, default=READ_SPLIT)
    args = parser.parse_args()

    rows = load_rows(args.limit)
    if not rows:
        print("relevance_shadow 還沒有資料——影子模式剛上線，或 jev 沒跑成。")
        print("（jev 沒跑成時刻意不寫入：一整批 NULL 會長得像「它覺得都不相關」。）")
        return 0

    buckets = agreement(rows, args.split)
    total = len(rows)
    print(f"影子觀測 {total} 筆（只算 jev 真的跑成的）")
    print(f"  判讀分界：jev ≥ {args.split}  ·  餘弦地板：{rows[0].get('floor')}")
    print()
    print(f"  兩邊都說要注入      {len(buckets['both_yes']):>5}")
    print(f"  兩邊都說不要        {len(buckets['both_no']):>5}")
    print(f"  只有餘弦說要        {len(buckets['cosine_only']):>5}  ← 可能是被硬塞進去的")
    print(f"  只有 jev 說要       {len(buckets['jev_only']):>5}  ← 可能是被門檻誤殺的")
    print()

    lo, mid, hi = spread(buckets["both_yes"], "jev")
    print(f"  餘弦留下的那些，jev 給的分數：{lo:.2f} / 中位 {mid:.2f} / {hi:.2f}")
    lo, mid, hi = spread(buckets["jev_only"], "cosine")
    print(f"  jev 說要但被丟掉的，餘弦分數：{lo:.2f} / 中位 {mid:.2f} / {hi:.2f}")
    print()

    if args.json:
        print(json.dumps({k: v[: args.show] for k, v in buckets.items()},
                         ensure_ascii=False, indent=2))
        return 0

    for name, title in (("jev_only", "被餘弦門檻丟掉、但 jev 認為有回答問題的"),
                        ("cosine_only", "餘弦留下、但 jev 認為沒回答問題的")):
        picked = buckets[name]
        if not picked:
            continue
        picked.sort(key=lambda r: -abs(float(r.get("jev") or 0)
                                       - float(r.get("cosine") or 0)))
        print(f"  {title}（差距最大的 {min(args.show, len(picked))} 筆）：")
        print(f"    {'餘弦':>5} {'jev':>5}  問題 / 知識")
        for row in picked[: args.show]:
            print(f"    {float(row.get('cosine') or 0):>5.2f} "
                  f"{float(row.get('jev') or 0):>5.2f}"
                  f"  {str(row.get('query') or '')[:26]} → "
                  f"{str(row.get('record_id') or '')[:34]}")
        print()

    print("  這支只報告。餘弦仍然是唯一做決定的那一個。")
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
