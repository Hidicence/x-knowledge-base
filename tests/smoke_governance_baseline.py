#!/usr/bin/env python3
"""Small smoke fixture loader for xkb_governance_baseline."""
from __future__ import annotations
import importlib.util
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "xkb_governance_baseline.py"
spec = importlib.util.spec_from_file_location("baseline", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "fixture-candidates.md").write_text(
        "# Fixture\n\n"
        "## Candidate 1\n- **Topic:** topic-a\n- **Confidence:** high\n"
        "- **Status:** [ ] approve  [ ] skip\n\nclaim\n\n"
        "## Candidate 2\n- **Topic:** topic-a\n- **Confidence:** low\n"
        "- **Status:** [x] skip  [ ] approve\n\nweak\n",
        encoding="utf-8",
    )
    result = module.scan(root)
    assert (result["candidates"], result["pending"], result["skipped"]) == (2, 1, 1)
print("fixture smoke: PASS")
