"""
XKB Unified LLM Helper
======================
Single entry point for all LLM calls in xkb scripts.

Model is configured in config/llm.json — change "model" there to switch all
scripts at once.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
_CONFIG_FILE = _SKILL_DIR / "config" / "llm.json"
_OPENCLAW_CFG = Path.home() / ".openclaw" / "openclaw.json"

_ENV_MODEL = os.getenv("LLM_MODEL", "")
_ENV_API_URL = os.getenv("LLM_API_URL", "")
_ENV_API_KEY = os.getenv("LLM_API_KEY", "")
_ENV_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

_OPENCLAW_ENV: dict = {}
if _OPENCLAW_CFG.exists():
    try:
        _OPENCLAW_ENV = json.loads(_OPENCLAW_CFG.read_text(encoding="utf-8")).get("env", {})
    except Exception:
        _OPENCLAW_ENV = {}


def _load_model() -> str:
    if _ENV_MODEL and _ENV_MODEL != "MiniMax-M2.7":
        return _ENV_MODEL
    try:
        cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        return cfg.get("model", "MiniMax-M2.7")
    except Exception:
        return "MiniMax-M2.7"



def _provider_for_model(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("openai-codex/") or m.startswith("openai/"):
        return "openai"
    if m.startswith("gemini") or m.startswith("google/"):
        return "gemini"
    if m.startswith("minimax"):
        return "minimax"
    return "minimax"



def _provider_config(model: str) -> tuple[str, str, str]:
    provider = _provider_for_model(model)
    if provider == "gemini":
        gemini_key = os.getenv("GEMINI_API_KEY", "") or _OPENCLAW_ENV.get("GEMINI_API_KEY", "") or _ENV_GEMINI_KEY
        if not gemini_key:
            raise RuntimeError("GEMINI_API_KEY not configured")
        return provider, "https://generativelanguage.googleapis.com/v1beta/models", gemini_key

    if provider == "openai":
        api_url = os.getenv("OPENAI_API_URL", "") or _OPENCLAW_ENV.get("OPENAI_API_URL", "") or os.getenv("OPENAI_BASE_URL", "") or "https://api.openai.com/v1"
        api_key = os.getenv("OPENAI_API_KEY", "") or _OPENCLAW_ENV.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured for openai/openai-codex models")
        return provider, api_url.rstrip("/"), api_key

    api_url = _ENV_API_URL or _OPENCLAW_ENV.get("LLM_API_URL", "")
    api_key = _ENV_API_KEY or _OPENCLAW_ENV.get("LLM_API_KEY", "")
    if not api_url or not api_key:
        raise RuntimeError("LLM_API_URL / LLM_API_KEY not configured for MiniMax models")
    return provider, api_url.rstrip("/"), api_key



def _direct_api_call(system: str, user: str, *, model: str, timeout: int = 120) -> str:
    provider, api_url, api_key = _provider_config(model)

    if provider == "gemini":
        url = f"{api_url}/{model}:generateContent?key={api_key}"
        parts_text = (f"{system.strip()}\n\n" if system else "") + user.strip()
        payload = {"contents": [{"parts": [{"text": parts_text}]}]}
        headers = {"Content-Type": "application/json"}
    elif provider == "openai":
        url = api_url + "/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload = {"model": model.split("/", 1)[1] if "/" in model else model, "messages": messages, "max_tokens": 4096}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    else:
        url = api_url
        if not url.endswith("/messages"):
            url = url + "/messages"
        payload = {"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": user}]}
        if system:
            payload["system"] = system
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Authorization": f"Bearer {api_key}",
        }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Direct API call failed: {e!r}")

    if provider == "gemini":
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    if provider == "openai":
        return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    content = data.get("content", [])
    if isinstance(content, list):
        text_block = next((b for b in content if b.get("type") == "text"), None)
        if text_block:
            return text_block["text"].strip()
    raise RuntimeError(f"Unexpected response format: {data}")



def call(system: str, user: str, *, model: str | None = None, timeout: int = 600) -> str:
    m = model or _load_model()

    # MiniMax models: skip openclaw entirely, go direct to API
    if _provider_for_model(m) == "minimax":
        return _direct_api_call(system, user, model=m, timeout=timeout)

    if system:
        combined = f"[SYSTEM]\n{system.strip()}\n\n[USER]\n{user.strip()}"
    else:
        combined = user.strip()

    cmd = ["openclaw", "infer", "model", "run", "--prompt", combined, "--json"]
    if m:
        cmd += ["--model", m]

    proc = None
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        while True:
            if proc.poll() is not None:
                break
            if time.time() - start > timeout:
                proc.kill()
                raise RuntimeError(f"openclaw model run timed out after {timeout}s")
            time.sleep(0.2)

        stdout, stderr = proc.communicate(timeout=5)
        raw_out = (stdout or "").strip()
        stderr = (stderr or "").strip()

        if proc.returncode != 0:
            raise RuntimeError(f"openclaw model run exited {proc.returncode}. stderr: {stderr[:500]} stdout: {raw_out[:500]}")
        if not raw_out:
            raise RuntimeError(f"openclaw model run produced no output. stderr: {stderr[:500]}")

        data = json.loads(raw_out)
        if not data.get("ok"):
            raise RuntimeError(f"openclaw model run failed: {data}")
        outputs = data.get("outputs", [])
        if not outputs:
            raise RuntimeError(f"openclaw model run returned empty outputs: {data}")
        return outputs[0].get("text", "").strip()

    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse openclaw JSON output: {e!r}")
    except RuntimeError:
        raise
    except FileNotFoundError:
        return _direct_api_call(system, user, model=m, timeout=timeout)
    finally:
        if proc and proc.poll() is None:
            proc.kill()



def call_raw(prompt: str, *, model: str | None = None, timeout: int = 600) -> str:
    return call("", prompt, model=model, timeout=timeout)


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "Reply with: OK"
    print(f"Model: {_load_model()}", file=sys.stderr)
    print(call("You are a helpful assistant.", query))
