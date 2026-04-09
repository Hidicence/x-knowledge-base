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
import os
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(os.getenv("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
ROUTER_SCRIPT = WORKSPACE / "skills" / "x-knowledge-base" / "scripts" / "recall_router.py"

SERVER_INFO = {
    "name": "xkb-recall",
    "version": "1.0.0",
}

TOOL_DEF = {
    "name": "xkb_recall",
    "description": (
        "Proactive knowledge recall from XKB (X Knowledge Base). "
        "Given the user's current message, automatically checks if it matches a "
        "recall trigger (hard: continuity/definition/status queries; soft: strategy/case study/how-to). "
        "Returns relevant knowledge from MEMORY.md, wiki topics, or knowledge cards. "
        "Returns empty string if the message is casual chat or a one-off task. "
        "Call this BEFORE responding to any substantive question about XKB, OpenClaw, "
        "workflows, AI strategy, decisions, or topics in the knowledge base."
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


def _run_recall(message: str) -> str:
    """Call recall_router.py and return its output."""
    if not ROUTER_SCRIPT.exists():
        return f"[xkb_recall] recall_router.py not found at {ROUTER_SCRIPT}"
    try:
        result = subprocess.run(
            [sys.executable, str(ROUTER_SCRIPT), message],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "OPENCLAW_WORKSPACE": str(WORKSPACE)},
        )
        output = result.stdout.strip()
        return output  # Empty string = suppress (silent)
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        return f"[xkb_recall error: {e}]"


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

        output = _run_recall(message)
        _respond(req_id, {
            "content": [{"type": "text", "text": output}],
            "isError": False,
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
