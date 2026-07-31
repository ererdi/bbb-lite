"""Tests for Stage AN (1/3): doc_index/symbol_index base schema + indexing.

Codex's 6th item - a code-graph layer that (per an earlier, incorrect
"Stage P code memory" claim) was believed to already exist but never did.
This is the real first implementation: symbols come from Python's own `ast`
module (no prior _extract_symbols logic existed anywhere in the codebase to
build on), scoped to .py files only for now - other languages are a
deliberate next step (see decisions.md, 2026-07-29).

index_docs() must skip memory/*.md entirely - that's memory_index's job
(Stage C onward); duplicating it here would just be two indexes of the same
files.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path

import mc


class IndexSymbolsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

        self.project_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.project_dir.cleanup)
        self.root = Path(self.project_dir.name)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _write_sample_module(self) -> None:
        (self.root / "widgets.py").write_text(
            '''"""Sample module for Stage AN tests."""\n\n'''
            "def compute_widget_price(base_price, tax_rate):\n"
            '    """Return base_price plus tax, rounded to 2 decimals."""\n'
            "    return round(base_price * (1 + tax_rate), 2)\n\n\n"
            "class WidgetFactory:\n"
            '    """Builds Widget instances from a catalog entry."""\n\n'
            "    def build(self, catalog_entry):\n"
            '        """Construct a Widget from catalog_entry."""\n'
            "        return catalog_entry\n",
            encoding="utf-8",
        )

    def test_index_symbols_extracts_function_and_method_with_docstrings(self) -> None:
        self._write_sample_module()
        count = mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        self.assertEqual(3, count)  # function + class + method

        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = {
                row["symbol_name"]: row
                for row in conn.execute("SELECT * FROM symbol_index").fetchall()
            }

        self.assertIn("compute_widget_price", rows)
        self.assertEqual("function", rows["compute_widget_price"]["symbol_type"])
        self.assertIn("plus tax", rows["compute_widget_price"]["docstring"])

        self.assertIn("WidgetFactory", rows)
        self.assertEqual("class", rows["WidgetFactory"]["symbol_type"])

        # Nested method is dotted-qualified, not just "build" - avoids
        # UNIQUE(file_path, symbol_name) collisions across classes.
        self.assertIn("WidgetFactory.build", rows)
        self.assertEqual("function", rows["WidgetFactory.build"]["symbol_type"])

    def test_index_symbols_sets_project_to_root_folder_name(self) -> None:
        self._write_sample_module()
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT project FROM symbol_index WHERE symbol_name = 'compute_widget_price'"
            ).fetchone()
        self.assertEqual(self.root.name, row["project"])

    def test_index_symbols_is_idempotent(self) -> None:
        self._write_sample_module()
        first = mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        second = mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        self.assertEqual(first, second)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM symbol_index").fetchone()[0]
        self.assertEqual(3, total)

    def test_index_symbols_skips_unparseable_file_without_crashing(self) -> None:
        (self.root / "broken.py").write_text("def not valid python(:\n", encoding="utf-8")
        self._write_sample_module()
        count = mc.index_symbols(root_dir=self.root, db_path=self.db_path)  # must not raise
        self.assertEqual(3, count)

    def test_search_symbols_finds_semantically_close_query(self) -> None:
        self._write_sample_module()
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        results = mc.search_symbols("calculating the total cost with tax", db_path=self.db_path)
        self.assertTrue(results)
        self.assertEqual("compute_widget_price", results[0]["symbol_name"])


class IndexDocsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

        self.project_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.project_dir.cleanup)
        self.root = Path(self.project_dir.name)
        (self.root / "docs").mkdir()
        (self.root / "memory").mkdir()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_index_docs_indexes_docs_dir_and_skips_memory_dir(self) -> None:
        (self.root / "docs" / "plan.md").write_text(
            "# Rollout Plan\n\nWe are rolling out the new widget pricing engine.\n",
            encoding="utf-8",
        )
        (self.root / "memory" / "some-note.md").write_text(
            "---\ntitle: A memory note\n---\n\nThis belongs to memory_index, not doc_index.\n",
            encoding="utf-8",
        )

        count = mc.index_docs(root_dir=self.root, db_path=self.db_path)

        self.assertEqual(1, count)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute("SELECT file_path, title FROM doc_index").fetchall()
        self.assertEqual(1, len(rows))
        self.assertIn("plan.md", rows[0]["file_path"])
        self.assertEqual("Rollout Plan", rows[0]["title"])

    def test_search_docs_finds_semantically_close_query(self) -> None:
        (self.root / "docs" / "plan.md").write_text(
            "# Rollout Plan\n\nWe are rolling out the new widget pricing engine.\n",
            encoding="utf-8",
        )
        mc.index_docs(root_dir=self.root, db_path=self.db_path)
        results = mc.search_docs("launching a pricing feature", db_path=self.db_path)
        self.assertTrue(results)
        self.assertIn("plan.md", results[0]["file_path"])


if __name__ == "__main__":
    unittest.main()
