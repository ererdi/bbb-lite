"""Tests for Stage AP: task completion tightened per Codex's Task Completion
item (on top of Stage AE's evidence requirement).

New in this stage:
- parent_task_id column + subtask check: complete_task() refuses to close a
  task while any of its subtasks are still open.
- require_test_evidence (default True): completion evidence must have a
  "kind" containing "test" (case-insensitive), unless the caller explicitly
  opts out (e.g. bin/dispatch.py's non-test dispatch receipt).
- status gating: only 'in_progress' or 'ready_for_review' tasks can be
  completed - a fresh 'todo' task must progress first. request_review()
  moves 'in_progress' -> 'ready_for_review' as an intermediate step.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import mc


def _test_evidence_id(db_path: Path) -> int:
    return mc.record_evidence("test", "fixture", "passed", db_path=db_path)


class TaskCompletionGatingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_refuses_completion_with_non_test_evidence_by_default(self) -> None:
        task_id = mc.add_task("ship feature", status="in_progress", db_path=self.db_path)
        evidence_id = mc.record_evidence(
            "manual-check", "eyeballed it", "passed", db_path=self.db_path
        )
        with self.assertRaises(ValueError) as ctx:
            mc.complete_task(
                task_id,
                session_id="s1",
                evidence_id=evidence_id,
                completion_summary="shipped",
                db_path=self.db_path,
            )
        self.assertIn("test execution", str(ctx.exception))
        self.assertEqual("in_progress", mc.list_tasks(db_path=self.db_path)[0]["status"])

    def test_require_test_evidence_false_allows_non_test_evidence(self) -> None:
        task_id = mc.add_task("ship feature", status="in_progress", db_path=self.db_path)
        evidence_id = mc.record_evidence(
            "dispatch", "agentic dispatch receipt", "passed", db_path=self.db_path
        )
        result = mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="shipped",
            require_test_evidence=False,
            db_path=self.db_path,
        )
        self.assertEqual("done", result["status"])

    def test_refuses_completion_with_open_subtask(self) -> None:
        parent_id = mc.add_task("epic: ship feature", status="in_progress", db_path=self.db_path)
        mc.add_task(
            "subtask: write tests", status="in_progress",
            parent_task_id=parent_id, db_path=self.db_path,
        )
        evidence_id = _test_evidence_id(self.db_path)
        with self.assertRaises(ValueError) as ctx:
            mc.complete_task(
                parent_id,
                session_id="s1",
                evidence_id=evidence_id,
                completion_summary="done",
                db_path=self.db_path,
            )
        self.assertIn("1 subtask(s) still open", str(ctx.exception))

    def test_completable_once_all_subtasks_are_done(self) -> None:
        parent_id = mc.add_task("epic: ship feature", status="in_progress", db_path=self.db_path)
        subtask_id = mc.add_task(
            "subtask: write tests", status="in_progress",
            parent_task_id=parent_id, db_path=self.db_path,
        )
        evidence_id = _test_evidence_id(self.db_path)
        mc.complete_task(
            subtask_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="tests written",
            db_path=self.db_path,
        )
        result = mc.complete_task(
            parent_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="feature shipped",
            db_path=self.db_path,
        )
        self.assertEqual("done", result["status"])

    def test_pending_task_cannot_be_completed_directly(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)  # default status="todo"
        evidence_id = _test_evidence_id(self.db_path)
        with self.assertRaises(ValueError) as ctx:
            mc.complete_task(
                task_id,
                session_id="s1",
                evidence_id=evidence_id,
                completion_summary="tests written",
                db_path=self.db_path,
            )
        self.assertIn("is not ready", str(ctx.exception))
        self.assertEqual("todo", mc.list_tasks(db_path=self.db_path)[0]["status"])

    def test_blocked_task_cannot_be_completed_directly(self) -> None:
        task_id = mc.add_task("write tests", status="blocked", db_path=self.db_path)
        evidence_id = _test_evidence_id(self.db_path)
        with self.assertRaises(ValueError):
            mc.complete_task(
                task_id,
                session_id="s1",
                evidence_id=evidence_id,
                completion_summary="tests written",
                db_path=self.db_path,
            )

    def test_request_review_moves_in_progress_to_ready_for_review(self) -> None:
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        mc.request_review(task_id, "s1", db_path=self.db_path)
        self.assertEqual(
            "ready_for_review", mc.list_tasks(db_path=self.db_path)[0]["status"]
        )

    def test_complete_task_accepts_ready_for_review(self) -> None:
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        mc.request_review(task_id, "s1", db_path=self.db_path)
        evidence_id = _test_evidence_id(self.db_path)
        result = mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="reviewed and done",
            db_path=self.db_path,
        )
        self.assertEqual("done", result["status"])

    def test_request_review_refuses_from_non_in_progress_status(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)  # "todo"
        with self.assertRaises(ValueError):
            mc.request_review(task_id, "s1", db_path=self.db_path)

    def test_restart_scenario_open_task_still_shows_up_after_new_session_start(self) -> None:
        """Mark a task in_progress, then simulate a restart (a fresh
        session_start-equivalent read via list_open_tasks) and prove the
        open task is still there, untouched by the "session" boundary."""

        task_id = mc.add_task(
            "write tests", status="in_progress", session_id="s-before-restart",
            db_path=self.db_path,
        )
        # "restart": a brand new session reads the shared task list, exactly
        # as hooks/session_start.py's list_open_tasks() call would.
        mc.ensure_session("s-after-restart", db_path=self.db_path)
        open_tasks = mc.list_open_tasks(db_path=self.db_path)
        self.assertEqual([task_id], [t["id"] for t in open_tasks])
        self.assertEqual("in_progress", open_tasks[0]["status"])


if __name__ == "__main__":
    unittest.main()
