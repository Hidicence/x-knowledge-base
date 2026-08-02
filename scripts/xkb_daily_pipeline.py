#!/usr/bin/env python3
"""每日攝取排程：把外部來源變成可召回的知識。

對話捕捉已經由 agent hook 自動處理（事件驅動）。但 X 書籤、YouTube、
GitHub 不會通知我們有新東西，所以外部攝取這一段只能靠排程。

階段（任何一段失敗，後面的照跑，最後一起回報）：

    1. 抓書籤        crawl_bookmarks_graphql.py
    2. 產卡片        run_bookmark_worker.py    ← 需要 LLM
    3. 重建搜尋索引  build_search_index.sh
    4. 補向量        build_vector_index.py --incremental
    5. 回報

為什麼不是「一段失敗就整個中止」：

    2026 年那次靜默故障的教訓是「壞了沒人知道」，不是「壞了還繼續跑」。
    LLM 斷線時第 2 段會失敗，但第 3、4 段仍然該把既有卡片索引好——
    整個中止只會讓已經抓到的東西也用不了。

    真正重要的是**失敗一定要出聲**。所以：任何一段失敗就送通知，
    而且通知走純 HTTP，不經過 LLM——LLM 掛掉正是最需要收到通知的時候。

Usage:
    python3 scripts/xkb_daily_pipeline.py                # 全跑
    python3 scripts/xkb_daily_pipeline.py --dry-run      # 只列出會做什麼
    python3 scripts/xkb_daily_pipeline.py --skip-fetch   # 不抓新的，只處理積欠
    python3 scripts/xkb_daily_pipeline.py --no-notify
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths

STATUS_PATH = xkb_paths.XKB_DATA_DIR / "daily-pipeline-status.json"
# 每一段的上限。卡住比失敗更糟——失敗會回報，卡住只會讓明天的排程疊上來。
STAGE_TIMEOUT = 3600


def stage(name: str, cmd: list[str], *, needs_llm: bool = False) -> dict:
    started = time.time()
    print(f"\n── {name} ──", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    try:
        proc = subprocess.run(
            cmd, cwd=str(xkb_paths.SKILL_DIR), env=xkb_paths.subprocess_env(),
            timeout=STAGE_TIMEOUT, capture_output=True, text=True,
        )
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        for line in tail:
            print("  " + line, flush=True)
        ok = proc.returncode == 0
        error = "" if ok else (proc.stderr or proc.stdout or "").strip()[-400:]
    except subprocess.TimeoutExpired:
        ok, error = False, f"逾時（超過 {STAGE_TIMEOUT}s）"
    except OSError as exc:
        ok, error = False, f"{type(exc).__name__}: {exc}"
    result = {
        "stage": name, "ok": ok, "needs_llm": needs_llm,
        "seconds": round(time.time() - started, 1), "error": error,
    }
    print(f"  {'完成' if ok else '失敗'}（{result['seconds']}s）", flush=True)
    return result


def counts() -> dict:
    bookmarks = len(list(xkb_paths.BOOKMARKS_DIR.rglob("*.md")))
    cards = len(list(xkb_paths.CARDS_DIR.glob("*.md")))
    carded = {p.stem for p in xkb_paths.CARDS_DIR.glob("*.md")}
    pending = len({p.stem for p in xkb_paths.BOOKMARKS_DIR.rglob("*.md")} - carded)
    return {"bookmarks": bookmarks, "cards": cards, "uncarded": pending}


def notify(results: list[dict], before: dict, after: dict) -> None:
    """只在有壞消息時出聲。每天報平安會讓人忽略它，那就等於沒有通知。"""
    failed = [r for r in results if not r["ok"]]
    if not failed:
        return
    try:
        from health_check_notify import resolve_telegram, send_telegram
    except ImportError:
        print("（通知模組不可用，僅記錄狀態檔）", file=sys.stderr)
        return
    lines = [
        "XKB 每日攝取有階段失敗",
        "",
        *[f"✗ {r['stage']}：{r['error'][:120]}" for r in failed],
        "",
        f"書籤 {after['bookmarks']}（+{after['bookmarks'] - before['bookmarks']}）",
        f"卡片 {after['cards']}（+{after['cards'] - before['cards']}）",
        f"待產卡 {after['uncarded']}",
    ]
    if any(r["needs_llm"] for r in failed):
        lines += ["", "註：失敗的階段需要 LLM，先確認模型供應商是否可用。"]
    try:
        token, chat_id = resolve_telegram()
        send_telegram(token, chat_id, "\n".join(lines))
        print("已送出失敗通知", flush=True)
    except Exception as exc:
        print(f"（通知送不出去：{exc}）", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true", help="不抓新書籤，只處理已有的積欠")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--batch", type=int, default=40, help="一次產幾張卡")
    args = parser.parse_args(argv)

    python = sys.executable
    plan: list[tuple[str, list[str], bool]] = []
    if not args.skip_fetch:
        plan.append(("抓書籤", [python, "scripts/crawl_bookmarks_graphql.py"], False))
    plan += [
        ("產卡片", [python, "scripts/run_bookmark_worker.py", "--limit", str(args.batch)], True),
        ("重建搜尋索引", ["bash", "scripts/build_search_index.sh"], False),
        ("補向量索引", [python, "scripts/build_vector_index.py", "--incremental"], True),
    ]

    if args.dry_run:
        for name, cmd, needs_llm in plan:
            print(f"  {name:14}{' (需要 LLM)' if needs_llm else '':<12} {' '.join(cmd)}")
        return 0

    before = counts()
    print(f"開始：書籤 {before['bookmarks']}、卡片 {before['cards']}、待產卡 {before['uncarded']}")
    results = [stage(name, cmd, needs_llm=needs_llm) for name, cmd, needs_llm in plan]
    after = counts()

    status = {
        "schema": "xkb-daily-pipeline.v1",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(r["ok"] for r in results),
        "stages": results,
        "before": before,
        "after": after,
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n結束：書籤 {after['bookmarks']}（+{after['bookmarks'] - before['bookmarks']}）、"
          f"卡片 {after['cards']}（+{after['cards'] - before['cards']}）、待產卡 {after['uncarded']}")
    print(f"狀態寫入 {STATUS_PATH}")
    if not args.no_notify:
        notify(results, before, after)
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
