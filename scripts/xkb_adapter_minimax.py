from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xkb_adapter_base import XKBInferenceAdapter


class MiniMaxAPIAdapter(XKBInferenceAdapter):
    name = "minimax-api"

    def run(self, request_path: str) -> dict[str, Any]:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        return {
            "version": "xkb.result.v1",
            "ok": False,
            "backend": self.name,
            "model": request.get("meta", {}).get("model") or "MiniMax-M2.7",
            "source_date": request.get("source_date"),
            "chunk_index": request.get("chunk_index"),
            "error": {
                "type": "not_implemented",
                "message": "MiniMax API adapter placeholder.",
            },
        }
