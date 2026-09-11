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
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xkb_failures
import xkb_paths

BUILDER = xkb_paths.SCRIPTS_DIR / "build_search_index.sh"


_SOURCE_URL = re.compile(r'^source_url:[ \t]*"?([^"\n]*?)"?[ \t]*$', re.MULTILINE)

_by_stem: dict[str, list[Path]] | None = None
_by_url: dict[str, list[Path]] | None = None


def _scan() -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    """掃一次磁碟，建 stem → 檔案 與 source_url → 檔案 兩張表。

    每一列各自去掃全部檔案的話是 1680 × 3300 次讀檔。掃一次就夠。
    """
    global _by_stem, _by_url
    if _by_stem is not None and _by_url is not None:
        return _by_stem, _by_url
    by_stem: dict[str, list[Path]] = {}
    by_url: dict[str, list[Path]] = {}
    for root in (xkb_paths.CARDS_DIR, xkb_paths.BOOKMARKS_DIR):
        if not root.exists():
            continue
        for found in root.rglob("*.md"):
            if found.name.startswith(".") or not found.is_file():
                continue
            by_stem.setdefault(found.stem, []).append(found)
            try:
                text = found.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            match = _SOURCE_URL.search(text)
            url = match.group(1).strip() if match else ""
            # 只有真的是網址才當識別依據。空字串會把所有沒有來源的卡片串成一組。
            if url.startswith(("http://", "https://")):
                by_url.setdefault(url, []).append(found)
    _by_stem, _by_url = by_stem, by_url
    return by_stem, by_url


def reset_scan() -> None:
    """測試用，或磁碟在同一個行程裡被改過之後。"""
    global _by_stem, _by_url
    _by_stem = _by_url = None


def files_for_item(item: dict, *, by_source: bool = True) -> list[Path]:
    """索引的一列代表的是「一組檔案」，不是一個檔案。

    by_source 決定「一組」有多大，而這個差別很要緊：

      True （預設）連同來源相同的其他檔案一起——品質排除要的是這個。一個
            沒被充實過的空殼書籤，它的每一份副本都該一起排除。

      False 只認路徑與檔名相同的那些。去重要的是這個：重複組本來就共用同一
            個來源，用來源去找會連「要保留的那一份」都撈進來。2026-09-10 我
            就是這樣寫的，結果整組七份全被標成排除——那份知識會從召回裡完全
            消失，而輸出只會說「已標記」。


    索引會依 source_url 去重，所以同一份知識在磁碟上可能有兩三個檔案——同一
    個書籤被歸進兩個分類資料夾、或書籤與卡片各一份——而索引裡只留下最好的那
    一列。

    2026-09-10：品質腳本照著那一列去標記檔案，標到的是「贏的那一列指向的檔
    案」，另外那幾份沒標。重建之後去重會挑「沒有被排除」的那一份留下，於是
    排除等於沒有發生：8 筆標記，索引裡只看得到 5 筆生效，而兩支腳本都回報成功。

    所以要標就要標齊。這裡回傳同一份知識的所有檔案。
    """
    by_stem, by_url = _scan()
    paths: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path.resolve())
        if key not in seen and path.is_file():
            seen.add(key)
            paths.append(path)

    raw = (item.get("path") or "").strip()
    if raw and Path(raw).is_absolute():
        _add(Path(raw))
    # 這一列自己的檔案。relative_path 的基準歷來有三種寫法，全部試過。
    rel = (item.get("relative_path") or "").strip()
    if rel:
        for base in (xkb_paths.WORKSPACE, xkb_paths.BOOKMARKS_DIR,
                     xkb_paths.CARDS_DIR):
            _add(base / rel)

    # 以下是「擴散到同一份知識的其他副本」，只在整組模式下做。
    #
    # 原本這段靠檔名的擴散在 by_source 判斷之外，於是 by_source=False 仍然會
    # 跨目錄擴散：normalize_index_quality 標一筆原始書籤時，會連 cards/ 裡同名
    # 的那張充實過的卡片一起標掉。去重那邊沒事只是因為它另外算了 keep_files，
    # 品質排除沒有那道保險。
    if by_source:
        stem = Path(rel or raw).stem
        for found in by_stem.get(stem, []):
            _add(found)
        url = (item.get("source_url") or "").strip()
        if url.startswith(("http://", "https://")):
            for found in by_url.get(url, []):
                _add(found)
    return paths


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
