"""Verify/repair saved cards and finish ingestion without regenerating them.

The outbox records retryable publication, not proof about a future backend.
Provider output is deliberately excluded from errors and queue receipts.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

import xbrain_recall as backend
import xkb_index
import xkb_paths


class PublicationError(RuntimeError):
    def __init__(self, stage: str, *, unavailable: bool = False):
        self.stage = stage
        self.unavailable = unavailable
        scope = "backend unavailable" if unavailable else "card pending"
        super().__init__(f"{scope}: {stage}; retry scripts/gbrain_publish.py")


@contextmanager
def _db():
    path = Path(os.getenv("XKB_PUBLISH_OUTBOX", str(xkb_paths.XKB_DATA_DIR / "publish-outbox.sqlite")))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS outbox (slug TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL)")
            yield db
    finally:
        db.close()


def transport_content(text: str) -> str:
    # JavaScript YAML must not round identity integers larger than 2**53.
    parts = text.split("---", 2)
    if len(parts) == 3 and not parts[0].strip():
        parts[1] = re.sub(r"(?m)^(id|tweet_id|post_id):[ \t]*([0-9]+)[ \t]*$",
                          lambda m: m[1] + ": " + json.dumps(m[2]), parts[1])
        return "---".join(parts)
    return text


def storage_slug(slug: str) -> str:
    # GBrain lowercases slugs; preserve case-sensitive source identities.
    return "xkb-case-" + slug.encode("utf-8").hex() if slug != slug.lower() else slug


def _runtime():
    settings = backend._safe_runtime_env()
    root = backend._resolve_gbrain_dir(settings)
    env = backend._make_subprocess_env(True, settings)
    if root is None:
        raise PublicationError("runtime", unavailable=True)
    return root, {**env, "GBRAIN_DIR": str(root)}


def _run(command, root, env, content=None):
    try:
        return subprocess.run(command, input=content, capture_output=True, text=True,
                              encoding="utf-8", env=env, cwd=str(root), timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise PublicationError("transport", unavailable=True) from None


def _verify(root, env, slug=None, content=None) -> bool:
    args = ["--probe"] if slug is None else ["--stdin", slug]
    result = _run([backend.BUN, "run", str(Path(__file__).with_name("gbrain_verify.ts")), *args],
                  root, env, content)
    try:
        state = json.loads(result.stdout)["status"]
    except (ValueError, TypeError, KeyError):
        raise PublicationError("verify protocol", unavailable=True) from None
    if result.returncode == 0 and state == ("ready" if slug is None else "verified"):
        return True
    if slug is not None and result.returncode == 1 and state == "mismatch":
        return False
    raise PublicationError("verify", unavailable=state != "invalid_card")


def check_backend() -> None:
    """Read-only preflight before spending on new card generation."""
    root, env = _runtime()
    if not env.get("GEMINI_API_KEY"):
        raise PublicationError("embedding credential", unavailable=True)
    _verify(root, env)


def publish(card_path: Path, slug: str) -> str:
    """Return verified (no writes) or published (repaired), or raise safely."""
    card_path = Path(card_path).resolve()
    try:
        content = card_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        # Rotate unreadable receipts so one missing file cannot starve recovery.
        try:
            with _db() as db:
                db.execute("UPDATE outbox SET status='pending',stage='read saved card',attempts=attempts+1,updated=? WHERE slug=?",
                           (time.time(), slug))
        except (OSError, sqlite3.Error):
            raise PublicationError("outbox", unavailable=True) from None
        raise PublicationError("read saved card") from None
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    stage = "outbox"
    try:
        with _db() as db:
            db.execute("INSERT INTO outbox VALUES (?,?,?,?,?,0,?) ON CONFLICT(slug) DO UPDATE SET path=excluded.path,sha256=excluded.sha256,status=excluded.status,stage=excluded.stage,updated=excluded.updated",
                       (slug, str(card_path), digest, "pending", stage, time.time()))
        stage = "verify"
        root, env = _runtime()
        key = storage_slug(slug)
        transported = transport_content(content)
        outcome = "verified"
        if not _verify(root, env, key, transported):
            if not env.get("GEMINI_API_KEY"):
                raise PublicationError("embedding credential", unavailable=True)
            for stage in ("put", "embed"):
                result = _run([backend.BUN, "run", str(root / "src/cli.ts"), stage, key,
                               "--source", "default"], root, env, transported if stage == "put" else None)
                if result.returncode:
                    # Only explicit input errors are item-scoped. Unknown outages
                    # must stop paid generation, and raw output stays private.
                    output = (result.stderr or "") + (result.stdout or "")
                    item_error = bool(re.search(r"(?i)(invalid slug|invalid yaml|input too long|request body too large|status(?: code)?[:= ]+(?:400|413|422)\b)", output))
                    raise PublicationError(stage, unavailable=not item_error)
            stage = "verify"
            if not _verify(root, env, key, transported):
                raise PublicationError(stage)
            outcome = "published"
        stage = "card changed"
        if card_path.read_text(encoding="utf-8") != content:
            raise PublicationError(stage)
        with _db() as db:
            changed = db.execute("UPDATE outbox SET status='verified',stage='verified',attempts=attempts+1,updated=? WHERE slug=? AND sha256=?",
                                 (time.time(), slug, digest)).rowcount
        if not changed:
            raise PublicationError(stage)
        return outcome
    except (PublicationError, OSError, UnicodeError, sqlite3.Error) as exc:
        error = exc if isinstance(exc, PublicationError) else PublicationError(stage, unavailable=stage == "outbox" or isinstance(exc, sqlite3.Error))
        try:
            with _db() as db:
                db.execute("UPDATE outbox SET status='pending',stage=?,attempts=attempts+1,updated=? WHERE slug=? AND sha256=?",
                           (error.stage, time.time(), slug, digest))
        except (OSError, sqlite3.Error):
            error = PublicationError("outbox", unavailable=True)
        raise error from None


class PublicationBatch:
    """One ingestion lifecycle: budget, saved-card recovery and derived index."""
    def __init__(self, publisher=None, *, recovery_limit=20):
        self.publisher = publisher or publish
        self.recovery_limit = recovery_limit
        self.generated = self.recovered = self.verified = self.failed = 0
        self.revisited = self.deferred = self.saved = 0
        self.blocked = False
        self._seen = set()

    def ready(self) -> bool:
        try:
            check_backend()
        except PublicationError as exc:
            self.blocked = True
            print(str(exc))
        return not self.blocked

    def publish(self, path, slug, *, generated=False) -> bool:
        if self.blocked:
            return False
        self._seen.add(slug)
        self.saved += int(Path(path).is_file())
        self.generated += int(generated)
        try:
            outcome = self.publisher(path, slug)
            if outcome is False:
                raise PublicationError("publisher", unavailable=True)
            if not generated:
                if outcome == "verified":
                    self.verified += 1
                else:
                    self.recovered += 1
            return True
        except PublicationError as exc:
            self.failed += 1
            self.blocked = exc.unavailable
            print(str(exc))
            return False

    def existing(self, path, slug) -> bool:
        """Existing cards never consume the new-generation budget."""
        if not Path(path).is_file():
            return False
        if slug not in self._seen and not self.blocked:
            self.revisited += 1
            self.publish(path, slug)
        return True

    def recover_saved(self, cards) -> None:
        """Rotate background verification; also discover files without receipts."""
        with _db() as db:
            updated = dict(db.execute("SELECT slug,updated FROM outbox"))
        candidates = sorted(((Path(path), slug) for path, slug in cards
                             if slug not in self._seen and Path(path).is_file()),
                            key=lambda item: (updated.get(item[1], 0), item[1]))
        available = max(0, self.recovery_limit - self.revisited)
        self.deferred += max(0, len(candidates) - available)
        for path, slug in candidates[:available]:
            if self.blocked:
                break
            self.revisited += 1
            self.publish(path, slug)

    def finish(self, indexer=None) -> bool:
        # Saved evidence remains searchable locally even if publication failed.
        indexed = (indexer or xkb_index.finish_ingest)(self.saved) if self.saved else True
        if not indexed:
            # Index completion is part of recovery, not an ephemeral exit code.
            with _db() as db:
                db.executemany("UPDATE outbox SET status='pending',stage='index',updated=? WHERE slug=?",
                               [(time.time(), slug) for slug in self._seen])
        print(json.dumps({key: getattr(self, key) for key in
                          ("generated", "recovered", "verified", "failed", "deferred", "blocked")}))
        return bool(indexed and not self.failed and not self.blocked)


def retry_pending(limit=20, *, batch=None):
    batch = batch or PublicationBatch()
    with _db() as db:
        rows = db.execute("SELECT path,slug FROM outbox WHERE status='pending' ORDER BY updated LIMIT ?",
                          (max(0, limit),)).fetchall()
    attempted, before = 0, batch.failed
    for path, slug in rows:
        if batch.blocked:
            break
        if slug in batch._seen:
            continue
        attempted += 1
        batch.revisited += 1
        batch.publish(Path(path), slug)
    return {"attempted": attempted, "failed": batch.failed - before}


if __name__ == "__main__":
    import xkb_usage
    xkb_usage.record(__file__)
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    batch = PublicationBatch()
    print(json.dumps(retry_pending(args.limit, batch=batch)))
    raise SystemExit(0 if batch.finish() else 1)
