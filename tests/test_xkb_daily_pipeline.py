import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import xkb_daily_pipeline


class DailyPipelineTests(unittest.TestCase):
    def test_dry_run_includes_bounded_governance_cli(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(xkb_daily_pipeline.main(["--dry-run", "--governance-limit", "3"]), 0)
        self.assertIn("xkb_review.py --governance --dry-run --limit 3", output.getvalue())

    def test_lock_prevents_reentry_and_releases(self):
        with patch.object(xkb_daily_pipeline, "GOVERNANCE_LOCK", Path("/tmp/xkb-test-governance.lock")):
            xkb_daily_pipeline.release_governance_lock()
            self.assertTrue(xkb_daily_pipeline.acquire_governance_lock())
            try:
                self.assertFalse(xkb_daily_pipeline.acquire_governance_lock())
            finally:
                xkb_daily_pipeline.release_governance_lock()
            self.assertTrue(xkb_daily_pipeline.acquire_governance_lock())
            xkb_daily_pipeline.release_governance_lock()


if __name__ == "__main__":
    unittest.main()
