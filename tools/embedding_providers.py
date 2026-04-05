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
import time
from typing import List

from pathlib import Path
import requests


# ── Helpers ──────────────────────────────────────────────────────────────────

def _post(url: str, headers: dict, body: dict, timeout: int = 30) -> dict:
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"Embedding API error {resp.status_code}: {resp.text[:200]}")
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

    def __init__(self, api_key: str, model: str = "gemini-embedding-2-preview"):
        self.api_key = api_key
        self.model = model

    def embed(self, text: str) -> List[float]:
        url = f"{self.BASE_URL}/{self.model}:embedContent?key={self.api_key}"
        body = {"content": {"parts": [{"text": text}]}}
        data = _post(url, headers={"Content-Type": "application/json"}, body=body)
        return data["embedding"]["values"]

    def _embed_batch_impl(self, texts: List[str]) -> List[List[float]]:
        # Gemini supports batchEmbedContents
        url = f"{self.BASE_URL}/{self.model}:batchEmbedContents?key={self.api_key}"
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


def _openclaw_key(key_name: str) -> str:
    """Fallback: read API key from /root/.openclaw/openclaw.json."""
    try:
        config_path = Path("/root/.openclaw/openclaw.json")
        if config_path.exists():
            import json as _json
            cfg = _json.loads(config_path.read_text(encoding="utf-8"))
            return cfg.get("env", {}).get(key_name, "")
    except Exception:
        pass
    return ""


def get_provider() -> EmbeddingProvider:
    """
    Create an EmbeddingProvider based on environment variables.

    Required env vars (per provider):
        EMBEDDING_PROVIDER=gemini    → GEMINI_API_KEY
        EMBEDDING_PROVIDER=openai    → OPENAI_API_KEY
        EMBEDDING_PROVIDER=ollama    → OLLAMA_BASE_URL (optional, defaults to localhost)

    Optional:
        EMBEDDING_MODEL=<model name>  (overrides per-provider default)
    """
    provider_name = os.getenv("EMBEDDING_PROVIDER", "").lower()
    model = os.getenv("EMBEDDING_MODEL", "")

    if provider_name == "gemini":
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is required for EMBEDDING_PROVIDER=gemini")
        return GeminiProvider(api_key=api_key, model=model or "gemini-embedding-2-preview")

    elif provider_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is required for EMBEDDING_PROVIDER=openai")
        return OpenAIProvider(api_key=api_key, model=model or "text-embedding-3-small")

    elif provider_name == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return OllamaProvider(base_url=base_url, model=model or "nomic-embed-text")

    elif provider_name == "":
        # Auto-detect from available API keys (env var first, then openclaw.json)
        gemini_key = os.getenv("GEMINI_API_KEY") or _openclaw_key("GEMINI_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY") or _openclaw_key("OPENAI_API_KEY")
        if gemini_key:
            return GeminiProvider(api_key=gemini_key, model=model or "gemini-embedding-2-preview")
        elif openai_key:
            return OpenAIProvider(api_key=openai_key, model=model or "text-embedding-3-small")
        else:
            raise EnvironmentError(
                "No embedding provider configured. "
                "Set EMBEDDING_PROVIDER=gemini|openai|ollama, "
                "or provide GEMINI_API_KEY / OPENAI_API_KEY."
            )
    else:
        raise EnvironmentError(
            f"Unknown EMBEDDING_PROVIDER: '{provider_name}'. "
            "Supported: gemini, openai, ollama"
        )
