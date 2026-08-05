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

    def _write_caller_module(self) -> None:
        (self.root / "checkout.py").write_text(
            "from widgets import compute_widget_price\n\n"
            "def total_for_cart(base_price, tax_rate):\n"
            '    """Wraps compute_widget_price for a cart line."""\n'
            "    return compute_widget_price(base_price, tax_rate)\n",
            encoding="utf-8",
        )

    def test_index_symbols_writes_calls_edge_for_direct_call(self) -> None:
        self._write_sample_module()
        self._write_caller_module()
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)

        edges = mc.list_graph_edges("calls", db_path=self.db_path)
        self.assertEqual(1, len(edges))
        edge = edges[0]
        # node_ids are slugified ("_" -> "-") by _graph_node_id/_slug.
        self.assertIn("total-for-cart", edge["src"])
        self.assertIn("compute-widget-price", edge["dst"])
        self.assertEqual(0, edge["curated"])

    def test_index_symbols_skips_ambiguous_call_names(self) -> None:
        # Two unrelated classes both define a "build" method - a call to
        # "build" cannot be safely wired to either one project-wide.
        (self.root / "a.py").write_text(
            "class A:\n    def build(self):\n        return 1\n",
            encoding="utf-8",
        )
        (self.root / "b.py").write_text(
            "class B:\n    def build(self):\n        return 2\n",
            encoding="utf-8",
        )
        (self.root / "caller.py").write_text(
            "def run(thing):\n    return thing.build()\n",
            encoding="utf-8",
        )
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        edges = mc.list_graph_edges("calls", db_path=self.db_path)
        self.assertEqual(0, len(edges))

    def test_index_symbols_calls_edges_are_idempotent(self) -> None:
        self._write_sample_module()
        self._write_caller_module()
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        edges = mc.list_graph_edges("calls", db_path=self.db_path)
        self.assertEqual(1, len(edges))

    def test_find_callers_returns_nothing_without_edges_but_finds_with_them(self) -> None:
        self._write_sample_module()
        self._write_caller_module()

        # Before index_symbols() runs, symbol_index/graph_edges are empty -
        # there is no data path from "compute_widget_price" to its callers.
        before = mc.find_callers("compute_widget_price", db_path=self.db_path)
        self.assertEqual([], before)

        mc.index_symbols(root_dir=self.root, db_path=self.db_path)

        after = mc.find_callers("compute_widget_price", db_path=self.db_path)
        self.assertEqual(1, len(after))
        self.assertEqual("total_for_cart", after[0]["symbol_name"])


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

    def test_index_docs_skip_dirs_empty_list_also_indexes_memory_dir(self) -> None:
        # memory/ is skipped by DEFAULT (bbb-lite's own memory_index
        # convention) - but that default must be overridable for another
        # project whose memory/ (if any) isn't memory_index's territory
        # (see decisions.md, 2026-08-05, Stage BUS-2).
        (self.root / "docs" / "plan.md").write_text(
            "# Rollout Plan\n\nWe are rolling out the new widget pricing engine.\n",
            encoding="utf-8",
        )
        (self.root / "memory" / "some-note.md").write_text(
            "---\ntitle: A memory note\n---\n\nThis belongs to memory_index, not doc_index.\n",
            encoding="utf-8",
        )

        count = mc.index_docs(root_dir=self.root, skip_dirs=[], db_path=self.db_path)

        self.assertEqual(2, count)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute("SELECT file_path FROM doc_index").fetchall()
        file_paths = {row["file_path"] for row in rows}
        self.assertTrue(any("plan.md" in p for p in file_paths))
        self.assertTrue(any("some-note.md" in p for p in file_paths))

    def test_index_docs_skip_dirs_custom_value_replaces_default(self) -> None:
        (self.root / "docs" / "plan.md").write_text(
            "# Rollout Plan\n\nWe are rolling out the new widget pricing engine.\n",
            encoding="utf-8",
        )
        (self.root / "memory" / "some-note.md").write_text(
            "---\ntitle: A memory note\n---\n\nThis belongs to memory_index, not doc_index.\n",
            encoding="utf-8",
        )

        # skip "docs" instead of the default "memory" - memory/ gets indexed,
        # docs/ does not.
        count = mc.index_docs(root_dir=self.root, skip_dirs=["docs"], db_path=self.db_path)

        self.assertEqual(1, count)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute("SELECT file_path FROM doc_index").fetchall()
        self.assertEqual(1, len(rows))
        self.assertIn("some-note.md", rows[0]["file_path"])

    def test_search_docs_finds_semantically_close_query(self) -> None:
        (self.root / "docs" / "plan.md").write_text(
            "# Rollout Plan\n\nWe are rolling out the new widget pricing engine.\n",
            encoding="utf-8",
        )
        mc.index_docs(root_dir=self.root, db_path=self.db_path)
        results = mc.search_docs("launching a pricing feature", db_path=self.db_path)
        self.assertTrue(results)
        self.assertIn("plan.md", results[0]["file_path"])

    def _write_sample_module_and_symbols(self) -> None:
        (self.root / "widgets.py").write_text(
            "def compute_widget_price(base_price, tax_rate):\n"
            '    """Return base_price plus tax."""\n'
            "    return round(base_price * (1 + tax_rate), 2)\n",
            encoding="utf-8",
        )
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)

    def test_index_docs_writes_references_edge_for_known_symbol(self) -> None:
        self._write_sample_module_and_symbols()
        (self.root / "docs" / "plan.md").write_text(
            "# Rollout Plan\n\n"
            "compute_widget_price is the single source of truth for pricing.\n",
            encoding="utf-8",
        )

        mc.index_docs(root_dir=self.root, db_path=self.db_path)

        edges = mc.list_graph_edges("references", db_path=self.db_path)
        self.assertEqual(1, len(edges))
        edge = edges[0]
        # node_ids are slugified ("_"/"." -> "-", "/" -> "-") by _graph_node_id/_slug.
        self.assertIn("plan-md", edge["src"])
        self.assertIn("compute-widget-price", edge["dst"])
        self.assertEqual(0, edge["curated"])

    def test_index_docs_no_reference_edge_for_unmentioned_symbol(self) -> None:
        self._write_sample_module_and_symbols()
        (self.root / "docs" / "plan.md").write_text(
            "# Rollout Plan\n\nThis text never names any indexed symbol.\n",
            encoding="utf-8",
        )

        mc.index_docs(root_dir=self.root, db_path=self.db_path)

        edges = mc.list_graph_edges("references", db_path=self.db_path)
        self.assertEqual(0, len(edges))

    def test_index_docs_references_edges_are_idempotent(self) -> None:
        self._write_sample_module_and_symbols()
        (self.root / "docs" / "plan.md").write_text(
            "# Rollout Plan\n\ncompute_widget_price handles pricing.\n",
            encoding="utf-8",
        )

        mc.index_docs(root_dir=self.root, db_path=self.db_path)
        mc.index_docs(root_dir=self.root, db_path=self.db_path)

        edges = mc.list_graph_edges("references", db_path=self.db_path)
        self.assertEqual(1, len(edges))


