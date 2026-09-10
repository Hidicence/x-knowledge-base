#!/usr/bin/env python3
"""重建搜尋索引——唯一寫 search_index.json 的地方。

索引是衍生物。它的每一個欄位都是從卡片與書籤檔案解析出來的，2026-09-10 實測：
把它整份從檔案重建，1680 筆一模一樣，而且比正在用的那份還準——重建找回了三張
現行索引漏掉的卡，也解掉了一組同一份內容被記成兩個路徑的分歧。

會漂走，是因為原本有八支程式各自對它做讀-改-寫：

    build_search_index.sh   從檔案重建
    sync_enriched_index.py  只走 X 書籤 ID 那條路
    canonicalize_duplicates.py / normalize_index_quality.py   標記品質
    fetch_github_repos.py / fetch_youtube_playlist.py / local_ingest.py   各自寫入
    migrate_schema.py       遷移

每一支都合併而不是覆蓋，所以不會大規模掉資料；但沒有共用鎖，而且各自對「這
是同一個來源嗎」有不同的認定——`github-x`、`github_fork-x`、`github_star-x`
會被當成三個東西。於是索引慢慢跟磁碟上的檔案說的不一樣，而沒有任何地方會發現。

現在的規則只有一條：**擷取器寫檔案，索引由這裡從檔案重算。**

這條規則附帶一個約束，而且是刻意的：任何值得留下來的東西都必須寫進卡片檔案，
不能只寫進索引。原本違反這條規則的兩樣——「排除低品質」與「標記重複」的旗標
——實測在正式索引 1680 筆裡剩 0 筆，早就被重建洗光了，而讀它的四個地方一直讀
到 False，沒有任何錯誤。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_failures
import xkb_paths

BUILDER = xkb_paths.SCRIPTS_DIR / "build_search_index.sh"


def rebuild(*, incremental: bool = True, timeout: int = 900) -> bool:
    """從檔案重算索引。回傳有沒有成功。

    增量模式只重新解析 mtime/size 變過的檔案，所以每次擷取之後跑一次的成本，
    跟原本「自己修補幾列」是同一個數量級——差別在結果由檔案決定，不是由呼叫
    順序決定。
    """
    if not BUILDER.exists():
        xkb_failures.note("search index rebuild",
                          FileNotFoundError(str(BUILDER)), repeat=True)
        return False

    env = {
        **os.environ,
        "WORKSPACE_DIR": str(xkb_paths.WORKSPACE),
        "BOOKMARKS_DIR": str(xkb_paths.BOOKMARKS_DIR),
        "CARDS_DIR": str(xkb_paths.CARDS_DIR),
        "INDEX_FILE": str(xkb_paths.INDEX_FILE),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    cmd = ["bash", str(BUILDER)]
    if incremental:
        cmd.append("--incremental")

    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                timeout=timeout)
    except (OSError, subprocess.SubprocessError) as err:
        xkb_failures.note("search index rebuild", err, repeat=True)
        return False

    if result.returncode != 0:
        # 重建失敗要說話。安靜失敗的話，擷取器會回報「收了 N 筆」而那 N 筆
        # 查不到——正是這個模組要消滅的那種等價。
        xkb_failures.note(
            "search index rebuild",
            RuntimeError(f"exit {result.returncode}: {(result.stderr or '').strip()[:200]}"),
            repeat=True)
        return False

    tail = (result.stdout or "").strip().splitlines()
    if tail:
        print(tail[-1])
    return True
