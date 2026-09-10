#!/usr/bin/env python3
"""哪些腳本真的有人在跑——刪東西之前唯一該相信的證據。

scripts/ 底下有五十幾支可以直接執行的腳本。其中有些是遷移時期的一次性工具，
有些是每天跑的管線，有些是 Pan 偶爾手動叫的。從程式碼裡分不出來：

  - 「有幾個檔案引用它」分不出來。action_recall 只被 recall_router 引用一次，
    但它在每一次召回裡都會跑；migrate_schema 同樣只被引用一次，而它上一次
    執行可能是半年前。
  - 檔名分不出來。full_sync_v2 聽起來像現役的，init_rebuild_v2 聽起來像一次
    性的，實際上要看誰在叫它。
  - 我分不出來。哪支是手動工作流程的一環，只有用的人知道。

所以這裡不做分類，只做量測：每次有人把某支腳本當程式跑（不是 import），就記
一行。跑一段時間之後，「從來沒被叫過」就成了證據，而不是猜測——那正是「先
標記、記錄呼叫者，不要一開始就刪」需要的那份資料。

刻意的設計：
  - 只在當成主程式執行時記，import 不記。被 import 的次數不代表被使用。
  - 記的是腳本名、時間、參數的「形狀」（旗標名，不含值）。參數值可能含有
    路徑或查詢內容，那不是這份紀錄該保存的東西。
  - 寫不進去絕不影響腳本本身。這是觀測，不是功能。

用法（腳本裡）:
    if __name__ == "__main__":
        xkb_usage.record(__file__)
        raise SystemExit(main())

用法（看結果）:
    python3 scripts/xkb_usage.py report
    python3 scripts/xkb_usage.py report --unused
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths

USAGE_PATH = xkb_paths.XKB_DATA_DIR / "script-usage.jsonl"
SCRIPTS_DIR = xkb_paths.SCRIPTS_DIR


def record(script: str | Path, argv: list[str] | None = None,
           path: Path | None = None) -> None:
    """記一次執行。任何失敗都吞掉——觀測不能影響被觀測的東西。"""
    try:
        argv = sys.argv[1:] if argv is None else argv
        # 只留旗標名。參數值可能是路徑或查詢字串，那是使用者的內容，
        # 不是「這支腳本有沒有被用到」需要的資訊。
        flags = sorted({a.split("=", 1)[0] for a in argv if a.startswith("-")})
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "script": Path(script).stem,
            "flags": flags,
        }
        target = path or USAGE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 觀測失敗不能弄掛腳本
        pass


def read(path: Path | None = None) -> list[dict]:
    target = path or USAGE_PATH
    if not target.exists():
        return []
    rows = []
    try:
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("script"):
            rows.append(row)
    return rows


def runnable_scripts(scripts_dir: Path | None = None) -> list[str]:
    """可以當程式跑的腳本——有 __main__ 區塊的那些。"""
    scripts_dir = scripts_dir or SCRIPTS_DIR
    found = []
    for path in sorted(scripts_dir.glob("*.py")):
        try:
            if '__name__ == "__main__"' in path.read_text(encoding="utf-8", errors="ignore"):
                found.append(path.stem)
        except OSError:
            continue
    return found


def summary(path: Path | None = None,
            scripts_dir: Path | None = None) -> dict[str, dict]:
    """每支腳本被叫過幾次、最後一次什麼時候。從沒被叫過的也要在裡面。"""
    counts = Counter(row["script"] for row in read(path))
    last: dict[str, str] = {}
    for row in read(path):
        last[row["script"]] = row.get("ts", "")
    result = {}
    for name in runnable_scripts(scripts_dir):
        result[name] = {"runs": counts.get(name, 0), "last": last.get(name, "")}
    # 已經被刪掉、但紀錄裡還有的腳本也列出來，否則「這支還在被叫」會消失。
    for name in counts:
        if name not in result:
            result[name] = {"runs": counts[name], "last": last.get(name, ""),
                            "missing": True}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report", help="每支腳本被叫過幾次")
    rep.add_argument("--unused", action="store_true", help="只列從沒被叫過的")
    rep.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = summary()
    if args.unused:
        data = {k: v for k, v in data.items() if v["runs"] == 0}

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if not data:
        print("(沒有符合的腳本)")
        return 0

    total_rows = len(read())
    if total_rows == 0:
        print("紀錄還是空的——量測剛開始，現在還不能拿它當「沒人用」的證據。")
        print()

    width = max(len(k) for k in data)
    for name, info in sorted(data.items(), key=lambda kv: (kv[1]["runs"], kv[0])):
        mark = "  ← 已不存在" if info.get("missing") else ""
        when = info["last"][:19] or "從沒被叫過"
        print(f"  {name.ljust(width)}  {info['runs']:>4} 次   {when}{mark}")
    return 0


if __name__ == "__main__":
    record(__file__)
    raise SystemExit(main())
