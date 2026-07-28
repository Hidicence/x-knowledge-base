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
    ~/.openclaw/openclaw.json 的 channels.telegram（沿用既有設定）

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

import xkb_paths
import health_check_pipeline as hc

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 20


def _from_openclaw_config() -> tuple[str, str]:
    """沿用 OpenClaw 既有的 Telegram 設定，免得同一組憑證要維護兩份。"""
    path = Path(os.getenv("OPENCLAW_JSON", str(Path.home() / ".openclaw" / "openclaw.json")))
    if not path.exists():
        return "", ""
    try:
        with path.open(encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return "", ""

    tg = (cfg.get("channels") or {}).get("telegram") or {}
    token = tg.get("botToken") or tg.get("bot_token") or ""
    chat_id = ""
    for key in ("chatId", "chat_id", "defaultChatId", "owner"):
        if tg.get(key):
            chat_id = str(tg[key])
            break
    if not chat_id:
        # 有些版本把收件人放在 allowlist / admins 之類的清單裡
        for key in ("allowedChatIds", "admins", "allowlist"):
            value = tg.get(key)
            if isinstance(value, list) and value:
                chat_id = str(value[0])
                break
    return token, chat_id


def resolve_telegram() -> tuple[str, str]:
    token = os.getenv("XKB_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("XKB_TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        return token, chat_id

    tg = xkb_paths.load_config().get("telegram") or {}
    token = token or tg.get("bot_token", "")
    chat_id = chat_id or str(tg.get("chat_id", ""))
    if token and chat_id:
        return token, chat_id

    fallback_token, fallback_chat = _from_openclaw_config()
    return token or fallback_token, chat_id or fallback_chat


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
        hc.check_staging_backlog(),
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
