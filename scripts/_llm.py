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
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
_CONFIG_FILE = _SKILL_DIR / "config" / "llm.json"

# Env override takes priority over config file
_ENV_MODEL = os.getenv("LLM_MODEL", "")


def _load_model() -> str:
    if _ENV_MODEL:
        return _ENV_MODEL
    try:
        cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        return cfg.get("model", "MiniMax-M2.7")
    except Exception:
        return "MiniMax-M2.7"


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
        raise RuntimeError(
            "openclaw CLI not found. Is OpenClaw installed and in PATH?"
        )


def call_raw(prompt: str, *, model: str | None = None, timeout: int = 120) -> str:
    """Single-prompt variant (no system/user split)."""
    return call("", prompt, model=model, timeout=timeout)


if __name__ == "__main__":
    # Quick smoke test: python3 scripts/_llm.py "Hello, reply in 5 words"
    query = " ".join(sys.argv[1:]) or "Reply with: OK"
    print(f"Model: {_load_model()}", file=sys.stderr)
    print(call("You are a helpful assistant.", query))
