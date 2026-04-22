from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xkb_adapter_base import XKBInferenceAdapter


class ClaudeCodeAdapter(XKBInferenceAdapter):
    name = "claude-code"

    def run(self, request_path: str) -> dict[str, Any]:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        return {
            "version": "xkb.result.v1",
            "ok": False,
            "backend": self.name,
            "model": request.get("meta", {}).get("model") or "",
            "source_date": request.get("source_date"),
            "chunk_index": request.get("chunk_index"),
            "error": {
                "type": "not_implemented",
                "message": "Claude Code adapter placeholder. Intended for Claude Code / Claude CLI style runtimes.",
            },
        }
