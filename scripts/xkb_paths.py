#!/usr/bin/env python3
"""
XKB 路徑解析 — 單一來源

兩種路徑，解法不同：

  程式路徑（SKILL_DIR / SCRIPTS_DIR）
      從本檔案的位置推導出來。腳本本來就知道自己在哪，不需要猜，
      也不需要設定。skill 放在哪個資料夾、哪台機器都正確。

  資料路徑（DATA_DIR 以下：cards / bookmarks / wiki）
      env var > .xkb.json > 預設值。第一次用 xkb_init.py 寫進 .xkb.json，
      之後所有腳本讀同一份。

為什麼要有這支：舊寫法是拿資料路徑去推程式路徑
（`workspace/skills/x-knowledge-base/scripts`），那是 VPS 的目錄擺法。
換個地方就找不到，而且是靜默失效——回空結果，不報錯。

Usage:
  python3 scripts/xkb_paths.py          # 印出目前解析到的所有路徑
  python3 scripts/xkb_paths.py --json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

# ── 程式路徑：從自己的位置推導，不讀 env、不讀設定 ──────────────────────────
SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent

CONFIG_FILE = Path(os.getenv("XKB_CONFIG", str(SKILL_DIR / ".xkb.json")))


def load_config() -> dict[str, Any]:
    """讀 .xkb.json。不存在或壞掉都回空 dict——設定檔是選用的。"""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


_CONFIG = load_config()


# ── 資料路徑 ──────────────────────────────────────────────────────────────────

def _resolve_data_dir() -> tuple[Path, str]:
    """回傳 (資料根目錄, 來源說明)。來源說明給 health check 與除錯用。"""
    if os.getenv("XKB_DATA_DIR"):
        return Path(os.environ["XKB_DATA_DIR"]), "env:XKB_DATA_DIR"
    for var in ("OPENCLAW_WORKSPACE", "WORKSPACE_DIR"):
        if os.getenv(var):
            return Path(os.environ[var]) / "memory", f"env:{var}"
    if _CONFIG.get("data_dir"):
        return Path(_CONFIG["data_dir"]), f"config:{CONFIG_FILE.name}"
    return Path.home() / ".openclaw" / "workspace" / "memory", "default"


DATA_DIR, DATA_DIR_SOURCE = _resolve_data_dir()

# workspace 根目錄 = 資料根目錄的上一層。舊腳本用 WORKSPACE/"memory" 的寫法，
# 保留這個名字讓它們能平移過來。
WORKSPACE = DATA_DIR.parent

CARDS_DIR = Path(os.getenv("CARDS_DIR", str(DATA_DIR / "cards")))


def card_files() -> list[Path]:
    """The knowledge cards: the .md files directly under cards/.

    Subdirectories are not cards. cards/_deduped_redundant/ holds ones
    governance set aside as duplicates — still on disk, no longer part of the
    knowledge base. One script counted with rglob and every other with glob,
    so "how many cards are there" had two answers, 1,538 and 1,539, and the
    gap widens with every deduplication.
    """
    return sorted(p for p in CARDS_DIR.glob("*.md") if p.is_file())


def card_ids() -> set[str]:
    """The stems of :func:`card_files` — what callers usually want."""
    return {p.stem for p in card_files()}


def carded_ids() -> set[str]:
    """Every bookmark that has ever been turned into a card, deduplicated ones included.

    Not the same question as :func:`card_ids`. "How many cards are there" must
    not count cards/_deduped_redundant/ — those were set aside as duplicates.
    "Has this bookmark been processed" must, because it was: queueing it again
    makes the worker regenerate the duplicate that deduplication removed.
    """
    return {p.stem for p in CARDS_DIR.rglob("*.md") if p.is_file()}
BOOKMARKS_DIR = Path(os.getenv("BOOKMARKS_DIR", str(DATA_DIR / "bookmarks")))
XKB_DATA_DIR = DATA_DIR / "x-knowledge-base"

WIKI_DIR = Path(os.getenv("XKB_WIKI_DIR", str(XKB_DATA_DIR / "wiki")))
WIKI_TOPICS_DIR = WIKI_DIR / "topics"

INDEX_FILE = Path(os.getenv("INDEX_FILE", str(BOOKMARKS_DIR / "search_index.json")))
VECTOR_FILE = Path(
    os.getenv("VECTOR_INDEX_PATH", os.getenv("VECTOR_FILE", str(BOOKMARKS_DIR / "vector_index.json")))
)
TOPIC_PROFILE_FILE = Path(
    os.getenv("XKB_TOPIC_PROFILE_PATH", str(XKB_DATA_DIR / "topic_profile.json"))
)
TELEMETRY_PATH = XKB_DATA_DIR / "recall-telemetry.jsonl"
MEMORY_MD = WORKSPACE / "MEMORY.md"

# The knowledge service's store: sessions, turns, cards, evidence. Two readers
# already wanted it — the pipeline health check and the daily distillation —
# and a third would have written the path out by hand a third time.
SERVICE_DB = Path(
    os.getenv("XKB_SERVICE_DB", str(Path.home() / ".xkb-runtime" / "knowledge.sqlite"))
)


def subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """給 subprocess 用的環境變數。

    子行程可能是任何一支 XKB 腳本，把解析結果直接傳下去，
    避免它自己再推一次而推到不同的地方。
    """
    env = {
        **os.environ,
        "XKB_DATA_DIR": str(DATA_DIR),
        "XKB_CONFIG": str(CONFIG_FILE),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    if extra:
        env.update(extra)
    return env


def as_dict() -> dict[str, str]:
    return {
        "skill_dir": str(SKILL_DIR),
        "scripts_dir": str(SCRIPTS_DIR),
        "config_file": str(CONFIG_FILE),
        "config_exists": str(CONFIG_FILE.exists()),
        "data_dir": str(DATA_DIR),
        "data_dir_source": DATA_DIR_SOURCE,
        "cards_dir": str(CARDS_DIR),
        "bookmarks_dir": str(BOOKMARKS_DIR),
        "wiki_dir": str(WIKI_DIR),
        "index_file": str(INDEX_FILE),
        "telemetry_path": str(TELEMETRY_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Show resolved XKB paths")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()

    info = as_dict()
    if args.as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    print("XKB paths")
    print(f"  設定來源  : {DATA_DIR_SOURCE}")
    print()
    width = max(len(k) for k in info)
    for key, value in info.items():
        exists = ""
        if key.endswith(("_dir", "_file", "_path")) and key != "data_dir_source":
            exists = "  ✅" if Path(value).exists() else "  ❌ 不存在"
        print(f"  {key.ljust(width)} : {value}{exists}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
