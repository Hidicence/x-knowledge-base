#!/usr/bin/env python3
"""
Embedding provider abstraction for x-knowledge-base semantic search.
Supports Gemini, OpenAI, and Ollama via direct HTTP (no SDK required).

Usage:
    from tools.embedding_providers import get_provider
    provider = get_provider()          # reads EMBEDDING_PROVIDER env var
    vector = provider.embed("hello")   # returns List[float]
    vectors = provider.embed_batch(["hello", "world"])  # returns List[List[float]]
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import List
from urllib.parse import urlparse

from pathlib import Path
import requests

# 這個模組會被兩種方式載入：`from tools.embedding_providers import ...`（tools/
# 不在 sys.path）與 `from embedding_providers import ...`（在 path 上）。前者
# 找不到隔壁的 runtime_config，所以這裡自己補上，跟 scripts/ 那邊同一個做法。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_config import load_env_file, runtime_env  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "gemini-embedding-2-preview"
DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str = "gemini"
    model: str = DEFAULT_MODEL
    endpoint: str = DEFAULT_ENDPOINT
    workspace_root: str = ""


def _config_path() -> Path:
    return Path(os.getenv("XKB_CONFIG", str(Path(__file__).resolve().parent.parent / "config" / "embedding.json")))


def _load_xkb_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid embedding config at {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("embedding", {}), dict):
        raise ValueError(f"Invalid embedding config at {path}: expected an embedding object")
    return value


def _http_endpoint(value: str, setting: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{setting} must be an HTTP(S) URL")
    return value.rstrip("/")


def _load_env_file(path: Path) -> dict[str, str]:
    """Read a dotenv-style env file. Thin alias over the one shared loader.

    This used to be a second, byte-for-byte copy of runtime_config.load_env_file:
    same dotenv rules, same "process env wins" precedence, maintained separately.
    Two loaders means two places to fix when the precedence rule turns out to be
    wrong — and on 2026-09-09 it did, when a scheduler's inherited LLM_* silently
    replaced the endpoint an explicit --env-file had named. The embedding side
    had the identical hole for EMBEDDING_* / GEMINI_API_KEY; nobody had noticed
    because nobody had looked at it as the same rule.

    One copy now. The only thing that was ever different was the wording of the
    error, and callers only ever asserted on the shared one's.
    """
    return load_env_file(path)


def load_config(env_file: str | Path | None = None) -> EmbeddingConfig:
    settings = runtime_env(env_file)
    configured = _load_xkb_config().get("embedding", {})
    defaults = {
        "provider": "gemini",
        "model": DEFAULT_MODEL,
        "endpoint": DEFAULT_ENDPOINT,
        "workspace_root": "",
    }
    for setting, value in configured.items():
        if setting in defaults and not isinstance(value, str):
            raise ValueError(f"embedding.{setting} must be a string")
    values = {
        "provider": settings.get("EMBEDDING_PROVIDER") or configured.get("provider", defaults["provider"]),
        "model": settings.get("EMBEDDING_MODEL") or configured.get("model", defaults["model"]),
        "endpoint": settings.get("EMBEDDING_ENDPOINT") or configured.get("endpoint", defaults["endpoint"]),
        "workspace_root": settings.get("EMBEDDING_WORKSPACE_ROOT") or configured.get("workspace_root", defaults["workspace_root"]),
    }
    for setting, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"embedding.{setting} must be a string")
    provider = values["provider"].strip().lower()
    model = values["model"].strip()
    endpoint = values["endpoint"]
    workspace_root = values["workspace_root"]
    return EmbeddingConfig(provider=provider, model=model, endpoint=_http_endpoint(endpoint, "EMBEDDING_ENDPOINT"), workspace_root=workspace_root)


def _validate_model(provider: str, model: str) -> None:
    prefixes = {"gemini": ("gemini-",), "openai": ("text-embedding-",), "ollama": ()}
    if provider not in prefixes:
        raise ValueError(f"Unknown EMBEDDING_PROVIDER: '{provider}'. Supported: {', '.join(PROVIDER_REGISTRY)}")
    if prefixes[provider] and not model.startswith(prefixes[provider]):
        raise ValueError(f"Model '{model}' is not compatible with provider '{provider}'")


# 嵌入 API 連不上時，這個秒數就是每一次召回要等的時間。預設 30 是原本的
# 值；召回是互動路徑，等 30 秒等於當掉，所以留一個可以調的旋鈕，而不是把
# 數字寫死在呼叫裡。
DEFAULT_TIMEOUT = int(os.getenv("XKB_EMBEDDING_TIMEOUT", "30"))


def _post(url: str, headers: dict, body: dict, timeout: int | None = None) -> dict:
    if timeout is None:
        timeout = int(os.getenv("XKB_EMBEDDING_TIMEOUT", str(DEFAULT_TIMEOUT)))
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    if not resp.ok:
        # Upstream bodies can echo request text or credentials. Keep failures
        # actionable without copying potentially sensitive response content.
        raise RuntimeError(f"Embedding API error {resp.status_code}")
    return resp.json()


# ── Base class ────────────────────────────────────────────────────────────────

class EmbeddingProvider:
    """Abstract base for embedding providers."""

    def embed(self, text: str) -> List[float]:
        raise NotImplementedError

    def embed_batch(self, texts: List[str], batch_size: int = 50) -> List[List[float]]:
        """Embed a list of texts, processing in batches."""
        results: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            results.extend(self._embed_batch_impl(batch))
            if i + batch_size < len(texts):
                time.sleep(0.5)  # be polite to rate limits
        return results

    def _embed_batch_impl(self, texts: List[str]) -> List[List[float]]:
        # Default: embed one by one. Subclasses can override for true batching.
        return [self.embed(t) for t in texts]


# ── Gemini ────────────────────────────────────────────────────────────────────

class GeminiProvider(EmbeddingProvider):
    """
    Uses Google Gemini Embedding API.
    Requires: GEMINI_API_KEY
    Default model: gemini-embedding-2-preview
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_ENDPOINT):
        self.api_key = api_key
        self.model = model
        self.base_url = _http_endpoint(base_url, "Gemini endpoint")

    def embed(self, text: str) -> List[float]:
        url = f"{self.base_url}/{self.model}:embedContent?key={self.api_key}"
        body = {"content": {"parts": [{"text": text}]}}
        data = _post(url, headers={"Content-Type": "application/json"}, body=body)
        return data["embedding"]["values"]

    def _embed_batch_impl(self, texts: List[str]) -> List[List[float]]:
        # Gemini supports batchEmbedContents
        url = f"{self.base_url}/{self.model}:batchEmbedContents?key={self.api_key}"
        requests_list = [{"model": f"models/{self.model}", "content": {"parts": [{"text": t}]}} for t in texts]
        body = {"requests": requests_list}
        data = _post(url, headers={"Content-Type": "application/json"}, body=body)
        return [item["values"] for item in data["embeddings"]]


