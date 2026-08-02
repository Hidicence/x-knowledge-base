import sys, unittest
from pathlib import Path
S = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(S))
import xkb_synthesize_topic as syn

class T(unittest.TestCase):
    def test_limit_is_enforced_not_requested(self):
        """117 in, 86 out proved the model ignores a prompt-level cap."""
        md = "\n".join(f"- conclusion {i}" for i in range(30))
        self.assertEqual(len(syn.take_bullets(md, 8)), 8)

    def test_continuation_lines_stay_with_their_bullet(self):
        kept = syn.take_bullets("- first line\n  wrapped tail\n- second", 8)
        self.assertEqual(len(kept), 2)
        self.assertIn("wrapped tail", kept[0])

    def test_links_are_separated_from_content(self):
        page = ("## Head\n"
                "- [a title](https://x.com/1) — xkb\n"
                "- " + "a substantive note that is definitely longer than sixty characters here\n")
        prose, bullets, links = syn.split_page(page)
        self.assertEqual(len(links), 1)
        self.assertEqual(len(bullets), 1)
        self.assertIn("Head", prose)
if __name__ == "__main__":
    unittest.main()
