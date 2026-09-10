"""XKB 只能有一條設定優先順序的規則，而且只有一份實作。

2026-09-09 的故障是：排程在啟動腳本前把自己的 LLM_API_URL / LLM_MODEL 放進
環境，蓋掉 --env-file 指名的端點，於是書籤轉卡連續八天回報成功卻 0 產出。

修完那一個之後往下看，發現同一條規則在這個 repo 裡寫了兩次：
tools/runtime_config.py 一份，tools/embedding_providers.py 自己又抄了一份，
負責 EMBEDDING_* 與各家 API key。兩份的 dotenv 解析逐字相同、優先順序相同、
洞也相同——EMBEDDING_PROVIDER 或 GEMINI_API_KEY 同樣會被外面的環境蓋掉。
沒人發現，因為沒有人把它們當成同一條規則看。

這裡測三件事：規則只有一份實作、規則本身是什麼、embedding 那條路真的走它。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import runtime_config  # noqa: E402
import embedding_providers  # noqa: E402


class OneImplementation(unittest.TestCase):
    def test_embedding_side_delegates_to_the_shared_loader(self):
        """不要再長出第二份 dotenv 解析。"""
        source = (ROOT / "tools" / "embedding_providers.py").read_text(encoding="utf-8")
        self.assertIn("return load_env_file(path)", source)
        # 第二份實作的指紋：自己的錯誤訊息與自己的逐行解析。
        self.assertNotIn("Invalid embedding env file", source)
        self.assertNotIn("Embedding env file not found", source)

    def test_embedding_side_does_not_reimplement_precedence(self):
        """`os.getenv(X) or env_values.get(X)` 就是在原地重寫優先順序。"""
        source = (ROOT / "tools" / "embedding_providers.py").read_text(encoding="utf-8")
        self.assertNotIn("env_values.get(", source)


class TheRule(unittest.TestCase):
    def _env_file(self, tmp: str, body: str) -> Path:
        path = Path(tmp) / "xkb.env"
        path.write_text(body, encoding="utf-8")
        return path

    def test_a_real_process_value_still_wins(self):
        """互動時臨時覆蓋一個設定，仍然要有效。"""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = self._env_file(tmp, "XKB_TEST_MODEL=from-file\n")
            with mock.patch.dict(os.environ, {"XKB_TEST_MODEL": "from-process"}):
                settings = runtime_config.runtime_env(env_file)
        self.assertEqual(settings["XKB_TEST_MODEL"], "from-process")

    def test_an_empty_process_value_does_not_blank_out_the_file(self):
        """`export FOO=` 是「匯出了但沒填」，不是一個值。

        讓它贏，得到的是一把空憑證和一個既不提檔案也不提變數的錯誤——
        又一條安靜出錯的路。
        """
        with tempfile.TemporaryDirectory() as tmp:
            env_file = self._env_file(tmp, "XKB_TEST_MODEL=from-file\n")
            with mock.patch.dict(os.environ, {"XKB_TEST_MODEL": ""}):
                settings = runtime_config.runtime_env(env_file)
        self.assertEqual(settings["XKB_TEST_MODEL"], "from-file")

    def test_an_empty_process_value_with_no_file_value_stays_empty(self):
        """檔案沒提到的鍵不受影響，這不是「把空值一律當沒設定」。"""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = self._env_file(tmp, "XKB_TEST_OTHER=x\n")
            with mock.patch.dict(os.environ, {"XKB_TEST_MODEL": ""}):
                settings = runtime_config.runtime_env(env_file)
        self.assertEqual(settings["XKB_TEST_MODEL"], "")


class EmbeddingObeysIt(unittest.TestCase):
    def test_config_reads_the_env_file_through_the_shared_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "xkb.env"
            env_file.write_text(
                "EMBEDDING_PROVIDER=gemini\nEMBEDDING_MODEL=gemini-from-file\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"EMBEDDING_MODEL": ""}):
                config = embedding_providers.load_config(env_file=env_file)
        self.assertEqual(config.model, "gemini-from-file")

    def test_a_real_env_var_still_overrides_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "xkb.env"
            env_file.write_text(
                "EMBEDDING_PROVIDER=gemini\nEMBEDDING_MODEL=gemini-from-file\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"EMBEDDING_MODEL": "gemini-from-process"}):
                config = embedding_providers.load_config(env_file=env_file)
        self.assertEqual(config.model, "gemini-from-process")

    def test_the_api_key_comes_from_the_file_when_the_variable_is_empty(self):
        """這是實務上最痛的那個：空的 GEMINI_API_KEY 蓋掉檔案裡真的那把。"""
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "xkb.env"
            env_file.write_text(
                "EMBEDDING_PROVIDER=gemini\n"
                "EMBEDDING_MODEL=gemini-embedding-2-preview\n"
                "GEMINI_API_KEY=key-from-file\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
                provider = embedding_providers.get_provider(env_file=env_file)
        self.assertEqual(provider.api_key, "key-from-file")


if __name__ == "__main__":
    unittest.main()
