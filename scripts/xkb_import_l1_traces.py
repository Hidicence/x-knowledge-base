#!/usr/bin/env python3
"""把 OpenClaw 寫出的 L1 軌跡送進知識服務——讓 VPS 上的對話也進共享記憶。

為什麼需要這支:

    VPS 的 OpenClaw 用 `xkb-agent-end-capture` 這個 extension 捕捉對話，
    寫成 `runtime/l1-traces/*.json`。而知識服務把 turn 存在 SQLite。
    **兩個分開的儲存體**——所以 VPS 上的對話從來沒有進到候選記憶池，
    「跨 Agent 共享」實際上只有讀是共享的，寫不是。

    (同一個模式在這個專案出現過兩次：卡片在 gbrain、wiki 在自己的索引，
    也是查一個看不到另一個。)

不改 OpenClaw 的 extension，改用讀檔轉送——那是第三方程式，
改了會在下次更新被蓋掉，而且它壞掉會影響 agent 本身。
軌跡檔已經寫好了，讀它是安全的。

冪等:trace_id 穩定，已匯入的會被服務用同一個 turn_id 擋掉。

Usage:
    python3 scripts/xkb_import_l1_traces.py --dry-run
    python3 scripts/xkb_import_l1_traces.py --since 3
    python3 scripts/xkb_import_l1_traces.py --limit 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths
from xkb_noise import is_noise   # 雜訊清單只有一份

TRACE_DIR = xkb_paths.XKB_DATA_DIR / "runtime" / "l1-traces"
STATE_PATH = xkb_paths.XKB_DATA_DIR / "l1-import-state.json"
DEFAULT_URL = os.getenv("XKB_MEMORY_SERVICE_URL", "http://127.0.0.1:18972")
# 只送人看得懂的對話。工具呼叫與中繼資料留在軌跡檔裡當證據，
# 但把它們送進候選池只會製造雜訊——那正是 memmy 的系統雜訊過濾在擋的東西。
ROLES = {"user", "assistant"}
MAX_CHARS = 4000


def local_token(url: str) -> str:
    """在同一台機器上時,直接讀服務的 auth 檔。

    排程需要 token 才能寫入,但把 token 放進 crontab 等於公開它——
    `crontab -l` 就看得到,而且會被備份、被複製。同一台機器上能讀
    auth.json 的人本來就有服務的完整權限,所以這樣讀沒有多給任何東西。

    非 loopback 就不做這件事:那代表是從別台機器連進來,必須明確給 token。
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    if parsed.username or parsed.password or hostname not in {"127.0.0.1", "localhost", "::1"}:
        return ""
    path = Path(os.getenv("XKB_SERVICE_AUTH", str(Path.home() / ".xkb-runtime" / "auth.json")))
    try:
        tokens = json.loads(path.read_text(encoding="utf-8")).get("tokens") or {}
    except (OSError, json.JSONDecodeError):
        return ""
    return next(iter(tokens), "")


def post(path: str, payload: dict, url: str, token: str) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url.rstrip("/") + path, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def conversation(trace: dict) -> tuple[str, str]:
    """從軌跡裡抽出 (最後一個使用者訊息, 最後一個助理回覆)。"""
    messages = trace.get("content")
    if not isinstance(messages, list):
        return "", ""
    said: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        if role not in ROLES:
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "\n".join(str(part.get("text", "")) for part in content
                             if isinstance(part, dict) and part.get("type") == "text").strip()
        else:
            text = ""
        if text:
            said[role] = text[:MAX_CHARS]
    return said.get("user", ""), said.get("assistant", "")


def load_state() -> set[str]:
    try:
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("imported", []))
    except (OSError, json.JSONDecodeError):
        return set()


def save_state(imported: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 只留最近的一批。這個檔案是為了少打幾次 API，不是稽核紀錄——
    # 真正的去重由服務端的 turn_id 負責，這裡漏了也不會重複寫入。
    STATE_PATH.write_text(json.dumps({"imported": sorted(imported)[-5000:]},
                                     ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token", default=os.getenv("XKB_SERVICE_TOKEN", ""))
    parser.add_argument("--source", default="openclaw", help="記錄在 session 上的來源,用來分辨是哪個 agent")
    parser.add_argument("--since", type=int, default=7, help="只匯入最近幾天的軌跡")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reimport", action="store_true", help="忽略本機狀態,重跑一次")
    args = parser.parse_args(argv)

    if not TRACE_DIR.exists():
        print(f"找不到軌跡目錄:{TRACE_DIR}", file=sys.stderr)
        return 1
    token = args.token or local_token(args.url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.since)
    done = set() if args.reimport else load_state()

    candidates = []
    skipped_noise = 0
    for path in sorted(TRACE_DIR.glob("*.json")):
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if trace.get("schema") != "xkb-l1-trace.v1":
            continue
        trace_id = str(trace.get("trace_id") or path.stem)
        if trace_id in done:
            continue
        try:
            observed = datetime.fromisoformat(str(trace.get("observed_at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if observed < cutoff:
            continue
        query, answer = conversation(trace)
        if not query or not answer:
            continue        # 沒有一問一答就沒有東西可以蒸餾
        if is_noise(query, answer):
            skipped_noise += 1
            continue
        candidates.append((trace_id, trace, query, answer))

    candidates = candidates[: args.limit]
    print(f"  待匯入 {len(candidates)} 筆(近 {args.since} 天;已匯入 {len(done)} 筆,擋掉雜訊 {skipped_noise} 筆)")
    if args.dry_run:
        for trace_id, trace, query, _ in candidates[:10]:
            print(f"    {trace_id[:28]}  {str(trace.get('session_id'))[:14]}  {query[:44]}")
        return 0

    imported = failed = 0
    for trace_id, trace, query, answer in candidates:
        session_key = str(trace.get("session_id") or trace.get("episode_id") or trace_id)
        turn_id = f"l1:{trace_id.split(':')[-1]}"
        try:
            session = post("/v1/sessions/open", {
                "source": args.source,
                "agent_id": str(trace.get("agent_id") or args.source),
                "session_key": session_key,
            }, args.url, token)
            post("/v1/turns/start", {
                "session_id": session["session_id"], "turn_id": turn_id, "query": query,
            }, args.url, token)
            post(f"/v1/turns/{turn_id}/complete", {
                "session_id": session["session_id"], "query": query, "answer": answer,
                "episode_id": str(trace.get("episode_id") or ""),
            }, args.url, token)
            done.add(trace_id)
            imported += 1
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as exc:
            failed += 1
            if failed <= 3:
                print(f"    失敗 {trace_id[:24]}:{type(exc).__name__} {exc}", file=sys.stderr)

    save_state(done)
    print(f"  匯入 {imported} 筆,失敗 {failed} 筆")
    return 1 if failed and not imported else 0


if __name__ == "__main__":
    raise SystemExit(main())
