"""Tests for accept_task_completion()/task_receipts — the reported != accepted
split (bbb-os-core parity items 2/4/6), layered on top of complete_task()
without changing its existing behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import mc


def _test_evidence_id(db_path: Path) -> int:
    return mc.record_evidence("test", "fixture", "passed", db_path=db_path)


def _completed_task(db_path: Path, *, role: str = "builder") -> tuple[int, int]:
    """Create + complete a task, return (task_id, task_completion_id)."""

    mc.set_role("reporter", role, db_path=db_path)
    task_id = mc.add_task("ship feature", status="in_progress", db_path=db_path)
    evidence_id = _test_evidence_id(db_path)
    result = mc.complete_task(
        task_id,
        session_id="reporter",
        evidence_id=evidence_id,
        completion_summary="shipped",
        db_path=db_path,
    )
    return task_id, result["task_completion_id"]


class AcceptTaskCompletionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_accept_succeeds_for_architect_role(self) -> None:
        task_id, completion_id = _completed_task(self.db_path)
        mc.set_role("approver", "architect", db_path=self.db_path)
        result = mc.accept_task_completion(
            completion_id, session_id="approver", db_path=self.db_path
        )
        self.assertTrue(result["ok"])
        self.assertEqual(task_id, result["task_id"])
        self.assertEqual("architect", result["accepted_by_role"])
        self.assertIsInstance(result["state_hash"], str)
        self.assertTrue(result["state_hash"])

    def test_accept_succeeds_for_builder_role_accepting_own_completion(self) -> None:
        # bbb-lite is single-user: accepting session need not differ from
        # the reporting session (unlike bbb-os-core's multi-person model).
        task_id, completion_id = _completed_task(self.db_path, role="builder")
        result = mc.accept_task_completion(
            completion_id, session_id="reporter", db_path=self.db_path
        )
        self.assertTrue(result["ok"])

    def test_accept_refused_for_brozzer_role(self) -> None:
        _task_id, completion_id = _completed_task(self.db_path)
        mc.set_role("viewer", "brozzer", db_path=self.db_path)
        with self.assertRaises(ValueError) as ctx:
            mc.accept_task_completion(completion_id, session_id="viewer", db_path=self.db_path)
        self.assertIn("cannot accept work", str(ctx.exception))

    def test_accept_refused_for_advisor_role(self) -> None:
        _task_id, completion_id = _completed_task(self.db_path)
        mc.set_role("viewer", "advisor", db_path=self.db_path)
        with self.assertRaises(ValueError):
            mc.accept_task_completion(completion_id, session_id="viewer", db_path=self.db_path)

    def test_accept_refused_when_evidence_status_changed_to_fail_after_completion(self) -> None:
        task_id = mc.add_task("ship feature", status="in_progress", db_path=self.db_path)
        evidence_id = _test_evidence_id(self.db_path)
        mc.set_role("reporter", "builder", db_path=self.db_path)
        result = mc.complete_task(
            task_id,
            session_id="reporter",
            evidence_id=evidence_id,
            completion_summary="shipped",
            db_path=self.db_path,
        )
        # evidence status changes after the fact (e.g. a later re-run failed)
        import contextlib
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute("UPDATE evidence SET status = 'fail' WHERE id = ?", (evidence_id,))
            conn.commit()
        mc.set_role("approver", "architect", db_path=self.db_path)
        with self.assertRaises(ValueError) as ctx:
            mc.accept_task_completion(
                result["task_completion_id"], session_id="approver", db_path=self.db_path
            )
        self.assertIn("failure", str(ctx.exception))

    def test_accept_refused_when_health_blocked(self) -> None:
        _task_id, completion_id = _completed_task(self.db_path)
        for _ in range(4):
            mc.log_health_event("ollama", "unavailable", db_path=self.db_path)
        mc.set_role("approver", "architect", db_path=self.db_path)
        with self.assertRaises(ValueError) as ctx:
            mc.accept_task_completion(
                completion_id, session_id="approver", db_path=self.db_path
            )
        self.assertIn("blocked", str(ctx.exception))

    def test_cannot_accept_same_completion_twice(self) -> None:
        _task_id, completion_id = _completed_task(self.db_path)
        mc.set_role("approver", "architect", db_path=self.db_path)
        mc.accept_task_completion(completion_id, session_id="approver", db_path=self.db_path)
        with self.assertRaises(ValueError) as ctx:
            mc.accept_task_completion(
                completion_id, session_id="approver", db_path=self.db_path
            )
        self.assertIn("already accepted", str(ctx.exception))

    def test_task_status_unaffected_by_accept_step(self) -> None:
        task_id, completion_id = _completed_task(self.db_path)
        before = [t for t in mc.list_tasks(db_path=self.db_path) if t["id"] == task_id][0]
        self.assertEqual("done", before["status"])
        mc.set_role("approver", "architect", db_path=self.db_path)
        mc.accept_task_completion(completion_id, session_id="approver", db_path=self.db_path)
        after = [t for t in mc.list_tasks(db_path=self.db_path) if t["id"] == task_id][0]
        self.assertEqual("done", after["status"])

    def test_list_task_receipts_filters_by_task_id(self) -> None:
        task_id, completion_id = _completed_task(self.db_path)
        mc.set_role("approver", "architect", db_path=self.db_path)
        mc.accept_task_completion(completion_id, session_id="approver", db_path=self.db_path)
        all_receipts = mc.list_task_receipts(db_path=self.db_path)
        self.assertEqual(1, len(all_receipts))
        scoped = mc.list_task_receipts(task_id, db_path=self.db_path)
        self.assertEqual(1, len(scoped))
        other_task_receipts = mc.list_task_receipts(999999, db_path=self.db_path)
        self.assertEqual(0, len(other_task_receipts))


if __name__ == "__main__":
    unittest.main()
