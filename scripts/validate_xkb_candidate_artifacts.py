#!/usr/bin/env python3
"""Validate Stage 2 candidate artifacts without mutating the input batch."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REQUIRED_SHA = 64
ALLOWED_STATUS = {"observed", "aggregated", "review_ready", "hold", "rejected", "expired"}
ALLOWED_DECISIONS = {"REVIEW_READY", "HOLD", "REJECT_SYSTEM_ECHO", "EXPIRED"}


def error(errors: list[str], path: Path, message: str) -> None:
    errors.append(f"{path}: {message}")


def validate_artifact(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"{path}: invalid JSON: {exc}"]
    if obj.get("schema") != "xkb-candidate-artifact.v1":
        error(errors, path, "wrong schema")
    for key in ("artifact_id", "candidate_key", "status", "provenance", "evidence", "decision"):
        if key not in obj:
            error(errors, path, f"missing required field {key}")
    if obj.get("status") not in ALLOWED_STATUS:
        error(errors, path, "invalid status")
    provenance = obj.get("provenance", {})
    for key in ("source_kind", "evidence_type", "source_path", "source_sha256"):
        if key not in provenance:
            error(errors, path, f"missing provenance.{key}")
    source_sha = provenance.get("source_sha256", "")
    if not isinstance(source_sha, str) or len(source_sha) != REQUIRED_SHA:
        error(errors, path, "invalid source_sha256")
    evidence = obj.get("evidence", [])
    if not isinstance(evidence, list) or not evidence:
        error(errors, path, "evidence must be non-empty array")
    for index, item in enumerate(evidence if isinstance(evidence, list) else []):
        excerpt = item.get("excerpt", "")
        digest = item.get("evidence_sha256", "")
        if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != digest:
            error(errors, path, f"evidence[{index}] sha256 mismatch")
        if not isinstance(item.get("induction_eligible"), bool):
            error(errors, path, f"evidence[{index}] induction_eligible must be boolean")
    decision = obj.get("decision", {})
    if decision.get("label") not in ALLOWED_DECISIONS:
        error(errors, path, "invalid decision label")
    if decision.get("automatic_promotion") is not False:
        error(errors, path, "automatic_promotion must be false")
    if obj.get("status") == "review_ready" and decision.get("label") != "REVIEW_READY":
        error(errors, path, "review_ready status/decision mismatch")
    return errors


def validate_batch(batch: Path) -> dict:
    errors: list[str] = []
    manifest_path = batch / "manifest.json"
    if not manifest_path.is_file():
        return {"valid": False, "artifact_count": 0, "errors": [f"{batch}: missing manifest.json"]}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"valid": False, "artifact_count": 0, "errors": [f"{manifest_path}: invalid JSON: {exc}"]}
    paths = sorted(p for p in batch.glob("*.json") if p.name != "manifest.json")
    declared = manifest.get("artifact_count")
    if declared != len(paths):
        errors.append(f"{batch}: manifest artifact_count={declared} actual={len(paths)}")
    if manifest.get("read_only") is not True:
        errors.append(f"{batch}: manifest read_only is not true")
    if manifest.get("automatic_promotion") is not False:
        errors.append(f"{batch}: manifest automatic_promotion is not false")
    for path in paths:
        errors.extend(validate_artifact(path))
    return {"valid": not errors, "artifact_count": len(paths), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = validate_batch(args.batch.resolve())
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
