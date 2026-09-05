#!/usr/bin/env python3
"""
XKB Recall MCP Server

MCP stdio server that exposes `xkb_recall` as a tool.
Works with any MCP-compatible agent: OpenClaw (via acpx), Claude Code, etc.

Protocol: JSON-RPC 2.0 over stdio (newline-delimited)

Tool: xkb_recall
  Input: { "message": "<user's current message>" }
  Output: recall result (inline injection or side hint) — empty string if suppress
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import xkb_paths

from runtime_config import runtime_env

WORKSPACE = xkb_paths.WORKSPACE

# Router resolution, most reliable first:
# - Sibling:      the router always ships next to this file, whatever OPENCLAW_WORKSPACE
#                 points at. Required when code and data live in different trees, which
#                 is what the README's MCP setup actually produces.
# - Local/direct: OPENCLAW_WORKSPACE = repo root  → WORKSPACE/scripts/recall_router.py
# - OpenClaw:     OPENCLAW_WORKSPACE = ~/.openclaw/workspace → WORKSPACE/skills/x-knowledge-base/scripts/recall_router.py
_candidates = [
    Path(__file__).resolve().parent / "recall_router.py",
    WORKSPACE / "scripts" / "recall_router.py",
    WORKSPACE / "skills" / "x-knowledge-base" / "scripts" / "recall_router.py",
]
ROUTER_SCRIPT = next((p for p in _candidates if p.exists()), _candidates[-1])

SERVER_INFO = {
    "name": "xkb-recall",
    "version": "1.0.0",
}

TOOL_DEF = {
    "name": "xkb_recall",
    "description": (
        "ALWAYS call this tool before responding to any substantive user message. "
        "It proactively checks if the current conversation topic matches knowledge stored in the user's "
        "personal knowledge base (XKB). Topics include but are not limited to: projects, strategies, "
        "decisions, how-to questions, case studies, roadmaps, people, tools, workflows, AI, SEO, startups, "
        "products, or any domain the user works in. "
        "Returns excerpts from MEMORY.md, wiki topics, or knowledge cards. "
        "Returns empty string for purely casual chat (greetings, weather, jokes). "
        "The router decides internally whether recall is needed — you just call it. "
        "\n\n"
        "IMPORTANT — the results are CANDIDATES, not an answer. They come from keyword and "
        "vector matching, which is cheap and runs on every message but cannot judge whether a "
        "result is actually about what the user asked. That judgement is yours: read each "
        "candidate and silently drop the ones that are not genuinely related. "
        "Surfacing an unrelated card as if it were the user's own knowledge is worse than "
        "returning nothing — it makes the knowledge base look wrong. "
        "`relevance` (0-1) is how strong the match is within its own retrieval leg; `unified_score` is only the fused rank position and compresses into a narrow band, so use it for order, not for confidence. Neither is a verdict — read each candidate yourself."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The user's current message to check for recall triggers.",
            }
        },
        "required": ["message"],
    },
}


# A router failure and an honest "nothing matched" used to be indistinguishable:
# both surfaced as results=[]. That turned a missing-import bug into a 12-week
# silent outage (2026-05-04 → 2026-07-26). Every return path now carries an
# explicit `status`, and failures say so loudly enough for the agent to repeat
# it back to the user.
def _empty(status: str = "ok", error: str = "") -> dict:
    return {"trigger_class": "suppress", "state": "suppress", "delivery_mode": "none",
            "results": [], "confidence": 0.0, "formatted_text": "", "query": "",
            "status": status, "error": error}


def _failure(reason: str) -> dict:
    """A recall that could not run at all — never mistakable for 'no results'."""
    return {**_empty("failed", reason),
            "formatted_text": (
                "【XKB 知識庫查詢失敗】\n"
                f"原因:{reason}\n"
                "注意:這不代表知識庫沒有相關內容,而是查詢本身沒有執行成功。"
                "請明確告訴使用者這次回答並未使用知識庫。"
            )}


def _run_recall_structured(message: str) -> dict:
    """Call recall_router.py --json and return structured result."""
    if not ROUTER_SCRIPT.exists():
        return _failure(f"router not found at {ROUTER_SCRIPT}")
    try:
        # Resolve the same portable contract at the MCP boundary, rather than
        # relying on the router (or a future worker) to rediscover it.  This
        # keeps process env > XKB_ENV_FILE precedence and ensures the
        # canonical GEMINI_API_KEY reaches the child without putting a secret
        # in the MCP command/configuration.
        child_env = runtime_env()
        child_env.update({
            "OPENCLAW_WORKSPACE": str(WORKSPACE),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        })
        result = subprocess.run(
            [sys.executable, str(ROUTER_SCRIPT), message, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        return _failure("router timed out after 30s")
    except Exception as e:
        return _failure(f"router could not be launched: {e}")

    if result.returncode != 0:
        stderr_tail = " / ".join((result.stderr or "").strip().splitlines()[-3:])
        return _failure(f"router exited {result.returncode}: {stderr_tail or 'no stderr'}")

    try:
        structured = json.loads(result.stdout)
    except Exception as e:
        stderr_tail = " / ".join((result.stderr or "").strip().splitlines()[-3:])
        return _failure(f"router returned unparseable output ({e}): {stderr_tail or 'no stderr'}")

    structured.setdefault("status", "ok")
    structured.setdefault("error", "")
    return structured


def _respond(req_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    print(json.dumps(resp, ensure_ascii=False), flush=True)


def _notify(method: str, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    print(json.dumps(msg, ensure_ascii=False), flush=True)


def handle(req: dict):
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    # ── initialize ──────────────────────────────────────────────────────────
    if method == "initialize":
        _respond(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
        return

    # ── initialized (notification, no response needed) ──────────────────────
    if method == "notifications/initialized":
        return

    # ── tools/list ──────────────────────────────────────────────────────────
    if method == "tools/list":
        _respond(req_id, {"tools": [TOOL_DEF]})
        return

    # ── tools/call ──────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if tool_name != "xkb_recall":
            _respond(req_id, error={"code": -32602, "message": f"Unknown tool: {tool_name}"})
            return

        message = arguments.get("message", "")
        if not message:
            _respond(req_id, {"content": [{"type": "text", "text": ""}], "isError": False})
            return

        structured = _run_recall_structured(message)
        # formatted_text for human-readable context injection
        text_output = structured.get("formatted_text", "")
        # 提示放在回傳內容裡，不只放在 tool description——description 可能被截斷或忽略，
        # 而這句話決定了 agent 會不會把不相關的卡片當成使用者的知識講出來。
        if text_output:
            text_output = (
                "（以下是候選，不是答案。撈取用的是關鍵字與向量比對，判斷不了語意相關性——"
                "請自行略過與問題無關的項目，不要當成使用者的知識引用。）\n\n"
                + text_output
            )
        # Full structured data as JSON annotation (agent can use it for routing decisions)
        _respond(req_id, {
            "content": [
                {"type": "text", "text": text_output},
                {"type": "text", "text": f"[xkb_recall_meta] {json.dumps(structured, ensure_ascii=False)}"},
            ],
            "isError": structured.get("status") == "failed",
        })
        return

    # ── ping ────────────────────────────────────────────────────────────────
    if method == "ping":
        _respond(req_id, {})
        return

    # ── unknown method ───────────────────────────────────────────────────────
    if req_id is not None:
        _respond(req_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            handle(req)
        except Exception as e:
            req_id = req.get("id") if isinstance(req, dict) else None
            if req_id is not None:
                _respond(req_id, error={"code": -32603, "message": str(e)})


if __name__ == "__main__":
    main()
