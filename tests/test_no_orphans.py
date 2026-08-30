from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOCS = ("SKILL.md", "README.md", "README.zh.md")


def _referenced_anywhere(name: str, stem: str) -> bool:
    """Called by other code, or named in the documentation."""
    for directory in (SCRIPTS, ROOT / "tests", ROOT / "tools"):
        for path in directory.rglob("*"):
            if path.suffix not in (".py", ".sh") or path.name == name:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if name in text or re.search(rf"\b(?:import|from)\s+{re.escape(stem)}\b", text):
                return True
    for doc in DOCS:
        path = ROOT / doc
        if path.exists() and name in path.read_text(encoding="utf-8", errors="ignore"):
            return True
    return False


class NoOrphanScriptsTest(unittest.TestCase):
    """A script with no caller and no mention is indistinguishable from one
    that was forgotten.

    This project accumulated seventeen of them: a distributed queue disabled
    for six weeks, a prototype whose own docstring said it was waiting to be
    wired up, an adapter importing a base class that never existed, and a
    candidate pipeline that ran once and produced nothing. Each looked like
    part of the system while reading the directory.

    A tool that only ever runs by hand is legitimate — it just has to be
    written down. SKILL.md has a section for exactly that, and adding a line
    there is the cost of keeping a script nothing calls.
    """

    def test_every_script_is_scheduled_called_or_documented(self) -> None:
        orphans = [
            path.name
            for path in sorted(SCRIPTS.iterdir())
            if path.suffix in (".py", ".sh") and not _referenced_anywhere(path.name, path.stem)
        ]
        self.assertEqual(
            orphans, [],
            "nothing calls or documents these — wire them up, delete them, or "
            "list them under 'Tools you run by hand' in SKILL.md: " + ", ".join(orphans),
        )

    def test_scripts_declare_what_they_are_for(self) -> None:
        undocumented = []
        for path in sorted(SCRIPTS.glob("*.py")):
            try:
                if not ast.get_docstring(ast.parse(path.read_text(encoding="utf-8", errors="ignore"))):
                    undocumented.append(path.name)
            except SyntaxError:
                undocumented.append(f"{path.name} (unparseable)")
        self.assertEqual(undocumented, [], "no module docstring: " + ", ".join(undocumented))


if __name__ == "__main__":
    unittest.main()
