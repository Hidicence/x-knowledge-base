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


def build_message(sections: list[dict], failures: list[tuple[str, str]]) -> str:
    host = socket.gethostname()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not failures:
        total = sum(len(s["checks"]) for s in sections)
        return f"XKB 健檢全綠（{total} 項）\n{host} · {stamp}"

    lines = [f"XKB 健檢有 {len(failures)} 項紅燈", f"{host} · {stamp}", ""]
    for section, msg in failures:
        lines.append(f"[{section}] {msg}")
    return "\n".join(lines)


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

    sections = [
        hc.check_wiki_canonical(),
        hc.check_recall_wiki_source(),
        hc.check_recall_live(),
        hc.check_recall_telemetry(),
        hc.check_semantic_index(),
        hc.check_topic_map(),
        hc.check_staging_backlog(),
        hc.check_governance_actionable(),
        hc.check_provenance_markers(),
        hc.check_conversation_capture(),
        hc.check_index_freshness(),
    ]
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


if __name__ == "__main__":
    raise SystemExit(main())
