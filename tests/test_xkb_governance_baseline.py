from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from xkb_governance_baseline import scan

def test_scan_empty(tmp_path):
    result = scan(tmp_path)
    assert result["candidates"] == 0
    assert result["pending"] == 0

def test_scan_preserves_status_counts(tmp_path):
    (tmp_path / "fixture-candidates.md").write_text("""# Fixture\n\n## Candidate 1\n- **Topic:** topic-a\n- **Confidence:** high\n- **Source date:** 2026-08-26\n- **Status:** [ ] approve  [ ] skip\n\nA reusable claim with evidence https://example.test/a\n\n## Candidate 2\n- **Topic:** topic-a\n- **Confidence:** low\n- **Source date:** 2026-08-26\n- **Status:** [x] skip  [ ] approve\n\nA weak claim.\n""", encoding="utf-8")
    result = scan(tmp_path)
    assert result["candidates"] == 2
    assert result["pending"] == 1
    assert result["skipped"] == 1
    assert result["topics"] == {"topic-a": 2}
