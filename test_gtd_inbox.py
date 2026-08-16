"""Tests for list_inbox_tasks()/list_overdue_reminders()/review() — the GTD
inbox/review analog (bbb-os-core parity item 7), query-only over the
existing tasks/sprints schema (no new tables)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mc


def _iso_days(delta: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=delta)).isoformat()


class GtdInboxTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_list_inbox_tasks_returns_tasks_without_sprint_id(self) -> None:
        untriaged = mc.add_task("untriaged idea", db_path=self.db_path)
        inbox = mc.list_inbox_tasks(db_path=self.db_path)
        self.assertEqual([untriaged], [t["id"] for t in inbox])

    def test_list_inbox_tasks_excludes_done_tasks(self) -> None:
        task_id = mc.add_task("untriaged, will be done", status="in_progress", db_path=self.db_path)
        evidence_id = mc.record_evidence("test", "fixture", "passed", db_path=self.db_path)
        mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="done",
            db_path=self.db_path,
        )
        inbox = mc.list_inbox_tasks(db_path=self.db_path)
        self.assertEqual([], inbox)

    def test_list_inbox_tasks_excludes_tasks_with_sprint_id(self) -> None:
        project_id = mc.add_project("bbb-lite", db_path=self.db_path)
        sprint_id = mc.add_sprint(project_id, "sprint 1", db_path=self.db_path)
        mc.add_task("triaged task", sprint_id=sprint_id, db_path=self.db_path)
        inbox = mc.list_inbox_tasks(db_path=self.db_path)
        self.assertEqual([], inbox)

    def test_list_overdue_reminders_returns_past_due_open_tasks(self) -> None:
        overdue_id = mc.add_task("late thing", due_date=_iso_days(-3), db_path=self.db_path)
        mc.add_task("future thing", due_date=_iso_days(3), db_path=self.db_path)
        overdue = mc.list_overdue_reminders(db_path=self.db_path)
        self.assertEqual([overdue_id], [t["id"] for t in overdue])
        self.assertIn("people", overdue[0])

    def test_list_overdue_reminders_excludes_done_and_future(self) -> None:
        done_id = mc.add_task("late but done", status="in_progress", due_date=_iso_days(-1), db_path=self.db_path)
        evidence_id = mc.record_evidence("test", "fixture", "passed", db_path=self.db_path)
        mc.complete_task(
            done_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="done",
            db_path=self.db_path,
        )
        mc.add_task("future thing", due_date=_iso_days(1), db_path=self.db_path)
        overdue = mc.list_overdue_reminders(db_path=self.db_path)
        self.assertEqual([], overdue)

    def test_review_bundles_inbox_overdue_and_upcoming(self) -> None:
        inbox_id = mc.add_task("untriaged", db_path=self.db_path)
        overdue_id = mc.add_task("late", due_date=_iso_days(-1), db_path=self.db_path)
        upcoming_id = mc.add_task("soon", due_date=_iso_days(2), db_path=self.db_path)
        snapshot = mc.review(db_path=self.db_path)
        self.assertEqual({"inbox", "overdue", "upcoming"}, snapshot.keys())
        self.assertIn(inbox_id, [t["id"] for t in snapshot["inbox"]])
        self.assertIn(overdue_id, [t["id"] for t in snapshot["overdue"]])
        self.assertIn(upcoming_id, [t["id"] for t in snapshot["upcoming"]])


if __name__ == "__main__":
    unittest.main()
