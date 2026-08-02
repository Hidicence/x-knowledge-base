#!/usr/bin/env python3
"""Install (or remove) the XKB hook in an agent.

Wires ``xkb_agent_hook.py`` into Claude Code's ``UserPromptSubmit`` and
``Stop`` events so every turn recalls from XKB and is captured back into it,
without the agent having to remember to call anything.

Install is idempotent: re-running replaces the previous entries rather than
stacking duplicates, and ``--uninstall`` removes exactly what was added.
Settings are rewritten atomically (temp file then rename) so an interrupted
run cannot leave the agent with a truncated settings file.

Usage:
    python3 scripts/xkb_install_agent_hook.py --status
    python3 scripts/xkb_install_agent_hook.py --install
    python3 scripts/xkb_install_agent_hook.py --install --url http://127.0.0.1:18972 --token <token>
    python3 scripts/xkb_install_agent_hook.py --uninstall
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
HOOK_SCRIPT = SCRIPTS_DIR / "xkb_agent_hook.py"
HOOK_CONFIG = SCRIPTS_DIR / "xkb-agent-hook-config.json"
EVENTS = ("UserPromptSubmit", "Stop")
TIMEOUT_SECONDS = 15


def agent_home() -> Path:
    return Path(os.getenv("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def command() -> str:
    """Quote the interpreter and script so paths with spaces survive."""
    return f'"{sys.executable}" "{HOOK_SCRIPT}"'


def is_ours(entry: dict) -> bool:
    return isinstance(entry, dict) and HOOK_SCRIPT.name in str(entry.get("command", ""))


def strip_ours(groups: object) -> list:
    """Drop previously installed XKB hooks, preserving everything else."""
    if not isinstance(groups, list):
        return []
    kept = []
    for group in groups:
        if not isinstance(group, dict):
            kept.append(group)
            continue
        hooks = group.get("hooks")
        if not isinstance(hooks, list):
            kept.append(group)
            continue
        remaining = [hook for hook in hooks if not is_ours(hook)]
        if remaining:
            kept.append({**group, "hooks": remaining})
        elif not hooks:
            kept.append(group)
    return kept


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON; refusing to overwrite it ({exc})")
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def install(url: str, token: str, source: str) -> int:
    if not HOOK_SCRIPT.is_file():
        raise SystemExit(f"hook script not found: {HOOK_SCRIPT}")
    settings_path = agent_home() / "settings.json"
    settings = read_json(settings_path)
    hooks = settings.get("hooks")
    hooks = dict(hooks) if isinstance(hooks, dict) else {}
    for event in EVENTS:
        hooks[event] = strip_ours(hooks.get(event)) + [
            {"matcher": "", "hooks": [{"type": "command", "command": command(), "timeout": TIMEOUT_SECONDS}]}
        ]
    settings["hooks"] = hooks
    write_json(settings_path, settings)

    config = {"url": url, "source": source}
    if token:
        config["token"] = token
    write_json(HOOK_CONFIG, config)
    # The config can hold a bearer token, so keep it owner-readable only.
    try:
        HOOK_CONFIG.chmod(0o600)
    except OSError:
        pass

    print(json.dumps({
        "installed": True, "settings": str(settings_path), "events": list(EVENTS),
        "config": str(HOOK_CONFIG), "url": url, "token": bool(token),
    }, ensure_ascii=False))
    return 0


def uninstall() -> int:
    settings_path = agent_home() / "settings.json"
    settings = read_json(settings_path)
    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        for event in EVENTS:
            remaining = strip_ours(hooks.get(event))
            if remaining:
                hooks[event] = remaining
            else:
                hooks.pop(event, None)
        if hooks:
            settings["hooks"] = hooks
        else:
            settings.pop("hooks", None)
        write_json(settings_path, settings)
    HOOK_CONFIG.unlink(missing_ok=True)
    print(json.dumps({"installed": False, "settings": str(settings_path)}, ensure_ascii=False))
    return 0


def status() -> int:
    settings = read_json(agent_home() / "settings.json")
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    installed = {
        event: any(is_ours(hook)
                   for group in (hooks.get(event) or []) if isinstance(group, dict)
                   for hook in (group.get("hooks") or []))
        for event in EVENTS
    }
    print(json.dumps({
        "agent_home": str(agent_home()),
        "hook_script": str(HOOK_SCRIPT),
        "installed": installed,
        "config_present": HOOK_CONFIG.is_file(),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--uninstall", action="store_true")
    mode.add_argument("--status", action="store_true")
    parser.add_argument("--url", default=os.getenv("XKB_MEMORY_SERVICE_URL", "http://127.0.0.1:18972"))
    parser.add_argument("--token", default=os.getenv("XKB_SERVICE_TOKEN", ""))
    parser.add_argument("--source", default="claude-code", help="agent id recorded on every captured turn")
    args = parser.parse_args()
    if args.status:
        return status()
    if args.uninstall:
        return uninstall()
    return install(args.url, args.token, args.source)


if __name__ == "__main__":
    raise SystemExit(main())
