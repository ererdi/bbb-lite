"""Tests for Stage AN (3/3)'s CLI scripts under bin/ — Haroon's bin/cyg-*
tools finally have a real counterpart: cyg_exists.py, cyg_similar.py,
cyg_search.py, bbb_graph_ingest.py, bbb_graph_doc_upsert.py.

Same pattern as test_daily_check.py: import the bin/ module directly (bin/
is added to sys.path), patch its module-level `mc` calls, and drive main()
through sys.argv like a real invocation would. All five scripts are
fail-soft — a raised exception must print a readable message, never a
traceback, and never propagate.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "bin"))

import bbb_graph_doc_upsert
import bbb_graph_ingest
import cyg_exists
import cyg_search
import cyg_similar


def _run(module, argv, main_name: str = "main") -> str:
    buffer = io.StringIO()
    with mock.patch.object(sys, "argv", ["prog"] + argv), redirect_stdout(buffer):
        getattr(module, main_name)()
    return buffer.getvalue()


class CygExistsCliTestCase(unittest.TestCase):
    def test_prints_match_when_exists(self) -> None:
        with mock.patch("cyg_exists.mc.init_db"), mock.patch(
            "cyg_exists.mc.cyg_exists",
            return_value={
                "exists": True,
                "score": 0.81,
                "match": {
                    "source_table": "symbol_index",
                    "symbol_name": "calculate_monthly_invoice",
                    "file_path": "billing.py",
                },
            },
        ) as cyg_exists_mock:
            output = _run(cyg_exists, ["calculate", "the", "monthly", "invoice"])
        cyg_exists_mock.assert_called_once_with(
            "calculate the monthly invoice", project=None, threshold=0.6
        )
        self.assertIn("exists: True", output)
        self.assertIn("calculate_monthly_invoice", output)

    def test_reports_error_readably_instead_of_raising(self) -> None:
        with mock.patch("cyg_exists.mc.init_db"), mock.patch(
            "cyg_exists.mc.cyg_exists", side_effect=RuntimeError("no embedding model")
        ):
            output = _run(cyg_exists, ["anything"])  # must not raise
        self.assertIn("[cyg-exists] error: no embedding model", output)

    def test_nothing_indexed_message_when_score_is_none(self) -> None:
        with mock.patch("cyg_exists.mc.init_db"), mock.patch(
            "cyg_exists.mc.cyg_exists",
            return_value={"exists": False, "score": None, "match": None},
        ):
            output = _run(cyg_exists, ["anything"])
        self.assertIn("nothing indexed yet", output)


class CygSimilarCliTestCase(unittest.TestCase):
    def test_lists_merged_hits(self) -> None:
        with mock.patch("cyg_similar.mc.init_db"), mock.patch(
            "cyg_similar.mc.cyg_similar",
            return_value=[
                {
                    "source_table": "symbol_index",
                    "symbol_name": "calculate_monthly_invoice",
                    "file_path": "billing.py",
                    "similarity": 0.81,
                },
                {
                    "source_table": "doc_index",
                    "title": "Billing Plan",
                    "file_path": "docs/billing-plan.md",
                    "similarity": 0.7,
                },
            ],
        ):
            output = _run(cyg_similar, ["monthly", "invoice"])
        self.assertIn("calculate_monthly_invoice", output)
        self.assertIn("Billing Plan", output)
        self.assertIn("symbol_index", output)
        self.assertIn("doc_index", output)


class CygSearchCliTestCase(unittest.TestCase):
    def test_prints_all_three_sections(self) -> None:
        with mock.patch("cyg_search.mc.init_db"), \
             mock.patch("cyg_search.mc.search_symbols", return_value=[
                 {"symbol_name": "calculate_monthly_invoice", "file_path": "billing.py", "similarity": 0.8}
             ]), \
             mock.patch("cyg_search.mc.search_docs", return_value=[
                 {"title": "Billing Plan", "file_path": "docs/billing-plan.md", "similarity": 0.7}
             ]), \
             mock.patch("cyg_search.mc.search_memory", return_value=[
                 {"title": "A lesson", "source_file": "lesson.md", "confidence": 0.9}
             ]):
            output = _run(cyg_search, ["invoice"])
        self.assertIn("--- symbols ---", output)
        self.assertIn("--- docs ---", output)
        self.assertIn("--- memory ---", output)
        self.assertIn("calculate_monthly_invoice", output)
        self.assertIn("Billing Plan", output)
        self.assertIn("A lesson", output)

    def test_source_error_is_readable_and_others_still_run(self) -> None:
        with mock.patch("cyg_search.mc.init_db"), \
             mock.patch("cyg_search.mc.search_symbols", side_effect=RuntimeError("boom")), \
             mock.patch("cyg_search.mc.search_docs", return_value=[]), \
             mock.patch("cyg_search.mc.search_memory", return_value=[]):
            output = _run(cyg_search, ["invoice"])  # must not raise
        self.assertIn("[cyg-search] symbol search error: boom", output)
        self.assertIn("--- docs ---", output)
        self.assertIn("--- memory ---", output)


class BbbGraphIngestCliTestCase(unittest.TestCase):
    def test_sums_counts_across_all_three_indexers(self) -> None:
        with mock.patch("bbb_graph_ingest.mc.init_db"), \
             mock.patch("bbb_graph_ingest.mc.index_symbols", return_value=3), \
             mock.patch("bbb_graph_ingest.mc.index_docs", return_value=2), \
             mock.patch("bbb_graph_ingest.mc.index_memory_files", return_value=5):
            output = _run(bbb_graph_ingest, [])
        self.assertIn("indexed 3 symbol item(s)", output)
        self.assertIn("indexed 2 doc item(s)", output)
        self.assertIn("indexed 5 memory item(s)", output)
        self.assertIn("total: 10 item(s) indexed", output)

    def test_one_indexer_failing_does_not_stop_the_others(self) -> None:
        with mock.patch("bbb_graph_ingest.mc.init_db"), \
             mock.patch("bbb_graph_ingest.mc.index_symbols", side_effect=RuntimeError("bad ast")), \
             mock.patch("bbb_graph_ingest.mc.index_docs", return_value=2), \
             mock.patch("bbb_graph_ingest.mc.index_memory_files", return_value=5):
            output = _run(bbb_graph_ingest, [])  # must not raise
        self.assertIn("[bbb-graph-ingest] symbol indexing error: bad ast", output)
        self.assertIn("indexed 2 doc item(s)", output)
        self.assertIn("total: 7 item(s) indexed", output)


class BbbGraphDocUpsertCliTestCase(unittest.TestCase):
    def test_upserts_py_file_via_upsert_symbol_file(self) -> None:
        with mock.patch("bbb_graph_doc_upsert.Path.exists", return_value=True), \
             mock.patch("bbb_graph_doc_upsert.mc.init_db"), \
             mock.patch("bbb_graph_doc_upsert.mc.upsert_symbol_file", return_value=2) as upsert_mock:
            output = _run(bbb_graph_doc_upsert, ["billing.py"])
        upsert_mock.assert_called_once()
        self.assertIn("indexed 2 symbol(s)", output)

    def test_upserts_md_file_via_upsert_doc_file(self) -> None:
        with mock.patch("bbb_graph_doc_upsert.Path.exists", return_value=True), \
             mock.patch("bbb_graph_doc_upsert.mc.init_db"), \
             mock.patch("bbb_graph_doc_upsert.mc.upsert_doc_file") as upsert_mock:
            output = _run(bbb_graph_doc_upsert, ["docs/plan.md"])
        upsert_mock.assert_called_once()
        self.assertIn("indexed", output)
        self.assertIn("doc_index", output)

    def test_missing_file_reports_error_without_crashing(self) -> None:
        output = _run(bbb_graph_doc_upsert, ["does-not-exist.py"])
        self.assertIn("error: file not found", output)

    def test_unsupported_extension_is_reported(self) -> None:
        with mock.patch("bbb_graph_doc_upsert.Path.exists", return_value=True), \
             mock.patch("bbb_graph_doc_upsert.mc.init_db"):
            output = _run(bbb_graph_doc_upsert, ["notes.txt"])
        self.assertIn("unsupported extension", output)


if __name__ == "__main__":
    unittest.main()
