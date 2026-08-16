"""Tests for Stage AL: compact_snapshot() extension + clear_recovery_verified()
(Codex's 4th item).

All new compact_snapshot() fields are read from tables that already existed
(dispatch_runs, evidence, answer_receipts, documents, graph_nodes/edges) -
no new table. Each is individually fail-soft: missing data becomes
0/None/False, never a raised error. compare_snapshots() only reads the
original four keys (tasks/decisions/checklist_items/role_declared), so these
additions cannot trigger a false degraded-recovery finding.

clear_recovery_verified() is a similar compact-style summary for /clear:
role, latest checkpoint, open tasks, recent decisions, and whether Stage
AF/AH's inheritance mechanism actually left a role row behind (verified).
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))

import mc
import session_start


_BOUND_FUNCS = (
    "ensure_session",
    "run_maintenance_checks",
    "record_lifecycle_run",
    "resolve_project",
    "find_session_by_client_uuid",
    "set_role",
    "get_role",
    "role_exists",
    "get_most_recent_role",
    "can_write_code",
    "can_write_data",
    "list_open_tasks",
    "latest_checkpoint",
    "semantic_search",
    "list_sessions",
    "recent_decisions",
    "recovery_package_size",
    "clear_recovery_verified",
)


def _bind_to_test_db(db_path: Path) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    for name in _BOUND_FUNCS:
        bound = functools.partial(getattr(mc, name), db_path=db_path)
        stack.enter_context(mock.patch(f"mc.{name}", bound))
    stack.enter_context(mock.patch("session_start.mc.init_db"))
    return stack


def _run_session_start(session_id: str, source: str = "clear") -> str:
    payload = json.dumps({"session_id": session_id, "source": source})
    buffer = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(buffer):
        session_start.main()
    return buffer.getvalue()


class CompactSnapshotExtensionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.session_id = "s-stage-al"
        mc.ensure_session(self.session_id, db_path=self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_new_fields_populated_with_real_data(self) -> None:
        mc.add_task("t1", session_id=self.session_id, db_path=self.db_path)
        mc.write_checkpoint(self.session_id, current_task="t1", db_path=self.db_path)
        mc.record_evidence("test", "fixture", "passed", db_path=self.db_path)
        mc.record_evidence("test", "fixture2", "passed", db_path=self.db_path)
        mc.propose_node("concept", "graph works", db_path=self.db_path)

        snapshot = mc.compact_snapshot(self.session_id, db_path=self.db_path)

        self.assertEqual(snapshot["tasks"], snapshot["open_task_ids"])
        self.assertIsNotNone(snapshot["latest_checkpoint_id"])
        self.assertEqual(2, snapshot["evidence_count"])
        self.assertEqual(0, snapshot["receipt_count"])
        # No session_id -> project_id mapping exists in the schema, so this is
        # "unknown" (None), not "no" (False) - see decisions.md, 2026-08-01.
        self.assertIsNone(snapshot["has_active_document"])
        self.assertEqual(1, snapshot["graph_node_count"])
        self.assertEqual(0, snapshot["graph_edge_count"])
        self.assertIsNone(snapshot["latest_dispatch_run_id"])

    def test_new_fields_fail_soft_to_empty_defaults(self) -> None:
        snapshot = mc.compact_snapshot(self.session_id, db_path=self.db_path)

        self.assertEqual([], snapshot["open_task_ids"])
        self.assertIsNone(snapshot["latest_checkpoint_id"])
        self.assertIsNone(snapshot["latest_dispatch_run_id"])
        self.assertEqual(0, snapshot["evidence_count"])
        self.assertEqual(0, snapshot["receipt_count"])
        self.assertIsNone(snapshot["has_active_document"])
        self.assertEqual(0, snapshot["graph_node_count"])
        self.assertEqual(0, snapshot["graph_edge_count"])

    def test_has_active_document_resolves_a_real_value_once_a_session_project_link_exists(
        self,
    ) -> None:
        """If projects ever gains a session_id column, this starts computing a
        real True/False instead of None - with no change to mc.py needed."""

        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute("ALTER TABLE projects ADD COLUMN session_id TEXT")
            cur = conn.execute(
                "INSERT INTO projects (name, status, created_at, session_id) "
                "VALUES (?, ?, ?, ?)",
                ("demo-project", "active", mc._now(), self.session_id),
            )
            project_id = cur.lastrowid
            conn.commit()

        no_doc_yet = mc.compact_snapshot(self.session_id, db_path=self.db_path)
        self.assertFalse(no_doc_yet["has_active_document"])

        mc.add_document(project_id, "northstar", "North Star", "content", db_path=self.db_path)
        with_doc = mc.compact_snapshot(self.session_id, db_path=self.db_path)
        self.assertTrue(with_doc["has_active_document"])

    def test_original_fields_and_compare_snapshots_unaffected(self) -> None:
        mc.add_task("t1", db_path=self.db_path)
        mc.set_role(self.session_id, "builder", db_path=self.db_path)
        mc.save_snapshot(self.session_id, "pre", db_path=self.db_path)
        mc.save_snapshot(self.session_id, "post", db_path=self.db_path)
        findings = mc.compare_snapshots(self.session_id, db_path=self.db_path)
        self.assertEqual([], findings)


class RecentDecisionIdsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.md_path = self.db_path.parent / f"{self.db_path.stem}-decisions.md"

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)
        self.md_path.unlink(missing_ok=True)

    def test_returns_ids_newest_first(self) -> None:
        id1 = mc.add_decision("first", db_path=self.db_path, md_path=self.md_path)
        id2 = mc.add_decision("second", db_path=self.db_path, md_path=self.md_path)
        id3 = mc.add_decision("third", db_path=self.db_path, md_path=self.md_path)

        ids = mc.recent_decision_ids(limit=2, db_path=self.db_path)

        self.assertEqual([id3, id2], ids)
        self.assertNotIn(id1, ids)

    def test_empty_when_no_decisions(self) -> None:
        self.assertEqual([], mc.recent_decision_ids(db_path=self.db_path))

    def test_recent_decisions_text_function_is_untouched(self) -> None:
        mc.add_decision("only decision", db_path=self.db_path, md_path=self.md_path)
        self.assertEqual(
            ["only decision"], mc.recent_decisions(limit=2, db_path=self.db_path)
        )


class ClearRecoveryVerifiedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_verified_true_when_role_row_exists(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.set_role("s1", "builder", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="doing work", db_path=self.db_path)
        mc.add_task("t1", session_id="s1", db_path=self.db_path)

        result = mc.clear_recovery_verified("s1", db_path=self.db_path)

        self.assertEqual("builder", result["role"])
        self.assertTrue(result["verified"])
        self.assertIsNotNone(result["latest_checkpoint"])
        self.assertEqual(1, len(result["open_task_ids"]))

    def test_verified_false_when_no_role_row(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        result = mc.clear_recovery_verified("s1", db_path=self.db_path)
        self.assertEqual("brozzer", result["role"])
        self.assertFalse(result["verified"])
        self.assertIsNone(result["latest_checkpoint"])

    def test_session_start_prints_clear_recovery_verified_line(self) -> None:
        old_session, new_session = "s-before-clear", "s-after-clear"
        mc.ensure_session(old_session, db_path=self.db_path)
        mc.set_role(old_session, "builder", db_path=self.db_path)
        mc.add_task("t1", db_path=self.db_path)

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(new_session, source="clear")

        self.assertIn(
            "[bbb-lite] clear recovery verified: role=builder, 1 open tasks, checkpoint=yok",
            output,
        )

    def test_no_clear_recovery_line_for_non_clear_source(self) -> None:
        with _bind_to_test_db(self.db_path):
            output = _run_session_start("s-startup", source="startup")
        self.assertNotIn("clear recovery verified", output)


if __name__ == "__main__":
    unittest.main()
