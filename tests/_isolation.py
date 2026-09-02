"""在乾淨的環境裡跑，而不是在沒有環境的地方跑。

這些測試要證明的是「不會偷偷讀到主機上的憑證或設定」。在 Linux 上，
「把環境變數清光」剛好等於那件事；在 Windows 上不等於，而且在 Linux
上其實也不完全等於：

  - 子行程拿到空環境時，python.exe 連 hash 亂數都取不到就死了
    （Fatal Python error: _Py_HashRandomization_Init），returncode 1、
    stderr 全空——看起來像被測的腳本壞掉，其實它一行都沒跑到。
  - Path.home() 與 expanduser("~") 在 Windows 看 USERPROFILE。清掉之後
    直接 RuntimeError: Could not determine home directory.
  - 反過來，POSIX 上把 HOME 清掉，expanduser 會退回 /etc/passwd 裡的
    真實家目錄。那不是隔離，那是隔離破洞——只是它不會報錯，所以沒人發現。

所以要清掉的是「主機的身分與憑證」，不是「作業系統本身」，而家目錄要
指到一個空目錄、不能不設。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# 作業系統自己要用的變數，跟被測的行為無關。少了它們，Windows 上的
# 子行程根本開不起來。
_WINDOWS_ESSENTIALS = (
    "SystemRoot", "SystemDrive", "WINDIR", "COMSPEC", "PATHEXT",
    "TEMP", "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)

_SCRATCH_HOME: str | None = None


def scratch_home() -> str:
    """一個空的家目錄，整個測試行程共用。不是主機的家目錄。"""
    global _SCRATCH_HOME
    if _SCRATCH_HOME is None:
        _SCRATCH_HOME = tempfile.mkdtemp(prefix="xkb-test-home-")
    return _SCRATCH_HOME


def clean_env() -> dict[str, str]:
    """作業系統開得起來的最小集合，加上一個空的家目錄。

    用法是把它展開在測試自己的變數前面：

        with mock.patch.dict(os.environ, {**clean_env(), "XKB_ENV_FILE": ...}, clear=True):
    """
    env = {name: os.environ[name] for name in _WINDOWS_ESSENTIALS if name in os.environ}
    home = scratch_home()
    env["HOME"] = home
    env["USERPROFILE"] = home
    return env


def isolated_env(home: Path | str | None = None, **overrides: str | None) -> dict[str, str]:
    """clean_env() 加上指定的變數；home 同時設 HOME 與 USERPROFILE。

    兩個都要設，因為 expanduser 在 POSIX 讀 HOME、在 Windows 讀
    USERPROFILE——只設一個，隔離就只在其中一個平台成立。
    """
    env = clean_env()
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
    env.update({key: value for key, value in overrides.items() if value is not None})
    return env


# fixture 用沒有副檔名、靠 shebang 執行的腳本假裝成 bun / git。Windows 沒有
# 那個 exec 語意，跑不起來的是「假裝」那一層，不是被測的契約。明講跳過，
# 不要讓它以紅燈的樣子留著——紅燈留久了就沒人看了。
needs_posix_exec = unittest.skipIf(
    os.name == "nt",
    "fixture 需要 POSIX 的 shebang 執行語意；這條契約在 VPS / CI 上驗",
)
