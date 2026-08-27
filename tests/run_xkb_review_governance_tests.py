#!/usr/bin/env python3
"""stdlib-only smoke runner for XKB governance tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

suite = unittest.defaultTestLoader.loadTestsFromName(
    "test_xkb_review_governance", module=None
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
