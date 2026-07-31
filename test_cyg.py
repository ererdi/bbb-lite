"""Tests for Stage AN (2/3): cyg_exists()/cyg_similar().

The practical, checkable form of Haroon's "reuse before build" Golden Rule -
checks real indexed symbols/docs (Stage AN 1/3's symbol_index/doc_index)
instead of just trusting the model to remember to search first.

Also covers hooks/pretooluse_log.py's new informational (never-blocking)
reuse nudge, which now calls cyg_exists() instead of nothing (no such
"Stage T search_code" logic ever existed - see decisions.md, 2026-07-29).
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))

import mc
import pretooluse_log


class CygSimilarAndExistsTestCase(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _index_sample_symbol_and_doc(self) -> None:
        (self.root / "billing.py").write_text(
            "def calculate_monthly_invoice(subscriptions, discount_pct):\n"
            '    """Sum active subscription prices and apply a percentage discount."""\n'
            "    return sum(s.price for s in subscriptions) * (1 - discount_pct / 100)\n",
            encoding="utf-8",
        )
        (self.root / "docs" / "billing-plan.md").write_text(
            "# Billing Plan\n\nHow monthly invoices get calculated and discounted.\n",
            encoding="utf-8",
        )
        mc.index_symbols(root_dir=self.root, db_path=self.db_path)
        mc.index_docs(root_dir=self.root, db_path=self.db_path)

    def test_cyg_exists_true_for_a_close_query(self) -> None:
        self._index_sample_symbol_and_doc()
        result = mc.cyg_exists(
            "calculate the monthly invoice with a discount", db_path=self.db_path
        )
        self.assertTrue(result["exists"])
        self.assertIsNotNone(result["match"])
        self.assertGreaterEqual(result["score"], 0.6)

    def test_cyg_exists_false_for_an_unrelated_query(self) -> None:
        self._index_sample_symbol_and_doc()
        result = mc.cyg_exists(
            "the migratory patterns of arctic terns in winter", db_path=self.db_path
        )
        self.assertFalse(result["exists"])
        self.assertIsNone(result["match"])
        self.assertIsNotNone(result["score"])  # best score found, just below threshold

    def test_cyg_exists_score_none_when_nothing_indexed(self) -> None:
        result = mc.cyg_exists("anything at all", db_path=self.db_path)
        self.assertFalse(result["exists"])
        self.assertIsNone(result["match"])
        self.assertIsNone(result["score"])

    def test_cyg_similar_merges_and_ranks_both_tables(self) -> None:
        self._index_sample_symbol_and_doc()
        results = mc.cyg_similar("monthly invoice discount calculation", db_path=self.db_path)
        source_tables = {hit["source_table"] for hit in results}
        self.assertEqual({"symbol_index", "doc_index"}, source_tables)
        # sorted descending by similarity
        similarities = [hit["similarity"] for hit in results]
        self.assertEqual(sorted(similarities, reverse=True), similarities)

    def test_cyg_similar_respects_limit(self) -> None:
        self._index_sample_symbol_and_doc()
        results = mc.cyg_similar("monthly invoice discount calculation", limit=1, db_path=self.db_path)
        self.assertEqual(1, len(results))


class PreToolUseReuseNudgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_reuse_nudge_is_informational_only_never_blocks(self) -> None:
        with mock.patch("mc.cyg_exists", return_value={"exists": True, "match": {
            "source_table": "symbol_index", "symbol_name": "calculate_monthly_invoice",
            "file_path": "billing.py",
        }, "score": 0.9}), mock.patch("pretooluse_log.mc.init_db"):
            result = pretooluse_log.check(
                {"file_path": "new_billing.py", "content": "def foo():\n    pass\n"},
                session_id=None,
            )
        self.assertIsNone(result)  # never blocks

    def test_reuse_nudge_prints_info_when_similar_code_exists(self) -> None:
        with mock.patch(
            "mc.cyg_exists",
            return_value={
                "exists": True,
                "match": {
                    "source_table": "symbol_index",
                    "symbol_name": "calculate_monthly_invoice",
                    "file_path": "billing.py",
                },
                "score": 0.87,
            },
        ), mock.patch("pretooluse_log.mc.init_db"):
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                pretooluse_log.check(
                    {"file_path": "new_billing.py", "content": "def foo():\n    pass\n"},
                    session_id=None,
                )
        output = buf.getvalue()
        self.assertIn("similar code may already exist", output)
        self.assertIn("calculate_monthly_invoice", output)

    def test_reuse_nudge_silent_when_cyg_exists_raises(self) -> None:
        with mock.patch("mc.cyg_exists", side_effect=RuntimeError("no embedding model")), \
             mock.patch("pretooluse_log.mc.init_db"):
            result = pretooluse_log.check(
                {"file_path": "new_billing.py", "content": "def foo():\n    pass\n"},
                session_id=None,
            )  # must not raise
        self.assertIsNone(result)

    def test_reuse_nudge_skipped_for_non_python_files(self) -> None:
        with mock.patch("mc.cyg_exists") as cyg_exists:
            pretooluse_log.check(
                {"file_path": "notes.txt", "content": "def foo():\n    pass\n"},
                session_id=None,
            )
        cyg_exists.assert_not_called()


if __name__ == "__main__":
    unittest.main()
