"""
XKB Unified LLM Helper
======================
Single entry point for all LLM calls in xkb scripts.

Model is configured in config/llm.json — change "model" there to switch all
scripts at once. OpenClaw handles auth/token-refresh automatically.

Usage:
    from _llm import call

    text = call(system="You are helpful.", user="Summarize this: ...")
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
_CONFIG_FILE = _SKILL_DIR / "config" / "llm.json"

# Env override takes priority over config file
_ENV_MODEL = os.getenv("LLM_MODEL", "")
# Direct API fallback (used when openclaw CLI is not available)
_DIRECT_API_URL = os.getenv("LLM_API_URL", "")
_DIRECT_API_KEY = os.getenv("LLM_API_KEY", "")

# Fallback: read LLM config from ~/.openclaw/openclaw.json
# Priority: env vars > openclaw.json (MiniMax) > Gemini
if not _DIRECT_API_URL or not _DIRECT_API_KEY:
    try:
        _oclaw_cfg = Path.home() / ".openclaw" / "openclaw.json"
        if _oclaw_cfg.exists():
            import json as _j
            _oclaw_env = _j.loads(_oclaw_cfg.read_text(encoding="utf-8")).get("env", {})
            _DIRECT_API_URL = _DIRECT_API_URL or _oclaw_env.get("LLM_API_URL", "")
            _DIRECT_API_KEY = _DIRECT_API_KEY or _oclaw_env.get("LLM_API_KEY", "")
            _ENV_MODEL = _ENV_MODEL or _oclaw_env.get("LLM_MODEL", "")
            # Gemini last-resort fallback
            if not _DIRECT_API_URL or not _DIRECT_API_KEY:
                _gk = _oclaw_env.get("GEMINI_API_KEY", "")
                if _gk:
                    _DIRECT_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
                    _DIRECT_API_KEY = _gk
                    _ENV_MODEL = _ENV_MODEL or "gemini-2.5-pro"
    except Exception:
        pass


def _load_model() -> str:
    if _ENV_MODEL:
        return _ENV_MODEL
    try:
        cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        return cfg.get("model", "MiniMax-M2.7")
    except Exception:
        return "MiniMax-M2.7"


def _direct_api_call(system: str, user: str, *, timeout: int = 120) -> str:
    """
    Fallback when openclaw CLI is not available.
    Uses LLM_API_URL + LLM_API_KEY env vars for direct HTTP calls.
    Supports both OpenAI chat-completions format and Anthropic messages format.
    """
    if not _DIRECT_API_URL or not _DIRECT_API_KEY:
        raise RuntimeError(
            "openclaw is not installed and LLM_API_URL / LLM_API_KEY are not set.\n"
            "Set them to use a direct API fallback:\n"
            "  export LLM_API_URL=https://api.minimax.io/anthropic\n"
            "  export LLM_API_KEY=your-key\n"
            "  export LLM_MODEL=MiniMax-M2.5"
        )

    model = _load_model()
    url = _DIRECT_API_URL.rstrip("/")
    # Use Anthropic format only when URL explicitly contains "/anthropic"
    is_anthropic = "/anthropic" in url
    is_gemini = "generativelanguage.googleapis.com" in url

    if is_gemini:
        # Gemini native generateContent format
        if ":generateContent" not in url:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        # model var not used for URL when generateContent already in URL
        url = f"{url}?key={_DIRECT_API_KEY}"
        parts_text = (f"{system.strip()}\n\n" if system else "") + user.strip()
        payload = {"contents": [{"parts": [{"text": parts_text}]}]}
        headers = {"Content-Type": "application/json"}
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        import time as _time
        _last_err = None
        for _attempt in range(5):
            if _attempt:
                _time.sleep(8 * _attempt)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except urllib.error.HTTPError as _he:
                _body = _he.read().decode()[:200]
                _last_err = RuntimeError(f"Direct API call failed: {_he!r} body={_body}")
                if _he.code not in (429, 503):
                    raise _last_err
            except Exception as _e:
                raise RuntimeError(f"Direct API call failed: {_e!r}")
        raise _last_err  # type: ignore
    elif is_anthropic:
        # Anthropic messages format
        if not url.endswith("/messages"):
            url = url + "/messages"
        messages = [{"role": "user", "content": user}]
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        headers = {
            "Content-Type": "application/json",
            "x-api-key": _DIRECT_API_KEY,
            "anthropic-version": "2023-06-01",
            "Authorization": f"Bearer {_DIRECT_API_KEY}",
        }
    else:
        # OpenAI chat completions format
        if not url.endswith("/chat/completions"):
            url = url + "/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_DIRECT_API_KEY}",
        }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Direct API call failed: {e!r}")

    if is_anthropic:
        # Handle thinking blocks: find first text block
        content = data.get("content", [])
        if isinstance(content, list):
            text_block = next((b for b in content if b.get("type") == "text"), None)
            if text_block:
                return text_block["text"].strip()
        raise RuntimeError(f"Unexpected Anthropic response format: {data}")
    else:
        # OpenAI format
        choices = data.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "") or ""
            # Strip <think>...</think> blocks (MiniMax M2.7 reasoning prefix)
            import re as _re
            text = _re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
            return text
        raise RuntimeError(f"Unexpected OpenAI response format: {data}")


def call(system: str, user: str, *, model: str | None = None, timeout: int = 120) -> str:
    """
    Call the configured LLM via `openclaw capability model run`.

    OpenClaw handles provider auth (OAuth, API keys) automatically.
    Configure the default model in config/llm.json.

    Args:
        system:  System prompt text.
        user:    User message / task prompt.
        model:   Override model for this call only.
        timeout: Seconds to wait for a response (default 120).

    Returns:
        LLM response text, stripped.

    Raises:
        RuntimeError: If the call fails or returns no output.
    """
    m = model or _load_model()

    # openclaw capability model run takes a single --prompt.
    # We inject the system context as a prefix.
    if system:
        combined = f"[SYSTEM]\n{system.strip()}\n\n[USER]\n{user.strip()}"
    else:
        combined = user.strip()

    cmd = ["openclaw", "capability", "model", "run", "--prompt", combined, "--json"]
    if m:
        cmd += ["--model", m]

    try:
        # The process may hang after outputting JSON (streaming artefact).
        # We read stdout until we have a complete JSON object, then kill.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Collect output until JSON is complete or timeout fires
        import signal, threading

        chunks: list[str] = []
        done = threading.Event()

        def _reader():
            depth = 0
            in_json = False
            for ch in iter(lambda: proc.stdout.read(1), ""):
                chunks.append(ch)
                if ch == "{":
                    depth += 1
                    in_json = True
                elif ch == "}":
                    depth -= 1
                if in_json and depth == 0:
                    break
            done.set()

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=timeout)

        proc.kill()
        proc.wait()

        raw_out = "".join(chunks).strip()

        if not raw_out:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"openclaw model run produced no output. stderr: {stderr[:300]}")

        data = json.loads(raw_out)
        if not data.get("ok"):
            raise RuntimeError(f"openclaw model run failed: {data}")

        outputs = data.get("outputs", [])
        if not outputs:
            raise RuntimeError(f"openclaw model run returned empty outputs: {data}")

        return outputs[0].get("text", "").strip()

    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse openclaw JSON output: {e!r}\nraw={raw_out[:300]}")
    except FileNotFoundError:
        # openclaw not installed — fall back to direct API call
        return _direct_api_call(system, user, timeout=timeout)


def call_raw(prompt: str, *, model: str | None = None, timeout: int = 120) -> str:
    """Single-prompt variant (no system/user split)."""
    return call("", prompt, model=model, timeout=timeout)


if __name__ == "__main__":
    # Quick smoke test: python3 scripts/_llm.py "Hello, reply in 5 words"
    query = " ".join(sys.argv[1:]) or "Reply with: OK"
    print(f"Model: {_load_model()}", file=sys.stderr)
    print(call("You are a helpful assistant.", query))
