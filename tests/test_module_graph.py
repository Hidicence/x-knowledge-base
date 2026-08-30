from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TOOLS = ROOT / "tools"

# Installed separately; absence is a deployment question, not a broken graph.
THIRD_PARTY = {"requests", "yaml", "numpy", "bs4", "dotenv", "fitz", "httpx", "psycopg2"}


def _local_imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


class ModuleGraphTests(unittest.TestCase):
    """Every module this code imports must exist.

    _session_dedup was moved into archive/ on 2026-05-04 while recall_router
    still imported it. The import failed from that day on, a try/except
    substituted a no-op, and the filter that stops the same knowledge being
    shown twice in one conversation was off for four months with no error
    anywhere. xkb_adapter_http imported a base class that had never existed
    in this repository at all, and a test listed it among the "active
    entrypoints" it scanned — passing, because it read the file as text
    instead of importing it.

    Both were found by reading, not by anything failing. This is the check
    that would have caught them the same day.
    """

    def test_every_import_resolves(self) -> None:
        available = (
            {path.stem for path in SCRIPTS.glob("*.py")}
            | {path.stem for path in TOOLS.glob("*.py")}
            | set(sys.stdlib_module_names)
            | THIRD_PARTY
            | {"tools", "__future__"}
        )
        missing: dict[str, list[str]] = {}
        for path in sorted(SCRIPTS.glob("*.py")):
            for name in sorted(_local_imports(path)):
                if name not in available:
                    missing.setdefault(name, []).append(path.name)
        self.assertEqual(
            missing, {},
            "imports with no module behind them: "
            + "; ".join(f"{name} <- {', '.join(users)}" for name, users in sorted(missing.items())),
        )

    def test_scripts_directory_holds_no_archive(self) -> None:
        """Git is the archive. A copy in the tree is dead code that greps,
        searches and every checkout still carry — and, once, an import
        target that quietly disappeared."""
        self.assertFalse((ROOT / "archive").exists(), "archive/ is back; delete it and rely on git history")


if __name__ == "__main__":
    unittest.main()
