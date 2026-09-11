"""排程給的環境變數不可以蓋掉 --env-file 指名的供應商。

2026-09-01 到 09-09，書籤轉卡每晚都回報成功，但一張卡都沒產出：每一筆都以
404 失敗，說找不到 gpt-5.6-luna。手動跑同一支腳本、同一份 env 檔會成功。
差別是排程（Hermes）在啟動腳本前就把自己的 LLM_API_URL / LLM_API_KEY /
LLM_MODEL 放進環境，而 runtime_env() 讓行程環境蓋過 env 檔，於是 --env-file
指的那個端點從頭到尾沒被用到。

八天、25 筆失敗，每日健檢一次都沒提——它看的是索引檔多久沒被寫過，而沒有
新卡片本來就不會寫索引，所以它報的是症狀的症狀。

這裡測的是：明確傳了 --env-file 之後，那份檔案定義的鍵由檔案說了算。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
HELPER = (SKILL_DIR / "scripts" / "_xkb_env.sh").as_posix()

SCHEDULED_ENTRY_POINTS = [
    "run_bookmark_batch.sh",
    "run_ingestion_batch.sh",
    "run_candidate_governance.sh",
    "run_distill_batch.sh",
]

# Windows 上光寫 "bash" 會解析到 WSL，那個 bash 看不到這份 checkout。
_WINDOWS_BASH = [
    "C:/Program Files/Git/bin/bash.exe",
    "C:/Program Files (x86)/Git/bin/bash.exe",
]


def _bash() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in _WINDOWS_BASH:
        if Path(candidate).exists():
            return candidate
    raise unittest.SkipTest("no POSIX bash available")


class SchedulerEnvPrecedence(unittest.TestCase):
    def test_env_file_keys_beat_the_ambient_environment(self):
        bash = _bash()
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "xkb.env"
            env_file.write_text(
                "# comment\n"
                "\n"
                "LLM_API_URL=https://api.example.test/v1\n"
                "export LLM_MODEL=the-model-the-file-names\n",
                encoding="utf-8",
            )
            script = (
                f'source "{HELPER}"\n'
                f'xkb_env_file_wins "{env_file.as_posix()}"\n'
                'echo "url=${LLM_API_URL-<unset>}"\n'
                'echo "model=${LLM_MODEL-<unset>}"\n'
                'echo "unrelated=${SOME_OTHER_VAR-<unset>}"\n'
            )
            env = dict(os.environ)
            env.update({
                "LLM_API_URL": "http://localhost:8080/v1",
                "LLM_MODEL": "a-model-that-endpoint-does-not-serve",
                "SOME_OTHER_VAR": "kept",
            })
            out = subprocess.run(
                [bash, "-c", script], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
            ).stdout

        # 檔案提到的鍵：環境裡那份被清掉，讓 runtime_env() 讀得到檔案的值。
        self.assertIn("url=<unset>", out)
        self.assertIn("model=<unset>", out)
        # 檔案沒提到的鍵不受影響——這不是「清空環境」。
        self.assertIn("unrelated=kept", out)

    def test_missing_or_empty_env_file_is_a_no_op(self):
        bash = _bash()
        script = (
            f'source "{HELPER}"\n'
            'xkb_env_file_wins ""\n'
            'xkb_env_file_wins "/nonexistent/xkb.env"\n'
            'echo "url=${LLM_API_URL-<unset>}"\n'
        )
        env = dict(os.environ, LLM_API_URL="http://localhost:8080/v1")
        out = subprocess.run(
            [bash, "-c", script], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
        ).stdout
        self.assertIn("url=http://localhost:8080/v1", out)

    def test_every_scheduled_entry_point_applies_it(self):
        """新增排程入口時，這個測試會提醒它也要接上。"""
        for name in SCHEDULED_ENTRY_POINTS:
            text = (SKILL_DIR / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn("_xkb_env.sh", text)
                self.assertIn('xkb_env_file_wins "$ENV_FILE"', text)


if __name__ == "__main__":
    unittest.main()
