"""Tests for reuse_receipts / log_reuse_decision() — records what a caller
did with a cyg_exists()/cyg_similar() hit (bbb-os-core parity item 1)."""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path

import mc


class LogReuseDecisionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _fetchone(self, query: str, params: tuple = ()) -> dict:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(query, params).fetchone()
        return dict(row)

    def _fetchall(self, query: str) -> list[dict]:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute(query).fetchall()
        return [dict(row) for row in rows]

    def test_records_match_when_reused_from_symbol_hit(self) -> None:
        match = {
            "source_table": "symbol_index",
            "symbol_name": "calculate_monthly_invoice",
            "file_path": "billing.py",
            "similarity": 0.91,
        }
        receipt_id = mc.log_reuse_decision(
            "sess-1", "calculate the monthly invoice", "reuse", match=match, db_path=self.db_path
        )
        self.assertIsInstance(receipt_id, int)
        rows = self._fetchall("SELECT * FROM reuse_receipts")
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("sess-1", row["session_id"])
        self.assertEqual("reuse", row["decision"])
        self.assertEqual("symbol_index", row["match_source_table"])
        self.assertEqual("billing.py::calculate_monthly_invoice", row["match_ref"])
        self.assertAlmostEqual(0.91, row["match_score"])

    def test_records_match_ref_for_doc_hit(self) -> None:
        match = {
            "source_table": "doc_index",
            "file_path": "docs/billing-plan.md",
            "title": "Billing Plan",
            "similarity": 0.75,
        }
        mc.log_reuse_decision(
            "sess-1", "billing plan", "reuse", match=match, db_path=self.db_path
        )
        row = self._fetchone("SELECT * FROM reuse_receipts")
        self.assertEqual("doc_index", row["match_source_table"])
        self.assertEqual("docs/billing-plan.md", row["match_ref"])

    def test_records_no_match(self) -> None:
        receipt_id = mc.log_reuse_decision(
            "sess-1", "something entirely new", "no_match", match=None, db_path=self.db_path
        )
        row = self._fetchone("SELECT * FROM reuse_receipts WHERE id = ?", (receipt_id,))
        self.assertEqual("no_match", row["decision"])
        self.assertIsNone(row["match_ref"])
        self.assertIsNone(row["match_score"])

    def test_build_new_decision_recorded(self) -> None:
        mc.log_reuse_decision(
            "sess-1", "a query", "build_new", match=None, db_path=self.db_path
        )
        row = self._fetchone("SELECT * FROM reuse_receipts")
        self.assertEqual("build_new", row["decision"])

    def test_rejects_invalid_decision(self) -> None:
        with self.assertRaises(ValueError):
            mc.log_reuse_decision(
                "sess-1", "a query", "maybe later", match=None, db_path=self.db_path
            )


if __name__ == "__main__":
    unittest.main()
