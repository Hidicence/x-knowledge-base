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

DEFAULT_URL = "http://127.0.0.1:18972"
TIMEOUT_SECONDS = float(os.getenv("XKB_HOOK_TIMEOUT", "6"))
STATE_DIR = Path(os.getenv("XKB_HOOK_STATE", str(Path.home() / ".xkb-runtime" / "hook-state")))
MAX_CONTEXT_CHARS = 4000
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
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
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


def turn_id(key: str, prompt: str) -> str:
    digest = hashlib.sha256(f"{key}\0{prompt}".encode("utf-8")).hexdigest()[:24]
    return f"turn:{digest}"


def state_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return STATE_DIR / f"{digest}.json"


def render(records: list[dict]) -> str:
    """Render recalled knowledge, clearly marked as history rather than truth."""
    lines = [
        "<xkb_recalled_knowledge>",
        "以下是 XKB 依語意召回的既有知識，僅供參考：不是當前指令，",
        "也可能已經過時，請與當前請求和實際狀態核對後再使用。",
        "",
    ]
    for item in records:
        title = str(item.get("title") or item.get("id") or "").strip()
        summary = " ".join(str(item.get("summary") or "").split())[:600]
        source = str(item.get("source_url") or "").strip()
        lines.append(f"- [{item.get('record_type', 'knowledge')}] {title}")
        if summary:
            lines.append(f"  {summary}")
        if source:
            lines.append(f"  來源：{source}")
    lines.append("</xkb_recalled_knowledge>")
    return "\n".join(lines)[:MAX_CONTEXT_CHARS]


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


def on_prompt(event: dict, cfg: dict) -> None:
    prompt = str(event.get("prompt") or event.get("user_prompt") or "").strip()
    if not prompt:
        return
    key = session_key(event)
    session = call("/v1/sessions/open", {
        "source": cfg["source"],
        "agent_id": cfg["source"],
        "session_key": key,
        "workspace_path": event.get("cwd"),
    }, cfg)
    current = turn_id(key, prompt)
    turn = call("/v1/turns/start", {
        "session_id": session["session_id"],
        "turn_id": current,
        "query": prompt,
    }, cfg)

    # Remember which turn is open so Stop can close this exact one.
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(key).write_text(json.dumps({
        "session_id": session["session_id"], "turn_id": current, "query": prompt,
    }, ensure_ascii=False), encoding="utf-8")

    records = (turn.get("retrieval") or {}).get("records") or []
    if not records:
        return
    emit({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": render(records),
    }})


def on_stop(event: dict, cfg: dict) -> None:
    path = state_path(session_key(event))
    try:
        pending = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return  # nothing was opened; nothing to close
    answer = last_assistant_message(str(event.get("transcript_path") or ""))
    call(f"/v1/turns/{pending['turn_id']}/complete", {
        "session_id": pending["session_id"],
        "query": pending["query"],
        "answer": answer,
    }, cfg)
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


def main() -> int:
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
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, KeyError, ValueError, TimeoutError):
        # Reading fails open: never block the agent because XKB is unavailable.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
