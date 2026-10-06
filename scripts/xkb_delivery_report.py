#!/usr/bin/env python3
"""主動建議有沒有被回答用上。

overlap 是建議內文的詞有幾成出現在回答裡：字面重疊，不是「確實有用」。
同主題的回答本來就會共用一些詞，所以看的是分布與相對高低，不是單一門檻。

用法：
    python scripts/xkb_delivery_report.py [--days 7] [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xkb_paths
from xkb_evidence import identity_source


def report(db_path: Path, days: int) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT evidence_key, overlap FROM delivery_outcomes WHERE recorded_at>=?", (since,)).fetchall()
    except sqlite3.OperationalError:
        rows = []  # 服務還沒用新版重啟過，表還沒建：沒有紀錄，不是錯誤
    turns = db.execute("SELECT retrieval_json FROM turns WHERE started_at>=? AND status='succeeded'", (since,)).fetchall()
    suggested = sum(1 for (r,) in turns if ((json.loads(r or "{}").get("delivery") or {}).get("records")))
    buckets = {"<0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, ">=0.6": 0}
    per_key: dict[str, list[float]] = {}
    for key, overlap in rows:
        buckets["<0.2" if overlap < .2 else "0.2-0.4" if overlap < .4 else "0.4-0.6" if overlap < .6 else ">=0.6"] += 1
        per_key.setdefault(key, []).append(overlap)
    repeated = sorted(((k, len(v), sum(v) / len(v)) for k, v in per_key.items() if len(v) >= 2), key=lambda x: x[2])
    return {"days": days, "turns": len(turns), "turns_with_suggestions": suggested,
            "suggestions": len(rows), "overlap_buckets": buckets,
            "mean_overlap": round(sum(o for _, o in rows) / len(rows), 3) if rows else None,
            "least_used_repeated": [{"source": identity_source(k), "times": n, "mean_overlap": round(m, 3)}
                                    for k, n, m in repeated[:10]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--db", default=str(xkb_paths.SERVICE_DB))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = report(Path(args.db), args.days)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"近 {result['days']} 天：{result['turns']} 輪，其中 {result['turns_with_suggestions']} 輪有建議，共 {result['suggestions']} 條")
    print(f"回答與建議的字面重疊（平均 {result['mean_overlap']}）：{result['overlap_buckets']}")
    for item in result["least_used_repeated"]:
        print(f"  送過 {item['times']} 次、平均重疊 {item['mean_overlap']}：{item['source']}")
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
