"""Portable runtime configuration loading for XKB services."""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path) -> dict[str, str]:
    """Read a dotenv-style file without mutating the process environment."""
    env_path = Path(path)
    if not env_path.exists():
        raise FileNotFoundError(f"XKB env file not found: {env_path}")
    values: dict[str, str] = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OSError(f"Unable to read XKB env file {env_path}: {exc}") from exc
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise ValueError(f"Invalid XKB env file {env_path} line {line_no}: expected KEY=VALUE")
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"Invalid XKB env file {env_path} line {line_no}: invalid key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def runtime_env(env_file: str | Path | None = None) -> dict[str, str]:
    """Return explicit env-file values; process environment remains highest precedence."""
    selected = env_file or os.getenv("XKB_ENV_FILE")
    file_values = load_env_file(selected) if selected else {}
    return {**file_values, **os.environ}
