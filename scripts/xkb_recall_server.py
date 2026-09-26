#!/usr/bin/env python3
"""
XKB Recall MCP Server

MCP stdio server that exposes `xkb_recall` as a tool.
Works with any MCP-compatible agent: OpenClaw (via acpx), Claude Code, etc.

Protocol: JSON-RPC 2.0 over stdio (newline-delimited)

Tool: xkb_recall
  Input: { "message": "<user's current message>" }
  Output: Knowledge Service packet plus compatible results/formatted_text aliases
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from xkb_recall import run_configured, compatibility_aliases

RECALL_SCRIPT = Path(__file__).resolve().parent / "xkb_recall.py"

SERVER_INFO = {
    "name": "xkb-recall",
    "version": "1.0.0",
}

TOOL_DEF = {
    "name": "xkb_recall",
    "description": (
        "Recall relevant evidence from the shared XKB knowledge service before "
        "answering substantive questions. Returns cards, wiki topics and conversation "
        "evidence with provenance. Uses the same retrieval, ACL, relevance judge and "
        "ranking as the HTTP API. Inspect retrieval_mode, judge and warnings: "
        "keyword fallback or an unavailable judge are not semantic success. "
        "Results are candidates, not established answers; check their relevance "
        "and sources. Greetings are skipped. Failures are reported explicitly."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The user's current message to check for recall triggers.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
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


def _run_recall_structured(message: str, limit: int = 10) -> dict:
    """Expose the common runtime boundary through MCP's explicit error contract."""
    try:
        return compatibility_aliases(run_configured(message, limit, script=RECALL_SCRIPT))
    except Exception as exc:
        return _failure(str(exc))


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
        structured = _run_recall_structured(message, arguments.get("limit", 10))
        # formatted_text for human-readable context injection
        text_output = structured.get("formatted_text", "")
        # 提示放在回傳內容裡，不只放在 tool description——description 可能被截斷或忽略，
        # 而這句話決定了 agent 會不會把不相關的卡片當成使用者的知識講出來。
        if text_output:
            text_output = (
                "（以下是候選，不是答案。請依來源與查詢內容檢查相關性——"
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


import xkb_usage  # noqa: E402  — 量測誰在跑，見 scripts/xkb_usage.py

if __name__ == "__main__":
    xkb_usage.record(__file__)
    main()
