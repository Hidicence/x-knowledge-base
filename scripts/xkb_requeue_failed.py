#!/usr/bin/env python3
"""把失敗的項目放回佇列——有次數上限，而且放棄之後不再回來。

2026-09-01 到 09-09 模型設定壞掉那八天留下 49 筆 failed。根因修好之後它們仍然
停在那裡：worker 只挑 status == "todo"，而 sync_tiege_queue 明文「保留 failed/
skipped，由操作者自行重設」。也就是說**修好根因不會讓它們自己好**，要人手動改
佇列——2026-09-10 那次就是我手動重設的。

但「一律自動重試」也不對。手動重試那兩輪之後剩下的 10 筆，全部是被供應商以 403
「上游服务拒绝访问」擋掉的內容，而同一把金鑰跑一般請求正常——那是內容被上游擋，
不是暫時性故障。無限重試等於每晚固定燒一次錢又永遠不會成功。

所以規則是：**重試有次數上限，而且到達上限就明確放棄。**

    failed  ──(attempts < MAX 且距上次夠久)──▶  todo
    failed  ──(attempts >= MAX)────────────▶  abandoned

`abandoned` 是終點，跟 `failed` 分開是為了讓「還會再試」和「已經不試了」在報表上
看得出來。放棄不是刪除：項目與最後一次的錯誤都留著，隨時可以手動放回去。

退避是為了不要在同一個晚上把三次機會用完——供應商的暫時性故障（429/503）要隔一
段時間才有意義，而內容被擋（403）隔多久都一樣，靠的是次數上限收斂。

用法：
    python3 scripts/xkb_requeue_failed.py            # 依規則放回
    python3 scripts/xkb_requeue_failed.py --dry-run
    python3 scripts/xkb_requeue_failed.py --reset-abandoned   # 手動把放棄的放回
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_console
import xkb_failures
import xkb_paths

xkb_console.use_utf8()

# 試幾次才放棄。每天一輪的話，一筆內容被上游擋住的項目會在三天內收斂。
MAX_ATTEMPTS = 3

# 兩次嘗試之間至少隔多久。批次一天跑一次，所以這個值只在手動連跑時起作用——
# 它防的是「同一個晚上把三次機會用完」。
BACKOFF = timedelta(hours=20)


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _eligible(item: dict, now: datetime) -> bool:
    last = _parse_ts(item.get("finished_at") or item.get("started_at") or "")
    if last is None:
        return True          # 沒有時間就當作可以試——時間讀不出來不該讓它卡死
    return now - last >= BACKOFF


def requeue(queue_path: Path, *, dry_run: bool = False,
            max_attempts: int = MAX_ATTEMPTS) -> dict[str, int]:
    """回傳 {requeued, abandoned, waiting}。"""
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        xkb_failures.note("requeue failed items", err, detail=str(queue_path))
        return {"requeued": 0, "abandoned": 0, "waiting": 0, "error": 1}

    items = data.get("items", []) if isinstance(data, dict) else data
    now = datetime.now(timezone.utc)
    counts = {"requeued": 0, "abandoned": 0, "waiting": 0}

    for item in items:
        if item.get("status") != "failed":
            continue
        attempts = int(item.get("attempts") or 1)
        if attempts >= max_attempts:
            item["status"] = "abandoned"
            item["abandoned_at"] = now.isoformat()
            counts["abandoned"] += 1
            continue
        if not _eligible(item, now):
            counts["waiting"] += 1
            continue
        item["status"] = "todo"
        item["attempts"] = attempts + 1
        counts["requeued"] += 1

    if not dry_run and (counts["requeued"] or counts["abandoned"]):
        try:
            queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except OSError as err:
            xkb_failures.note("requeue failed items", err, detail=str(queue_path))
            return {**counts, "error": 1}
    return counts


def reset_abandoned(queue_path: Path, *, dry_run: bool = False) -> int:
    """手動把放棄的全部放回佇列，次數歸零。給「供應商換了、值得再試一輪」用。"""
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    items = data.get("items", []) if isinstance(data, dict) else data
    n = 0
    for item in items:
        if item.get("status") == "abandoned":
            item["status"] = "todo"
            item["attempts"] = 0
            item.pop("abandoned_at", None)
            n += 1
    if n and not dry_run:
        queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset-abandoned", action="store_true",
                        help="把已放棄的全部放回佇列（次數歸零）")
    parser.add_argument("--queue", type=Path, default=xkb_paths.QUEUE_PATH)
    args = parser.parse_args()

    if not args.queue.exists():
        print(f"佇列不存在：{args.queue}")
        return 1

    if args.reset_abandoned:
        n = reset_abandoned(args.queue, dry_run=args.dry_run)
        print(f"放回佇列：{n} 筆（次數歸零）")
        return 0

    counts = requeue(args.queue, dry_run=args.dry_run)
    if counts.get("error"):
        print("佇列讀寫失敗——詳見 stderr")
        return 1

    bits = []
    if counts["requeued"]:
        bits.append(f"放回 {counts['requeued']} 筆")
    if counts["abandoned"]:
        bits.append(f"放棄 {counts['abandoned']} 筆（已達 {MAX_ATTEMPTS} 次上限）")
    if counts["waiting"]:
        bits.append(f"{counts['waiting']} 筆還在退避期")
    print("；".join(bits) if bits else "沒有需要處理的失敗項目")
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
