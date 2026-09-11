#!/usr/bin/env python3
"""
XKB 健檢告警 — 給排程用的死人開關

跑一次 health_check_pipeline，有紅燈就發 Telegram，全綠就安靜退出。

刻意不依賴 LLM、不經過 agent：
死人開關如果依賴「最可能死掉的東西」，它就會跟著一起死。
2026-07-28 查到 VPS 上十幾個 XKB 排程連續失敗 14 次（約兩週），
原因是模型供應商回 503——那些排程每一個都要叫起 agent。
純 Python + 直接打 Telegram API，模型掛掉時它照樣會出聲。

設定（依序，先找到先用）：
    環境變數 XKB_TELEGRAM_BOT_TOKEN / XKB_TELEGRAM_CHAT_ID
    .xkb.json 的 {"telegram": {"bot_token": "...", "chat_id": "..."}}
    XKB_ENV_FILE        optional dotenv file for runtime credential injection

Usage:
    python3 scripts/health_check_notify.py
    python3 scripts/health_check_notify.py --always    # 全綠也回報
    python3 scripts/health_check_notify.py --dry-run   # 只印，不發送
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import xkb_paths
import health_check_pipeline as hc
from runtime_config import runtime_env

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 20
ALERT_STATE_PATH = xkb_paths.WORKSPACE / "memory" / "health-check-alert-state.json"


def resolve_telegram() -> tuple[str, str]:
    settings = runtime_env()
    token = settings.get("XKB_TELEGRAM_BOT_TOKEN", "")
    chat_id = settings.get("XKB_TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        return token, chat_id
    tg = xkb_paths.load_config().get("telegram") or {}
    return token or tg.get("bot_token", ""), chat_id or str(tg.get("chat_id", ""))


def send_telegram(token: str, chat_id: str, text: str) -> None:
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    request = urllib.request.Request(TELEGRAM_API.format(token=token), data=payload)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = json.loads(response.read().decode("utf-8", "replace"))
    if not body.get("ok"):
        raise RuntimeError(f"telegram rejected: {body}")


# Two things arrive as "a red light" and they are not the same thing. One is
# the system being broken, which is nobody's decision. The other is work
# waiting on a judgement only Pan can make. Reporting them in one list, in the
# vocabulary of the check that produced them, is why the daily message stopped
# being read: it never answered "is this mine to deal with?"
# 「等你決定」只放真的只有 Pan 能決定的事。
#
# staging_backlog 與 governance_actionable 原本在這裡，而 2026-09-12 實測：那
# 180 筆待審裡 106 筆是 safe_promotion，治理會自動吸收，一筆都不需要人。積起來
# 的原因是治理每晚上限 20 筆而候選進來得更快——那是吞吐量設定，不是判斷。
#
# 把吞吐量問題放進「等你決定」，等於每天叫人決定一件系統自己會做的事。這則訊息
# 會被停止閱讀就是這樣來的。兩者現在都當一般紅燈處理：追不上才出聲，而且說的是
# 「追不上，調高 LIMIT」，不是「請你審核 180 筆」。
#
# 真正屬於這一類的是 governance 的 proposal（要不要開新的 wiki 主題）與 overdue，
# 那兩項由 _decision_lines 直接讀 actionable_counts，不需要整個 section 都算決定。
DECISION_SECTIONS: set[str] = set()

# Section names are internal identifiers. This is the only place they are
# turned into something worth reading.
FAULT_LABELS = {
    "wiki_canonical": "wiki 檔案結構",
    "recall_wiki_source": "召回讀不到 wiki",
    "recall_live": "召回本身跑不動",
    "recall_telemetry": "召回沒有留下紀錄",
    "semantic_index": "語意索引",
    "topic_map": "分類對應表",
    # 這兩個原本在 DECISION_SECTIONS 裡，所以從來不需要標籤（決定類不走
    # FAULT_LABELS）。移出來之後缺口就露出來了：它們紅燈時會印出內部識別字。
    "staging_backlog": "知識候選消化不完",
    "governance_actionable": "治理吸收追不上",
    "external_dependencies": "外部相依不在位",
    "card_production": "書籤沒有變成知識卡",
    "pipeline_ledger": "有階段沒在動",
    "index_freshness": "索引落後於卡片",
    "provenance_markers": "知識來源標記不一致",
    "conversation_capture": "對話沒有被記錄下來",
}


def _governance_counts(sections: list[dict]) -> dict[str, int]:
    for section in sections:
        if section.get("name") == "governance_actionable":
            return section.get("actionable_counts") or {}
    return {}


def _decision_lines(sections: list[dict]) -> list[str]:
    """只放真的需要 Pan 做決定的事。

    「等你決定」底下原本會出現「127 條治理下一輪會處理」——標題說要你決定，
    內容自己說系統會處理。實測那 127 條裡有 126 條是 safe_promotion，治理
    下一輪會自動吸收，一條都不需要人。

    每天叫人決定一件他不需要決定的事，是這則訊息會被停止閱讀的直接原因。
    所以這裡只留「只有你能決定」的那一類，其餘搬到現況那一段。
    """
    counts = _governance_counts(sections)
    lines = []
    if counts.get("proposal"):
        lines.append(f"  {counts['proposal']} 條想開新的 wiki 主題——要不要開，只有你能決定")
    if counts.get("overdue"):
        lines.append(f"  {counts['overdue']} 條因為太舊被隔離，沒有刪除")
    return lines


def _governance_progress_lines(sections: list[dict]) -> list[str]:
    """治理自己會處理的量——這是現況，不是待辦。"""
    counts = _governance_counts(sections)
    lines = []
    # pending 扣掉需要人決定的（提案）與已隔離的，剩下就是治理下一輪會吸收的。
    auto = (counts.get("pending", 0) - counts.get("proposal", 0)
            - counts.get("quarantine", 0))
    if auto > 0:
        lines.append(f"消化中：{auto} 條知識候選，治理下一輪自動吸收")
    held = counts.get("held", 0)
    if held > 0:
        lines.append(f"暫留：{held} 條證據或信心不足，留著沒丟——不用你做什麼")
    return lines


def build_message(sections: list[dict], failures: list[tuple[str, str]]) -> str:
    host = socket.gethostname()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    faults = [(name, msg) for name, msg in failures if name not in DECISION_SECTIONS]
    decisions = _decision_lines(sections)

    if faults:
        head = f"XKB 有 {len(faults)} 個地方壞了"
    elif decisions:
        head = "XKB 運作正常，有事情等你決定"
    else:
        head = "XKB 一切正常"

    lines = [head, f"{host} · {stamp}"]

    if faults:
        lines += ["", "● 壞掉了"]
        for name, msg in faults:
            lines.append(f"  {FAULT_LABELS.get(name, name)}")
            lines.append(f"    {msg}")

    if decisions:
        lines += ["", "● 等你決定"] + decisions

    inventory = _inventory_lines() + _governance_progress_lines(sections)
    if inventory:
        lines += [""] + inventory
    return "\n".join(lines)


def _inventory_lines() -> list[str]:
    """A one-glance sense of size, so the message says how things are going.

    Best effort: a failure to count must never stop an alert being sent.
    """
    lines = []
    try:
        cards = len(xkb_paths.card_files())
        lines.append(f"知識庫：{cards:,} 張卡片")
    except OSError:
        pass
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from xkb_pending_work import pending_breakdown
        counts = pending_breakdown(xkb_paths.BOOKMARKS_DIR, xkb_paths.CARDS_DIR)
        # 「排隊中」會自己消化掉，「卡住」不會——沒有任何排程會再碰 failed。
        # 兩者混在同一個「待消化：N」裡，等於把一件要你決定的事寫成一件會自己
        # 好的事，而那個數字每天都在那裡，看久了就不再是訊息。
        if counts["actionable"]:
            lines.append(f"待消化：{counts['actionable']} 筆書籤排隊中")
        if counts["stuck"]:
            lines.append(f"重試中：{counts['stuck']} 筆轉卡失敗，排程會再試"
                         f"（最多三次）")
        # 已放棄跟還在重試要分開。混在一起的話，「系統還在努力」和「系統已經
        # 不管了」在報表上長得一樣——而只有後者需要你做決定。
        if counts.get("abandoned"):
            lines.append(f"已放棄：{counts['abandoned']} 筆重試用盡，不會再試——"
                         f"要再給一次機會：xkb_requeue_failed.py --reset-abandoned")
        # 刻意跳過的也要出現。原本它既不算 actionable 也不算 stuck，於是從每一份
        # 報告裡消失——而「看不見」跟「處理掉了」在報表上長得一樣。
        if counts["skipped"]:
            lines.append(f"已跳過：{counts['skipped']} 筆你決定不收的，留著沒刪")
    except Exception:
        pass

    # Topic pages only ever grow. Nothing schedules synthesis — it rewrites
    # knowledge with a model, which is a call to make deliberately — so the
    # count belongs here, where a growing number is visible, rather than in a
    # tool nobody remembers to run. openclaw-agent-workflows reached 3,278
    # bullets before anyone counted.
    try:
        # Ask the tool that would do the work what is left to do. Counting
        # "- " lines here counted the conclusions synthesis had just written,
        # so this line asked for four pages on 2026-08-31 and three of them had
        # been digested the evening before.
        from xkb_synthesize_topic import undigested

        def _needs_digesting(path) -> bool:
            body = path.read_text(encoding="utf-8", errors="ignore")
            # 封存的主題不再要求消化。一個你已經決定不做的待辦是噪音，
            # 而噪音正是讓人停止閱讀這則訊息的東西。
            if re.search(r"^status:\s*archived\s*$", body, re.M):
                return False
            return len(undigested(body)[1]) >= 200

        bloated = [
            path.stem for path in sorted(xkb_paths.WIKI_TOPICS_DIR.glob("*.md"))
            if _needs_digesting(path)
        ]
        if bloated:
            lines.append(f"待整理：{len(bloated)} 個主題頁條列過多"
                         f"（{'、'.join(bloated[:2])}⋯，用 xkb_synthesize_topic.py 消化）")
    except Exception:
        pass
    return lines


def _failure_key(section: str, message: str) -> str:
    """Stable identity for one check+error; timestamps must not make it new."""
    import hashlib
    normalized = " ".join(message.split())
    return hashlib.sha256(f"{section}\0{normalized}".encode("utf-8")).hexdigest()


def _load_alert_state() -> dict:
    try:
        data = json.loads(ALERT_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_alert_state(active: dict) -> None:
    ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ALERT_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"active": active}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(ALERT_STATE_PATH)


def deduplicate_failures(failures: list[tuple[str, str]], state: dict) -> tuple[list[tuple[str, str]], dict, list[str]]:
    """Return only new/changed errors, retaining active errors until recovery."""
    previous = state.get("active", {}) if isinstance(state.get("active", {}), dict) else {}
    current = {_failure_key(section, msg): {"section": section, "message": msg}
               for section, msg in failures}
    changed = [(item["section"], item["message"]) for key, item in current.items() if key not in previous]
    recovered = [item["section"] for key, item in previous.items() if key not in current]
    return changed, {"active": current}, recovered


def main() -> int:
    parser = argparse.ArgumentParser(description="Run XKB health check and alert on failure")
    parser.add_argument("--always", action="store_true", help="全綠也送出通知")
    parser.add_argument("--dry-run", action="store_true", help="只印出訊息，不實際發送")
    args = parser.parse_args()

    # 清單在 health_check_pipeline.CHECKS，不在這裡。
    #
    # 原本這裡自己列了一份，而那份跟 pipeline 那份不一樣：少了 card_production
    # 與 pipeline_ledger（所以那兩項在每日訊息裡從來沒出現過），多了
    # external_dependencies（所以手動跑 pipeline 時它不被檢查）。
    sections = hc.run_all()
    failures = [
        (section["name"], check["msg"])
        for section in sections
        for check in section["checks"]
        if not check["ok"]
    ]

    message = build_message(sections, failures)
    print(message)

    # Persist state even when no Telegram credentials exist. This makes repeated
    # heartbeat/cron runs quiet and keeps recovery detectable on the next run.
    alert_failures, next_state, recovered = deduplicate_failures(failures, _load_alert_state())
    _save_alert_state(next_state["active"])
    if failures and not alert_failures and not recovered and not args.always:
        print("\n（狀態未變：略過重複通知）")
        return 1
    if not failures and recovered and not args.always:
        print("\n（排程異常已恢復：略過全綠通知）")
        return 0
    if alert_failures and not args.always:
        message = build_message(sections, alert_failures)

    if not failures and not args.always:
        return 0
    if args.dry_run:
        print("\n（--dry-run：沒有實際發送）")
        return 1 if failures else 0

    token, chat_id = resolve_telegram()
    if not token or not chat_id:
        print("\n找不到 Telegram 設定——健檢結果只留在 log 裡。", file=sys.stderr)
        print("設定 XKB_TELEGRAM_BOT_TOKEN / XKB_TELEGRAM_CHAT_ID 或 .xkb.json 的 telegram 區塊。",
              file=sys.stderr)
        return 2

    try:
        send_telegram(token, chat_id, message)
        print("\n已送出 Telegram 通知。")
    except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
        # 通知失敗本身就是要出聲的事：離開碼非 0，cron log 留下原因
        print(f"\nTelegram 發送失敗：{exc}", file=sys.stderr)
        return 3

    return 1 if failures else 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
