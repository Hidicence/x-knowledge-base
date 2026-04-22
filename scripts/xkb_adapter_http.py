from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from xkb_adapter_base import XKBInferenceAdapter


class GenericHTTPAdapter(XKBInferenceAdapter):
    """Generic HTTP API adapter for OpenAI-compatible endpoints.

    Reads LLM_API_URL / LLM_API_KEY from environment or ~/.openclaw/openclaw.json.
    Works with any OpenAI-compatible endpoint (MiniMax, Groq, local models, etc.).
    """

    name = "http-api"

    def run(self, request_path: str) -> dict[str, Any]:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        model = request.get("meta", {}).get("model") or "MiniMax-M2.7"
        system = request.get("input", {}).get("system", "")
        user = request.get("input", {}).get("user", "")

        cfg = {}
        cfg_path = Path.home() / ".openclaw" / "openclaw.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8")).get("env", {})
            except Exception:
                cfg = {}

        api_url = os.getenv("LLM_API_URL", "") or cfg.get("LLM_API_URL", "")
        api_key = os.getenv("LLM_API_KEY", "") or cfg.get("LLM_API_KEY", "")
        if not api_url or not api_key:
            return {
                "version": "xkb.result.v1",
                "ok": False,
                "backend": self.name,
                "model": model,
                "task": request.get("task", ""),
                "source_date": request.get("source_date"),
                "chunk_index": request.get("chunk_index"),
                "error": {
                    "type": "config_error",
                    "message": "LLM_API_URL / LLM_API_KEY not configured for GenericHTTPAdapter.",
                },
            }

        url = api_url.rstrip("/")
        if not url.endswith("/messages"):
            url = url + "/messages"

        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            payload["system"] = system

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Authorization": f"Bearer {api_key}",
        }

        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            content = data.get("content", [])
            text = ""
            if isinstance(content, list):
                text_block = next((b for b in content if b.get("type") == "text"), None)
                if text_block:
                    text = text_block.get("text", "").strip()

            output: dict[str, Any] = {
                "version": "xkb.result.v1",
                "ok": True,
                "backend": self.name,
                "model": model,
                "task": request.get("task", ""),
                "source_date": request.get("source_date"),
                "chunk_index": request.get("chunk_index"),
                "raw_text": text,
            }

            if request.get("task") == "bookmark_enrich_card":
                output["output"] = {
                    "card_markdown": text,
                    "card_id": request.get("card_id"),
                    "bookmark_file": request.get("bookmark_file"),
                }
            else:
                cleaned = text.strip().removeprefix("```json").removeprefix("```").rstrip("` \n")
                try:
                    parsed = json.loads(cleaned)
                    output["output"] = {"insights": parsed.get("insights", [])}
                except Exception as e:
                    output["output"] = {"insights": [], "_parse_error": str(e), "_raw_text": text[:300]}
            return output
        except Exception as e:
            return {
                "version": "xkb.result.v1",
                "ok": False,
                "backend": self.name,
                "model": model,
                "task": request.get("task", ""),
                "source_date": request.get("source_date"),
                "chunk_index": request.get("chunk_index"),
                "error": {
                    "type": type(e).__name__,
                    "message": str(e)[:500],
                },
                "output": {"insights": []},
            }