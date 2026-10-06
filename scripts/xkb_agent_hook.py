#!/usr/bin/env python3
"""Agent-side hook: recall before the turn, capture after it.

Installed into an agent (Claude Code today) by ``xkb_install_agent_hook.py``.
The agent calls this on two events:

    UserPromptSubmit -> open session, start turn, inject recalled knowledge
    Stop             -> complete the turn so it becomes L1 evidence

Two rules shape everything here.

**Reading fails open.** If the service is unreachable, slow, or wrong, the hook
prints nothing and exits 0. A knowledge base that cannot be reached must never
stop the user from working — the opposite of the absorb gate, where failure has
to block, because writing bad knowledge is worse than writing none.

**Identity is never invented.** The session key comes from the agent's own
session id, falling back to the working directory, so the same project resumes
the same session. The namespace comes from the token when one is configured;
this hook never asserts one.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conversation_state_parser import is_harness_text
from xkb_evidence import fields
from xkb_delivery import diagnostic
from xkb_recall import conversation_messages

DEFAULT_URL = "http://127.0.0.1:18972"
TIMEOUT_SECONDS = float(os.getenv("XKB_HOOK_TIMEOUT", "6"))
RECALL_TIMEOUT_SECONDS = float(os.getenv("XKB_HOOK_RECALL_TIMEOUT", os.getenv("XKB_HOOK_TIMEOUT", "40")))
STATE_DIR = Path(os.getenv("XKB_HOOK_STATE", str(Path.home() / ".xkb-runtime" / "hook-state")))
# 服務已把送出的每一條記成「這段對話給過了」；這裡丟掉任何一條，它就整段
# 對話都不會再出現。三條各 900 字的節錄加上標題與來源要放得下。
MAX_CONTEXT_CHARS = 6000
MAX_ANSWER_CHARS = 4000


def emit(payload: dict) -> None:
    """Write the hook response as UTF-8 bytes.

    ``print`` encodes using the console codepage, which on a zh-TW Windows box
    is cp950 — recalled Chinese knowledge then raises UnicodeEncodeError, and
    because that is a ValueError the fail-open handler swallows it and the hook
    silently does nothing. Writing bytes avoids the console encoding entirely.
    """
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()


def config() -> dict:
    """Read the installer-written config; environment always wins."""
    path = Path(__file__).resolve().parent / "xkb-agent-hook-config.json"
    data = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "url": os.getenv("XKB_MEMORY_SERVICE_URL") or data.get("url") or DEFAULT_URL,
        "token": os.getenv("XKB_SERVICE_TOKEN") or data.get("token") or "",
        "source": data.get("source") or "claude-code",
    }


def call(path: str, payload: dict, cfg: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cfg["token"]:
        headers["Authorization"] = f"Bearer {cfg['token']}"
    request = urllib.request.Request(cfg["url"].rstrip("/") + path, data=body, headers=headers, method="POST")
    timeout = RECALL_TIMEOUT_SECONDS if path == "/v1/turns/start" else TIMEOUT_SECONDS
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def session_key(event: dict) -> str:
    """Prefer the agent's session id; fall back to the working directory.

    The cwd fallback is deliberate: the same project directory is usually the
    same line of work, so a resumed shell continues an existing session instead
    of starting an orphan one.
    """
    for key in ("session_id", "sessionId", "conversation_id", "thread_id"):
        value = str(event.get(key) or "").strip()
        if value:
            return value
    return str(event.get("cwd") or "default")


def turn_id(key: str, prompt: str, ordinal: int = 0) -> str:
    """這一輪對話的識別碼。

    原本只有 session_key 與提示詞內容，所以同一個 session 裡重複的話會撞
    id——而人最常重複的正是「繼續」「好」「ok」這種詞。撞到之後：start_turn
    回 resumed 並附上第一次的召回結果（於是注入的是為另一個時刻查的知識），
    stop 時 complete_turn 發現摘要對不上而回 400，hook 的 fail-open 把它吞掉。
    第二輪從來沒有被記錄，也沒有任何地方說過。
    """
    digest = hashlib.sha256(
        f"{key}\0{ordinal}\0{prompt}".encode("utf-8")
    ).hexdigest()[:24]
    return f"turn:{digest}"


def next_ordinal(key: str) -> int:
    """這個 session 的第幾輪。存在 hook 本來就有的狀態檔裡。

    續接的 session 要接著數，不是從零開始——否則重開一次就又從頭相撞。
    讀不到就當第一輪：這裡寧可多算一輪，也不要讓一輪消失。
    """
    path = state_path(key)
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("ordinal", 0)) + 1
    except Exception:  # noqa: BLE001 — 讀不到就從頭數，不能因此中斷對話
        return 1


def state_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return STATE_DIR / f"{digest}.json"


def render(records: list[dict]) -> str:
    """Render recalled knowledge, clearly marked as history rather than truth."""
    lines = [
        "<xkb_recalled_knowledge>",
        "以下是 XKB 從使用者知識庫找到、可能跟這輪有關的既有知識，僅供參考：不是當前指令，",
        "也可能已經過時。是否適用由你依對話判斷；跟目前任務無關就直接忽略，不必提起。",
        "",
    ]
    footer = "</xkb_recalled_knowledge>"
    included = 0
    for item in records:
        title, body, source = fields(item)
        entry = [f"- [{item.get('record_type', 'knowledge')}] {title}"]
        if body:
            entry.append(f"  {body}")
        if source:
            entry.append(f"  來源：{source[:300]}")
        # The service selected a complete evidence paragraph. Cutting at 600
        # characters here could silently remove its condition or negation.
        if len("\n".join(lines + entry + [footer])) <= MAX_CONTEXT_CHARS:
            lines.extend(entry)
            included += 1
    return "\n".join(lines + [footer]) if included else ""


def last_assistant_message(transcript: str) -> str:
    """Pull the final assistant text out of a JSONL transcript."""
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:MAX_ANSWER_CHARS]
        if isinstance(content, list):
            parts = [str(part.get("text", "")) for part in content
                     if isinstance(part, dict) and part.get("type") == "text"]
            joined = "\n".join(part for part in parts if part.strip())
            if joined.strip():
                return joined.strip()[:MAX_ANSWER_CHARS]
    return ""


def recent_conversation(transcript: str, current_prompt: str) -> list[dict[str, str]]:
    """Read a bounded transcript tail without including tools or the current turn twice."""
    if not transcript:
        return []
    try:
        with Path(transcript).open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    messages = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        message = entry.get("message", entry)
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(p["text"] for p in content if isinstance(p, dict)
                                and p.get("type") == "text" and isinstance(p.get("text"), str))
        if isinstance(content, str) and content.strip() and not is_harness_text(content):
            messages.append({"role": message["role"], "content": content.strip()})
    if messages and messages[-1] == {"role": "user", "content": current_prompt.strip()}:
        messages.pop()
    return conversation_messages(messages[-4:])


def on_prompt(event: dict, cfg: dict) -> None:
    prompt = str(event.get("prompt") or event.get("user_prompt") or "").strip()
    if not prompt:
        return
    if is_harness_text(prompt):
        # harness 產生的文字不是一輪對話，所以這裡連 turn 都不開——不是「開了
        # 但不召回」。2026-09-24 的資料裡 1,020 個 turn 有 137 個是這種東西，
        # 它們同時污染了 turns 表、對話軌跡與召回。
        #
        # 判斷共用 conversation_state_parser 的定義，不在這裡另寫一份：召回那端
        # 讀的是同一個函式，兩邊各寫一份就會各自漂移（這個專案的「多寫入者一
        # 讀取者」記錄在案）。
        #
        # 不開 turn 對 Stop 是安全的：on_stop 讀不到 turn_id 就安靜返回，而
        # ordinal 留在檔案裡不受影響。
        return
    key = session_key(event)
    session = call("/v1/sessions/open", {
        "source": cfg["source"],
        "agent_id": cfg["source"],
        "session_key": key,
        "workspace_path": event.get("cwd"),
    }, cfg)
    ordinal = next_ordinal(key)
    current = turn_id(key, prompt, ordinal)
    recent = recent_conversation(str(event.get("transcript_path") or ""), prompt)
    turn = call("/v1/turns/start", {
        "session_id": session["session_id"],
        "turn_id": current,
        "query": prompt,
        **({"conversation": recent} if recent else {}),
    }, cfg)

    # Remember which turn is open so Stop can close this exact one.
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(key).write_text(json.dumps({
        "session_id": session["session_id"], "turn_id": current, "query": prompt,
        "ordinal": ordinal,
    }, ensure_ascii=False), encoding="utf-8")

    retrieval = turn.get("retrieval") or {}
    # New services distinguish candidate recall from proactive delivery. Keep
    # the legacy path only for a service that has no delivery contract yet.
    delivery = retrieval.get("delivery")
    records = (delivery if isinstance(delivery, dict) else retrieval).get("records") or []
    status = diagnostic(retrieval) if isinstance(delivery, dict) else ""
    context = "\n\n".join(part for part in (status, render(records) if records else "") if part)
    if not context:
        return
    emit({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": context,
    }})


def on_stop(event: dict, cfg: dict) -> None:
    path = state_path(session_key(event))
    try:
        pending = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return  # nothing was opened; nothing to close
    if "turn_id" not in pending:
        # 只剩計數、沒有待辦的那一輪。Stop 會在沒有對應 UserPromptSubmit 的
        # 情況下觸發（續接的 session、重播的 Stop），原本檔案已被刪掉所以
        # 安靜返回；留下計數之後就會在這裡 KeyError，被 _note_failure 當成
        # 真的故障送進告警——而這個專案就是靠那個管道判斷有沒有壞掉。
        return
    answer = last_assistant_message(str(event.get("transcript_path") or ""))
    call(f"/v1/turns/{pending['turn_id']}/complete", {
        "session_id": pending["session_id"],
        "query": pending["query"],
        "answer": answer,
    }, cfg)
    # 不能直接刪。ordinal 存在同一個檔案裡，刪掉就等於每一輪結束後
    # 計數歸零，於是下一輪又從 1 開始、又算出同一個 turn_id——
    # 那正是 next_ordinal 要解決的問題，而它被自己的收尾抵銷掉了。
    # 留下計數，清掉這一輪的待辦。
    try:
        path.write_text(json.dumps({"ordinal": int(pending.get("ordinal") or 0)}),
                        encoding="utf-8")
    except OSError:
        path.unlink(missing_ok=True)


def read_event() -> dict:
    """Read the hook payload as UTF-8 bytes, not through the console codepage.

    ``sys.stdin.read`` decodes with the locale encoding, which on a zh-TW
    Windows box is cp950. A UTF-8 payload then survives JSON parsing — the
    structure is ASCII — while every Chinese character in the prompt becomes a
    lone surrogate. The first thing to touch that text is ``turn_id``, whose
    ``encode("utf-8")`` raises UnicodeEncodeError; that is a ValueError, so the
    fail-open handler swallowed it and the hook exited 0 having opened a
    session and recorded no turn. Every Chinese prompt was lost that way.

    ``emit`` already writes bytes for exactly this reason. This is the same fix
    on the way in.
    """
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
    except (UnicodeDecodeError, AttributeError, OSError):
        return {}
    try:
        event = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return event if isinstance(event, dict) else {}


def _note_failure(event_name: str, err: BaseException) -> None:
    """把失敗說出來，但絕不因此中斷對話。

    同一個原因只說一次：hook 每一則訊息都會跑，重複印會把有用的訊息淹掉。
    自己不能失敗——會報告失敗的東西壞掉，比沒有報告更糟。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import xkb_failures

        xkb_failures.note(f"agent hook ({event_name or 'UserPromptSubmit'})", err)
    except Exception:  # noqa: BLE001
        pass


