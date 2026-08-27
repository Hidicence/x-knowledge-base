# Embedding setup (portable XKB repository)

This document is for a fresh GitHub checkout. It does not require Hermes, OpenClaw, or any private machine path.

## 1. Configure a workspace

XKB code lives in this repository; personal cards, bookmarks, wiki pages, and indexes belong in a separate workspace. Set `XKB_DATA_DIR` to a directory you control, or set `OPENCLAW_WORKSPACE` / `WORKSPACE_DIR` when integrating with an existing workspace. If none is set, the scripts use the platform user's `~/.openclaw/workspace/memory` default. `EMBEDDING_WORKSPACE_ROOT` is optional metadata consumed by the embedding config; it does not create or discover credentials.

For a portable setup, copy the example and edit only non-secret values:

```sh
cp config/embedding.example.json config/embedding.local.json
export XKB_CONFIG="$PWD/config/embedding.local.json"
export XKB_DATA_DIR="$PWD/runtime-data"
```

`config/embedding.local.json` is local configuration and should not be committed. The checked-in `config/embedding.json` is a sanitized default; `XKB_CONFIG` can point at another sanitized file.

## 2. Choose a provider and model

| Provider | Compatible model examples | Credential / endpoint |
| --- | --- | --- |
| `gemini` (default) | `gemini-embedding-2-preview` (default), other `gemini-*` models | `GEMINI_API_KEY`; Google AI Studio; Gemini endpoint is HTTPS by default |
| `openai` | `text-embedding-3-small` (default), other `text-embedding-*` models | `OPENAI_API_KEY`; OpenAI API |
| `ollama` | any model installed in Ollama, e.g. `nomic-embed-text` | no cloud key; optional `OLLAMA_BASE_URL` (default `http://localhost:11434`) |

The provider/model contract is validated before any network request. A Gemini provider cannot use an OpenAI model name, and vice versa. Provider-specific model availability, quotas, and dimensions remain service-side concerns; do not mix vectors made by different models in one index. Rebuild the index after changing provider or model.

Set values in the shell, or pass an explicit dotenv file to the embedding entry point. Precedence is: process environment > `--env-file` / `XKB_ENV_FILE` > checked-in JSON config > safe defaults. The loader never mutates the process environment and never reads private host configuration paths:

```sh
export EMBEDDING_PROVIDER=gemini
export EMBEDDING_MODEL=gemini-embedding-2-preview
export GEMINI_API_KEY='paste-your-key-here'
```

For an isolated worker, keep the file outside the checkout and pass it explicitly:

```sh
python3 scripts/build_vector_index.py --incremental --env-file "$HOME/.config/xkb/embedding.env"
python3 scripts/health_check.py --mode conflicts --env-file "$HOME/.config/xkb/embedding.env"
export XKB_ENV_FILE="$HOME/.config/xkb/embedding.env"  # wrappers inherit this
```

The env file uses `KEY=VALUE` lines (comments and `export KEY=VALUE` are accepted). Do not source it into logs or commit it. A missing or malformed explicit file fails before any API request.

For OpenAI, use `OPENAI_API_KEY` instead. For local-only Ollama:

```sh
export EMBEDDING_PROVIDER=ollama
export EMBEDDING_MODEL=nomic-embed-text
export OLLAMA_BASE_URL=http://localhost:11434
```

Never put a real key in `.env.example`, JSON config, Markdown, indexes, logs, commits, or bug reports. If a key is exposed, revoke it at the provider and replace it with a newly generated credential; do not try to “clean” a leaked key and keep using it.

## 3. Verify without calling a cloud API

From the skill directory:

```sh
python3 -m unittest discover -s tests -p 'test_embedding_providers.py' -v
python3 -m compileall -q scripts tools
python3 scripts/build_vector_index.py --dry-run --index-file "$XKB_DATA_DIR/bookmarks/search_index.json"
```

The unit tests use placeholder credentials and mocked HTTP responses. `--dry-run` reads the index and reports what would be embedded without creating a provider or making a request. If there is no search index yet, run the ingestion/index setup first; that is not an embedding credential failure.

To validate the configured provider with one real request, use a tiny disposable index and the normal builder (this sends text to the selected provider):

```sh
python3 scripts/build_vector_index.py --index-file "$XKB_DATA_DIR/bookmarks/search_index.json" \
  --vector-file "$XKB_DATA_DIR/bookmarks/vector_index.json" --incremental
```

A successful run reports provider/model and saved vector counts. It does not prove production readiness or quota capacity.

## Fail-fast errors

- `Unknown EMBEDDING_PROVIDER`: use `gemini`, `openai`, or `ollama`.
- `Model ... is not compatible`: choose a model prefix matching the provider.
- `GEMINI_API_KEY is required...` / `OPENAI_API_KEY is required...`: export the credential in the current process; there is no private-path fallback.
- `EMBEDDING_ENDPOINT must be an HTTP(S) URL`: fix the URL; `file://` and local secret paths are rejected.
- `Invalid embedding config ... expected an embedding object` or `embedding.<setting> must be a string`: repair the JSON types; do not rely on falsy values to trigger defaults.
- `Embedding API error <status>`: inspect the status and short response prefix, then check endpoint, model, quota, and network. Responses are bounded and credentials are not printed.

## Credential rotation and redaction

1. Create a replacement key in the provider's official console (Google AI Studio for Gemini, or the corresponding OpenAI account). For Ollama, keep the service local and change its access controls as appropriate.
2. Update the secret in your shell/secret manager, not in repository files.
3. Re-run the configuration tests, then run a small verification request if permitted.
4. Revoke the old key and inspect the diff/log output for accidental disclosure.

When sharing diagnostics, redact values as `***` and remove authorization headers, query-string keys, full URLs containing secrets, local absolute paths, personal card text, and request payloads. XKB's provider errors intentionally include only HTTP status and a bounded response prefix; do not paste even that prefix if the upstream service echoes sensitive input.