# ── OpenAI ────────────────────────────────────────────────────────────────────

class OpenAIProvider(EmbeddingProvider):
    """
    Uses OpenAI Embedding API.
    Requires: OPENAI_API_KEY
    Default model: text-embedding-3-small
    """

    BASE_URL = "https://api.openai.com/v1/embeddings"

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.model = model

    def embed(self, text: str) -> List[float]:
        return self._embed_batch_impl([text])[0]

    def _embed_batch_impl(self, texts: List[str]) -> List[List[float]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {"model": self.model, "input": texts}
        data = _post(self.BASE_URL, headers=headers, body=body)
        # Sort by index to preserve order
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]


# ── Ollama ────────────────────────────────────────────────────────────────────

class OllamaProvider(EmbeddingProvider):
    """
    Uses Ollama local embedding API.
    Requires: OLLAMA_BASE_URL (default: http://localhost:11434)
    Default model: nomic-embed-text
    """

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "nomic-embed-text"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def embed(self, text: str) -> List[float]:
        url = f"{self.base_url}/api/embeddings"
        body = {"model": self.model, "prompt": text}
        data = _post(url, headers={"Content-Type": "application/json"}, body=body)
        return data["embedding"]

    # Ollama has no native batch endpoint; uses default one-by-one


# ── Factory ───────────────────────────────────────────────────────────────────

PROVIDER_REGISTRY = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
}


def get_provider(env_file: str | Path | None = None) -> EmbeddingProvider:
    """
    Create an EmbeddingProvider based on environment variables.

    Required env vars (per provider):
        EMBEDDING_PROVIDER=gemini    → GEMINI_API_KEY
        EMBEDDING_PROVIDER=openai    → OPENAI_API_KEY
        EMBEDDING_PROVIDER=ollama    → OLLAMA_BASE_URL (optional, defaults to localhost)

    Optional:
        EMBEDDING_MODEL=<model name>  (overrides per-provider default)
    """
    settings = runtime_env(env_file)
    config = load_config(env_file=env_file)
    provider_name, model = config.provider, config.model
    _validate_model(provider_name, model)

    if provider_name == "gemini":
        api_key = settings.get("GEMINI_API_KEY", "")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is required for EMBEDDING_PROVIDER=gemini")
        return GeminiProvider(api_key=api_key, model=model, base_url=config.endpoint)

    elif provider_name == "openai":
        api_key = settings.get("OPENAI_API_KEY", "")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is required for EMBEDDING_PROVIDER=openai")
        return OpenAIProvider(api_key=api_key, model=model)

    elif provider_name == "ollama":
        base_url = settings.get("OLLAMA_BASE_URL") or "http://localhost:11434"
        return OllamaProvider(base_url=base_url, model=model or "nomic-embed-text")

    raise ValueError(f"Unknown EMBEDDING_PROVIDER: '{provider_name}'. Supported: {', '.join(PROVIDER_REGISTRY)}")