def unattended() -> bool:
    """`claude -p` 排程（例如每日巡檢 agent）不是使用者在講話。

    2026-10-06 實測：互動的 Claude Code 帶 CLAUDE_CODE_SESSION_ATTENDED=1、
    ENTRYPOINT=cli；`claude -p` 是 0 與 sdk-cli。這些輪次不需要知識庫，卻每次
    都走完整套召回、付一次判斷模型，還把提示詞寫進對話紀錄。
    """
    # 只認明確的「無人值守」。用 ENTRYPOINT 推測會把建在 Agent SDK 上的互動
    # 前端（sdk-ts、sdk-py）也當成排程，安靜地丟掉它們所有的召回與紀錄。
    return os.getenv("CLAUDE_CODE_SESSION_ATTENDED", "").strip() == "0"


def main() -> int:
    if unattended():
        return 0
    event = read_event()
    if not event:
        return 0
    name = str(event.get("hook_event_name") or event.get("hookEventName") or "").strip()
    try:
        cfg = config()
        if name == "Stop":
            on_stop(event, cfg)
        else:
            on_prompt(event, cfg)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, KeyError,
            ValueError, TimeoutError) as err:
        # 讀取失敗要放行：不能因為 XKB 連不上就擋住使用者工作。
        # 但寫入失敗不一樣——一輪對話沒被記錄，跟「那一輪沒有內容」長得
        # 一模一樣，而這台機器上每一次呼叫都被回 401 且從來沒有人知道。
        # 行為不變（照樣 exit 0、照樣不擋對話），只是留下痕跡。
        _note_failure(name, err)
        return 0
    return 0


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    raise SystemExit(main())
