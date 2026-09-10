#!/usr/bin/env python3
"""每一次管線執行做了什麼——一個重算不出來的紀錄。

XKB 原本能回答的是「現在有幾張卡、幾筆待轉、索引幾筆」。那些都是掃一次檔案
就能重算的量，而重算得出來的東西不會記得歷史。

2026-09-01 到 09-09，書籤轉卡每晚都跑、每晚都以 404 失敗、每晚都回報成功，
八天 0 產出。當時所有的量都還在：卡片 1,603 張、索引 1,632 筆，數字都「正常」。
沒有任何地方記著「昨晚這一階段收了 9 筆、產出 0 筆、失敗 9 筆、原因是找不到
模型」，所以那八天在系統裡不存在——只有佇列慢慢變長這一個間接影子。

這份帳本補的就是那件事。每個階段跑完寫一行：收了多少、產出多少、失敗多少、
還剩多少、失敗長什麼樣。它不取代 status_knowledge_pipeline.py（那是現況快照），
它記的是快照之間發生過什麼。

刻意不做的事：
  - 不做輪替與壓縮。一天數行，一年幾千行,問題還沒大到要先解。
  - 寫入失敗不會中斷管線,但也不會沉默——走 xkb_failures，stderr 上會說。
    一個會安靜寫不進去的帳本，比沒有帳本更糟。

用法（shell 階段）:
    python3 scripts/xkb_ledger.py record --stage bookmark-batch \
        --intake 9 --produced 0 --failed 9 --pending 57 --reason "404 model not found"

用法（Python）:
    import xkb_ledger
    xkb_ledger.record("bookmark-batch", intake=9, produced=0, failed=9, pending=57)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_failures
import xkb_paths

LEDGER_PATH = xkb_paths.PIPELINE_LEDGER


def record(
    stage: str,
    *,
    intake: int | None = None,
    produced: int | None = None,
    failed: int | None = None,
    pending: int | None = None,
    reason: str = "",
    ok: bool = True,
    extra: dict | None = None,
    path: Path | None = None,
) -> dict:
    """記一次執行。回傳寫下去的那筆，方便呼叫端印出來。"""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "ok": bool(ok),
    }
    for name, value in [("intake", intake), ("produced", produced),
                        ("failed", failed), ("pending", pending)]:
        if value is not None:
            entry[name] = int(value)
    if reason:
        # 失敗原因是給人看的一行,不是完整堆疊。堆疊在各階段自己的 log 裡。
        entry["reason"] = reason.strip()[:300]
    if extra:
        entry.update(extra)

    target = path or LEDGER_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as err:  # noqa: BLE001 — 帳本不能弄掛管線
        xkb_failures.note("pipeline ledger", err, detail=str(target), repeat=True)
    return entry


def read(stage: str | None = None, *, limit: int | None = None,
         path: Path | None = None) -> list[dict]:
    """讀回紀錄,舊的在前。壞掉的行跳過,不要因為一行壞掉就整份讀不到。"""
    target = path or LEDGER_PATH
    if not target.exists():
        return []
    rows: list[dict] = []
    try:
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as err:
        xkb_failures.note("pipeline ledger", err, detail=str(target))
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if stage and row.get("stage") != stage:
            continue
        rows.append(row)
    if limit is not None:
        rows = rows[-limit:]
    return rows


def stages(path: Path | None = None) -> list[str]:
    """帳本裡出現過的階段名稱。"""
    return sorted({row.get("stage", "") for row in read(path=path) if row.get("stage")})


def _parse_ts(row: dict) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(row.get("ts", "")))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# 比這個更近的兩次執行，是同一段人工操作，不是排程的節奏。
# 這個值存在的理由見 typical_gap_seconds。
SAME_SESSION_SECONDS = 3600.0


def typical_gap_seconds(rows: list[dict]) -> float | None:
    """這個階段平常多久跑一次——用它自己的紀錄算,不要手調常數。

    門檻寫死成「24 小時」的話,改排程就會變成誤報,而每天誤報的檢查等於沒有
    檢查。所以取間隔的中位數。

    但中位數只擋得住「一次」離群。2026-09-10 我為了驗證連續手動跑了六次書籤
    批次,每次相隔幾分鐘,中位間隔就被壓成 0 小時——於是健檢說「平常每 0h 跑
    一次,已經 11h 沒有紀錄」,把一個正常的階段報成排程死掉。我上一版的註解寫
    著「一次補跑不會把它拉歪」,而我的測試也只測了一次。

    所以先把「同一段人工操作」濾掉：靠得比 SAME_SESSION_SECONDS 還近的兩次
    執行不算節奏。濾完不足三個間隔就回 None——沒有節奏可比的時候要保持沉默,
    而不是拿一個算出來的假節奏去判斷。
    """
    times = sorted(t for t in (_parse_ts(r) for r in rows) if t is not None)
    if len(times) < 3:
        return None
    gaps = sorted(gap for gap in
                  ((b - a).total_seconds() for a, b in zip(times, times[1:]))
                  if gap >= SAME_SESSION_SECONDS)
    if len(gaps) < 2:
        return None
    mid = len(gaps) // 2
    return gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="記一次執行")
    rec.add_argument("--stage", required=True)
    rec.add_argument("--intake", type=int)
    rec.add_argument("--produced", type=int)
    rec.add_argument("--failed", type=int)
    rec.add_argument("--pending", type=int)
    rec.add_argument("--reason", default="")
    rec.add_argument("--not-ok", action="store_true", help="這次執行整體失敗")

    show = sub.add_parser("show", help="印出紀錄")
    show.add_argument("--stage")
    show.add_argument("--limit", type=int, default=20)
    show.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.command == "record":
        entry = record(
            args.stage,
            intake=args.intake,
            produced=args.produced,
            failed=args.failed,
            pending=args.pending,
            reason=args.reason,
            ok=not args.not_ok,
        )
        print(json.dumps(entry, ensure_ascii=False))
        return 0

    rows = read(args.stage, limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("(帳本是空的)")
        return 0
    for row in rows:
        bits = [row.get("ts", "")[:19], f"{row.get('stage', ''):22}"]
        for name, label in [("intake", "收"), ("produced", "產"),
                            ("failed", "壞"), ("pending", "待")]:
            if name in row:
                bits.append(f"{label}={row[name]}")
        if not row.get("ok", True):
            bits.append("整批失敗")
        if row.get("reason"):
            bits.append(f"— {row['reason']}")
        print("  ".join(bits))
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