class ExportSyncEdgesTestCase(unittest.TestCase):
    """Stage BUS-2 prep: mc.export_sync_edges() reads our own calls/
    references edges back out in Haroon's edges.jsonl shape, remapped to
    their vocabulary (calls -> depends-on, references -> derived-from) -
    see decisions.md, 2026-08-05."""

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

    def test_calls_edge_exported_as_depends_on(self) -> None:
        (self.root / "widgets.py").write_text(
            "def compute_widget_price(base_price, tax_rate):\n"
            "    return round(base_price * (1 + tax_rate), 2)\n\n"
            "def total_for_cart(base_price, tax_rate):\n"
            "    return compute_widget_price(base_price, tax_rate)\n",
            encoding="utf-8",
        )
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)

        exported = mc.export_sync_edges(project=self.root.name, db_path=self.db_path)

        self.assertEqual(1, len(exported))
        edge = exported[0]
        self.assertEqual("depends-on", edge["type"])
        self.assertIn("total_for_cart", edge["src"])
        self.assertIn("compute_widget_price", edge["dst"])
        self.assertEqual(self.root.name, edge["project_id"])
        # forward-slashed regardless of the indexing machine's OS - Haroon's
        # side is Linux.
        self.assertNotIn("\\", edge["src"])

    def test_references_edge_exported_as_derived_from(self) -> None:
        (self.root / "widgets.py").write_text(
            "def compute_widget_price(base_price, tax_rate):\n"
            '    """Return base_price plus tax."""\n'
            "    return round(base_price * (1 + tax_rate), 2)\n",
            encoding="utf-8",
        )
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        (self.root / "docs" / "plan.md").write_text(
            "# Plan\n\ncompute_widget_price handles pricing.\n",
            encoding="utf-8",
        )
        mc.index_docs(root_dir=self.root, db_path=self.db_path)

        exported = mc.export_sync_edges(project=self.root.name, db_path=self.db_path)

        self.assertEqual(1, len(exported))
        edge = exported[0]
        self.assertEqual("derived-from", edge["type"])
        self.assertIn("plan.md", edge["src"])
        self.assertIn("compute_widget_price", edge["dst"])

    def test_output_fields_match_haroons_load_edges_reader(self) -> None:
        # lib/bbb_graph_edges.py's load_edges() reads exactly these keys
        # (src, dst, type, evidence, weight, project_id/project) off each
        # jsonl line - nothing more, nothing less should be required.
        (self.root / "widgets.py").write_text(
            "def a():\n    return b()\n\ndef b():\n    return 1\n",
            encoding="utf-8",
        )
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        exported = mc.export_sync_edges(project=self.root.name, db_path=self.db_path)
        self.assertEqual(1, len(exported))
        self.assertEqual(
            {"src", "dst", "type", "evidence", "weight", "project_id"},
            set(exported[0].keys()),
        )

    def test_no_edges_means_no_export(self) -> None:
        (self.root / "widgets.py").write_text("x = 1\n", encoding="utf-8")
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        self.assertEqual([], mc.export_sync_edges(db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
