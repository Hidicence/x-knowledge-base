"""測試檔要真的跑得起來，不能安靜地什麼都不做。

VPS 上沒有 pytest，測試是用 `python3 tests/test_x.py` 一支一支跑的。
少了 `if __name__ == "__main__": unittest.main()` 的檔案，這樣執行會
**什麼都不做然後回傳 0**——在逐檔迴圈裡看起來跟通過一模一樣。

2026-09-02 抓到的第一個：tests/test_xkb_memory_service.py 的 32 個測試
在 VPS 上從來沒有跑過，而部署後的驗收一直是綠的。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent

_MAIN = re.compile(r"^if __name__ == .__main__.:", re.M)


class TestsActuallyRunTest(unittest.TestCase):
    def test_every_test_file_runs_when_executed_directly(self) -> None:
        offenders = [
            path.name
            for path in sorted(TESTS.glob("test_*.py"))
            if not _MAIN.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            offenders, [],
            "加上 if __name__ == \"__main__\": unittest.main()；"
            "否則在沒有 pytest 的機器上，這個檔案會安靜地回傳 0",
        )


if __name__ == "__main__":
    unittest.main()
