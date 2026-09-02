from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _isolation import clean_env

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import embedding_providers as providers  # noqa: E402


class EmbeddingConfigurationTests(unittest.TestCase):
    def test_explicit_env_file_is_used_below_process_environment(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "embedding.env"
            env_file.write_text(
                "EMBEDDING_PROVIDER=gemini\nEMBEDDING_MODEL=gemini-file-model\nGEMINI_API_KEY=file-key\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {**clean_env(), "GEMINI_API_KEY": "process-key"}, clear=True):
                provider = providers.get_provider(env_file=env_file)
        self.assertEqual(provider.api_key, "process-key")
        self.assertEqual(provider.model, "gemini-file-model")

    def test_explicit_env_file_supplies_credential_without_mutating_process(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "embedding.env"
            env_file.write_text("GEMINI_API_KEY=file-key\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {**clean_env(), }, clear=True):
                provider = providers.get_provider(env_file=env_file)
                self.assertNotIn("GEMINI_API_KEY", os.environ)
        self.assertEqual(provider.api_key, "file-key")

    def test_missing_explicit_env_file_is_actionable(self) -> None:
        with mock.patch.dict(os.environ, {**clean_env(), }, clear=True):
            with self.assertRaisesRegex(FileNotFoundError, "env file"):
                providers.get_provider(env_file=Path("/does/not/exist.env"))

    def test_defaults_to_gemini_and_reads_runtime_credential(self) -> None:
        with mock.patch.dict(os.environ, {**clean_env(), "GEMINI_API_KEY": "runtime-only"}, clear=True):
            config = providers.load_config()
            self.assertEqual(config.provider, "gemini")
            self.assertEqual(config.model, "gemini-embedding-2-preview")
            provider = providers.get_provider()
        self.assertIsInstance(provider, providers.GeminiProvider)
        self.assertEqual(provider.api_key, "runtime-only")

    def test_missing_credential_fails_without_private_path_fallback(self) -> None:
        with mock.patch.dict(os.environ, {**clean_env(), "EMBEDDING_PROVIDER": "gemini"}, clear=True):
            with self.assertRaisesRegex(EnvironmentError, "GEMINI_API_KEY"):
                providers.get_provider()

    def test_unknown_provider_is_actionable(self) -> None:
        with mock.patch.dict(os.environ, {**clean_env(), "EMBEDDING_PROVIDER": "wat"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Supported: gemini, openai, ollama"):
                providers.get_provider()

    def test_incompatible_model_is_rejected_before_provider_creation(self) -> None:
        with mock.patch.dict(os.environ, {**clean_env(), 
            "EMBEDDING_PROVIDER": "gemini",
            "EMBEDDING_MODEL": "text-embedding-3-small",
            "GEMINI_API_KEY": "runtime-only",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "not compatible"):
                providers.get_provider()

    def test_endpoint_must_be_http_url(self) -> None:
        with mock.patch.dict(os.environ, {**clean_env(), 
            "EMBEDDING_PROVIDER": "gemini",
            "GEMINI_API_KEY": "runtime-only",
            "EMBEDDING_ENDPOINT": "file:///tmp/secret",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, r"HTTP\(S\)"):
                providers.get_provider()

    def test_malformed_config_types_are_actionable(self) -> None:
        malformed = {
            "embedding": {
                "provider": ["gemini"],
                "model": "gemini-embedding-2-preview",
                "endpoint": "https://example.test/models",
                "workspace_root": "",
            }
        }
        with mock.patch.dict(os.environ, {**clean_env(), }, clear=True), \
                mock.patch.object(providers, "_load_xkb_config", return_value=malformed):
            with self.assertRaisesRegex(ValueError, r"embedding\.provider.*string"):
                providers.load_config()

    def test_malformed_workspace_root_type_is_actionable(self) -> None:
        malformed = {"embedding": {"workspace_root": 123}}
        with mock.patch.dict(os.environ, {**clean_env(), }, clear=True), \
                mock.patch.object(providers, "_load_xkb_config", return_value=malformed):
            with self.assertRaisesRegex(ValueError, r"embedding\.workspace_root.*string"):
                providers.load_config()

    def test_falsy_malformed_config_types_are_not_defaulted(self) -> None:
        for setting, value in {
            "provider": [],
            "model": {},
            "endpoint": 0,
            "workspace_root": False,
        }.items():
            with self.subTest(setting=setting):
                malformed = {"embedding": {setting: value}}
                with mock.patch.dict(os.environ, {**clean_env(), }, clear=True), \
                        mock.patch.object(providers, "_load_xkb_config", return_value=malformed):
                    with self.assertRaisesRegex(ValueError, rf"embedding\.{setting}.*string"):
                        providers.load_config()

    def test_cli_reports_configuration_errors_without_traceback(self) -> None:
        import contextlib
        import io
        import tempfile

        from scripts import build_vector_index

        with tempfile.TemporaryDirectory() as tmp:
            index_path = Path(tmp) / "search_index.json"
            index_path.write_text('{"items": [{"relative_path": "card.md"}]}', encoding="utf-8")
            args = ["build_vector_index.py", "--index-file", str(index_path), "--vector-file", str(Path(tmp) / "vectors.json")]
            stderr = io.StringIO()
            with mock.patch.object(sys, "argv", args), \
                    mock.patch.object(build_vector_index, "knowledge_section_docs", return_value=[]), \
                    mock.patch.object(build_vector_index, "extract_card_text", return_value="text"), \
                    mock.patch.object(build_vector_index, "load_vector_index", return_value={}), \
                    mock.patch.object(build_vector_index, "get_provider", side_effect=ValueError("embedding.provider must be a string")), \
                    contextlib.redirect_stderr(stderr):
                result = build_vector_index.main()
        self.assertEqual(result, 1)
        self.assertIn("embedding.provider must be a string", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class ProviderRegistryTests(unittest.TestCase):
    def test_active_embedding_entry_points_do_not_read_private_runtime_config(self) -> None:
        scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
        checked = {
            "continuity_recall.py",
            "health_check.py",
            "build_vector_index.py",
        }
        forbidden = ('Path.home() / ".openclaw"', "openclaw.json")
        for name in checked:
            source = (scripts_dir / name).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{name} must use the shared loader")

    def test_registry_contains_supported_providers(self) -> None:
        self.assertEqual(set(providers.PROVIDER_REGISTRY), {"gemini", "openai", "ollama"})

    def test_gemini_batch_request_remains_mockable(self) -> None:
        provider = providers.GeminiProvider("runtime-only")
        with mock.patch.object(providers, "_post", return_value={"embeddings": [{"values": [1.0]}]}) as post:
            self.assertEqual(provider.embed_batch(["hello"]), [[1.0]])
        self.assertIn(":batchEmbedContents", post.call_args.args[0])

    def test_gemini_single_request_uses_configured_endpoint_and_redacts_api_error(self) -> None:
        provider = providers.GeminiProvider("sanitized-gemini-key", base_url="https://mock.example/v1/models")
        response = mock.Mock(ok=False, status_code=401, text="echo sanitized-gemini-key")
        with mock.patch.object(providers.requests, "post", return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError, r"Embedding API error 401") as raised:
                provider.embed("fixture text")
        self.assertNotIn("sanitized-gemini-key", str(raised.exception))
        self.assertEqual(post.call_args.args[0], "https://mock.example/v1/models/gemini-embedding-2-preview:embedContent?key=sanitized-gemini-key")

    def test_configured_workspace_root_is_metadata_only_and_does_not_read_private_path(self) -> None:
        malformed = {"embedding": {"workspace_root": "/private/not-a-checkout"}}
        with mock.patch.dict(os.environ, {**clean_env(), }, clear=True), \
                mock.patch.object(providers, "_load_xkb_config", return_value=malformed):
            config = providers.load_config()
        self.assertEqual(config.workspace_root, "/private/not-a-checkout")

    def test_ollama_provider_is_credential_free_and_mockable(self) -> None:
        with mock.patch.dict(os.environ, {**clean_env(), "EMBEDDING_PROVIDER": "ollama"}, clear=True):
            provider = providers.get_provider()
        with mock.patch.object(providers, "_post", return_value={"embedding": [0.1, 0.2]}):
            self.assertEqual(provider.embed("fixture"), [0.1, 0.2])

    def test_health_cosine_rejects_mixed_vector_dimensions(self) -> None:
        import importlib.util
        health_path = Path(__file__).resolve().parents[1] / "scripts" / "health_check.py"
        spec = importlib.util.spec_from_file_location("health_check_fixture", health_path)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        health_check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health_check)
        with self.assertRaisesRegex(ValueError, "dimension mismatch"):
            health_check.cosine_similarity([1.0, 0.0], [1.0])

    def test_health_embed_passes_env_file_to_shared_loader(self) -> None:
        import importlib.util
        health_path = Path(__file__).resolve().parents[1] / "scripts" / "health_check.py"
        spec = importlib.util.spec_from_file_location("health_check_env_fixture", health_path)
        assert spec is not None and spec.loader is not None
        health_check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health_check)
        provider = mock.Mock()
        provider.embed.return_value = [0.1, 0.2]
        with mock.patch.object(health_check, "get_provider", return_value=provider) as loader:
            result = health_check._embed("ignored-legacy-key", "fixture", env_file="fixture.env")
        self.assertEqual(result, [0.1, 0.2])
        loader.assert_called_once_with(env_file="fixture.env")


if __name__ == "__main__":
    unittest.main()
