#!/usr/bin/env python3
"""
XKB 首次設定 — 記下資料放在哪

程式放在哪不用問，腳本自己知道（見 xkb_paths.py）。
要問的只有一件事：卡片、書籤、wiki 這些資料放在哪個資料夾。
答案寫進 skill 目錄下的 .xkb.json，之後所有腳本讀同一份。

Usage:
  python3 scripts/xkb_init.py                     # 互動式
  python3 scripts/xkb_init.py --data-dir /path    # 直接指定
  python3 scripts/xkb_init.py --show              # 只看目前設定
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_paths

SUBDIRS = ["cards", "bookmarks", "x-knowledge-base"]


def _describe(data_dir: Path) -> list[str]:
    """回報資料目錄裡有什麼，讓使用者確認自己指到對的地方。"""
    lines = []
    for name in SUBDIRS:
        path = data_dir / name
        if not path.exists():
            lines.append(f"    ❌  {name}/  不存在")
            continue
        if name == "cards":
            count = len(list(path.glob("*.md")))
            lines.append(f"    ✅  {name}/  {count} 張卡片")
        elif name == "x-knowledge-base":
            topics = list((path / "wiki" / "topics").glob("*.md"))
            lines.append(f"    ✅  {name}/  {len(topics)} 個 wiki topic")
        else:
            lines.append(f"    ✅  {name}/")
    return lines


def write_config(data_dir: Path) -> Path:
    config_file = xkb_paths.CONFIG_FILE
    existing = xkb_paths.load_config()
    existing["data_dir"] = str(data_dir)
    existing.setdefault("created", datetime.now(timezone.utc).isoformat())
    existing["updated"] = datetime.now(timezone.utc).isoformat()
    with config_file.open("w", encoding="utf-8") as fh:
        json.dump(existing, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return config_file


def show() -> int:
    print(f"skill dir   : {xkb_paths.SKILL_DIR}")
    print(f"設定檔      : {xkb_paths.CONFIG_FILE}"
          f"{'' if xkb_paths.CONFIG_FILE.exists() else '  (尚未建立)'}")
    print(f"資料目錄    : {xkb_paths.DATA_DIR}  (來源：{xkb_paths.DATA_DIR_SOURCE})")
    print()
    for line in _describe(xkb_paths.DATA_DIR):
        print(line)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="XKB first-run setup")
    parser.add_argument("--data-dir", help="資料目錄（cards / bookmarks / wiki 的上層）")
    parser.add_argument("--show", action="store_true", help="只顯示目前設定，不寫入")
    args = parser.parse_args()

    if args.show:
        return show()

    print("XKB 設定")
    print(f"  skill dir : {xkb_paths.SKILL_DIR}  (自動偵測)")
    print()

    default = xkb_paths.DATA_DIR
    if args.data_dir:
        data_dir = Path(args.data_dir).expanduser()
    elif sys.stdin.isatty():
        answer = input(f"  資料目錄 [{default}]: ").strip()
        data_dir = Path(answer).expanduser() if answer else default
    else:
        data_dir = default
        print(f"  資料目錄 : {data_dir}  (非互動模式，使用預設)")

    data_dir = data_dir.resolve()
    print()
    print(f"  資料目錄 : {data_dir}")
    for line in _describe(data_dir):
        print(line)
    print()

    if not data_dir.exists():
        print(f"  ❌  資料目錄不存在：{data_dir}")
        print("      確認路徑是否正確，或先把資料放進去再跑一次。")
        return 1

    missing = [name for name in SUBDIRS if not (data_dir / name).exists()]
    if missing:
        print(f"  ⚠️   缺少 {', '.join(missing)} — 確認這是不是正確的資料目錄。")

    config_file = write_config(data_dir)
    print(f"  ✅  已寫入 {config_file}")
    print()
    print("  下一步：python3 scripts/health_check_pipeline.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
