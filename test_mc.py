"""Tests for mc.py — the only file allowed to touch the database."""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path

import mc


class McTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()  # let sqlite create it fresh
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_init_db_creates_tables(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
        self.assertEqual({"sessions", "checkpoints", "tasks"}, names)

    def test_ensure_session_is_idempotent(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.ensure_session("s1", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = ?", ("s1",)
            ).fetchone()[0]
        self.assertEqual(1, count)

    def test_write_and_read_latest_checkpoint(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="first", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="second", db_path=self.db_path)
        latest = mc.latest_checkpoint("s1", db_path=self.db_path)
        self.assertEqual("second", latest["current_task"])

    def test_latest_checkpoint_falls_back_when_session_has_none(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="only one", db_path=self.db_path)
        latest = mc.latest_checkpoint("unknown-session", db_path=self.db_path)
        self.assertEqual("only one", latest["current_task"])

    def test_add_and_list_open_tasks(self) -> None:
        mc.add_task("write tests", db_path=self.db_path)
        mc.add_task("ship it", db_path=self.db_path)
        titles = {task["title"] for task in mc.list_open_tasks(db_path=self.db_path)}
        self.assertEqual({"write tests", "ship it"}, titles)

    def test_complete_task_marks_done_and_hides_from_open_list(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        result = mc.complete_task(task_id, db_path=self.db_path)
        self.assertEqual("done", result["status"])
        self.assertEqual([], mc.list_open_tasks(db_path=self.db_path))

    def test_complete_task_unknown_id_refused(self) -> None:
        with self.assertRaises(mc.MCCompletionRefused):
            mc.complete_task(999, db_path=self.db_path)

    def test_complete_task_twice_refused(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        mc.complete_task(task_id, db_path=self.db_path)
        with self.assertRaises(mc.MCCompletionRefused):
            mc.complete_task(task_id, db_path=self.db_path)

    def test_add_task_with_done_status_refused(self) -> None:
        with self.assertRaises(mc.MCCompletionRefused):
            mc.add_task("sneaky", status="done", db_path=self.db_path)

    def test_raw_update_to_done_refused(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        with self.assertRaises(mc.MCCompletionRefused):
            with contextlib.closing(mc._connect(self.db_path)) as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,)
                )

    def test_raw_update_to_non_done_status_allowed(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'in_progress' WHERE id = ?", (task_id,)
            )
            conn.commit()
        titles = [
            task["status"] for task in mc.list_open_tasks(db_path=self.db_path)
        ]
        self.assertEqual(["in_progress"], titles)

    def test_cursor_cannot_bypass_the_guard(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        with self.assertRaises(mc.MCCompletionRefused):
            with contextlib.closing(mc._connect(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,)
                )


if __name__ == "__main__":
    unittest.main()
