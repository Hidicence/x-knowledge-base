#!/usr/bin/env python3
"""在卡片檔案的 frontmatter 上做標記——索引是衍生物，檔案才是資產。

search_index.json 的每一個欄位都可以從卡片檔案重算出來（2026-09-10 實測：
把索引整份重建，1680 筆一模一樣，而且比正在用的那份還準——它找回了三張現行
索引漏掉的卡）。所以索引可以隨時刪掉重建。

但這件事有一個前提：**沒有任何東西只活在索引裡**。原本有兩樣：

    normalize_index_quality  把低品質的項目標成 excluded
    canonicalize_duplicates  把重複的項目標成 excluded

兩支都寫在索引列上，而它們的檔頭都明白寫著「不動 source markdown」。結果是
每次重建都把它們的成果洗掉一次：實測正式索引 1680 筆裡帶 excluded 的有 0 筆，
而讀這個旗標的四個地方一直讀到 False，沒有任何錯誤、沒有任何人發現。

所以標記要寫進檔案。這支模組就是那件事，只有這件事。

刻意不做完整的 YAML 解析：這裡只處理 `key: value` 這一種形狀，而 XKB 的卡片
frontmatter 全部都是這種形狀。引進一個 YAML 相依只為了設一個布林值，換來的是
「重寫整份 frontmatter」的風險——註解、順序、原本的引號都可能被改掉。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def has_frontmatter(text: str) -> bool:
    return bool(FRONTMATTER.match(text))


def get(text: str, key: str) -> str | None:
    """讀一個 frontmatter 欄位的原始字串；沒有這個欄位回 None。"""
    match = FRONTMATTER.match(text)
    if not match:
        return None
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.*?)\s*$", re.MULTILINE)
    found = pattern.search(match.group(1))
    return found.group(1) if found else None


def set_field(text: str, key: str, value: str) -> str:
    """設一個 frontmatter 欄位，回傳新的內容。

    已經有這個欄位就就地換值，沒有就加在 frontmatter 結尾——不重排、不重寫
    其他行。沒有 frontmatter 的檔案原樣回傳：這支模組的職責是標記，不是替
    別人決定檔案該長什麼樣。
    """
    match = FRONTMATTER.match(text)
    if not match:
        return text
    block = match.group(1)
    line = f"{key}: {value}"
    pattern = re.compile(rf"^{re.escape(key)}:.*$", re.MULTILINE)
    if pattern.search(block):
        new_block = pattern.sub(line, block, count=1)
    else:
        new_block = block + "\n" + line
    return text[:match.start(1)] + new_block + text[match.end(1):]


def mark_excluded(path: Path, reason: str = "", *, dry_run: bool = False) -> bool:
    """把一張卡標成不進召回。已經標過就不重寫，回傳有沒有改動。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not has_frontmatter(text):
        # 沒有 frontmatter 就沒有地方放這個旗標。安靜跳過會讓「標記了」和
        # 「標記不上」長得一樣，所以回 False 讓呼叫端自己算得出來。
        return False
    current = (get(text, "excluded") or "").strip().lower()
    already = current in {"true", "yes", "1"}
    if already:
        return False
    updated = set_field(text, "excluded", "true")
    if reason:
        updated = set_field(updated, "excluded_reason", reason.replace("\n", " ")[:200])
    if dry_run:
        return True
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def unmark_excluded(path: Path, *, dry_run: bool = False) -> bool:
    """撤掉排除標記。回傳有沒有改動。

    一個把知識從召回裡拿掉的決定，必須有辦法反悔。2026-09-10 一個寫錯的
    「標記整組」把重複組裡要保留的那一份也標掉了——那份知識會完全消失，而
    當時沒有任何工具可以還原，只能一個檔案一個檔案手動改。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not has_frontmatter(text):
        return False
    if (get(text, "excluded") or "").strip().lower() not in {"true", "yes", "1"}:
        return False
    updated = set_field(text, "excluded", "false")
    if get(updated, "excluded_reason") is not None:
        updated = set_field(updated, "excluded_reason", "")
    if dry_run:
        return True
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def is_excluded(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return (get(text, "excluded") or "").strip().lower() in {"true", "yes", "1"}
