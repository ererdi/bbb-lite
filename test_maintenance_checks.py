"""Tests for run_maintenance_checks()/maintenance_runs — the hook-triggered,
daemon-free maintenance battery (bbb-os-core parity item 3, "Living
Contract" self-check without a scheduler)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mc

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import session_start  # noqa: E402 - path insert must run first
import stop_checkpoint  # noqa: E402


def _test_evidence_id(db_path: Path) -> int:
    return mc.record_evidence("test", "fixture", "passed", db_path=db_path)


class RunMaintenanceChecksTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_all_checks_pass_on_a_healthy_empty_db(self) -> None:
        results = mc.run_maintenance_checks(db_path=self.db_path)
        statuses = {r["check_name"]: r["status"] for r in results}
        self.assertEqual("pass", statuses["integrity"])
        self.assertEqual("pass", statuses["orphaned_graph_edges"])
        self.assertEqual("pass", statuses["orphaned_context_edges"])
        self.assertEqual("pass", statuses["overdue_reminders"])
        # no session_id/checkpoint given, so memory_currentness_conflicts is
        # skipped entirely (nothing to check against), not recorded as pass/fail.
        self.assertNotIn("memory_currentness_conflicts", statuses)

    def test_passing_checks_do_not_affect_get_health_state(self) -> None:
        # Regression guard: get_health_state() counts ANY recent health_events
        # row, so logging passes there would false-positive a clean run to
        # 'blocked'. Only failures may land in health_events.
        mc.run_maintenance_checks(db_path=self.db_path)
        self.assertEqual("healthy", mc.get_health_state(db_path=self.db_path))
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            health_count = conn.execute("SELECT COUNT(*) FROM health_events").fetchone()[0]
        self.assertEqual(0, health_count)

    def test_all_checks_recorded_to_maintenance_runs_regardless_of_outcome(self) -> None:
        mc.run_maintenance_checks(session_id="s1", db_path=self.db_path)
        runs = mc.list_maintenance_runs(db_path=self.db_path)
        check_names = {r["check_name"] for r in runs}
        self.assertIn("integrity", check_names)
        self.assertIn("orphaned_graph_edges", check_names)
        self.assertIn("orphaned_context_edges", check_names)
        self.assertIn("overdue_reminders", check_names)
        for run in runs:
            self.assertIn(run["status"], ("pass", "fail"))

    def test_detects_orphaned_graph_edge(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                """INSERT INTO graph_edges (src, dst, type, weight, authority, updated_at)
                   VALUES ('missing::a', 'missing::b', 'depends-on', 1.0, 'system', '2026-01-01T00:00:00+00:00')"""
            )
            conn.commit()
        results = mc.run_maintenance_checks(db_path=self.db_path)
        orphan_result = next(r for r in results if r["check_name"] == "orphaned_graph_edges")
        self.assertEqual("fail", orphan_result["status"])
        self.assertIn("1 graph_edges", orphan_result["detail"])
        health_rows = mc.query_rows(
            "SELECT * FROM health_events WHERE component = 'maintenance'", db_path=self.db_path
        )
        self.assertEqual(1, len(health_rows))
        self.assertEqual("degraded", mc.get_health_state(db_path=self.db_path))

    def test_detects_orphaned_context_edge(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                """INSERT INTO context_edges (from_node_id, to_node_id, relation, created_at)
                   VALUES (9999, 9998, 'relates-to', '2026-01-01T00:00:00+00:00')"""
            )
            conn.commit()
        results = mc.run_maintenance_checks(db_path=self.db_path)
        orphan_result = next(r for r in results if r["check_name"] == "orphaned_context_edges")
        self.assertEqual("fail", orphan_result["status"])

    def test_detects_overdue_reminder(self) -> None:
        mc.add_task("late thing", due_date="2020-01-01", db_path=self.db_path)
        results = mc.run_maintenance_checks(db_path=self.db_path)
        overdue_result = next(r for r in results if r["check_name"] == "overdue_reminders")
        self.assertEqual("fail", overdue_result["status"])
        self.assertIn("1 task(s)", overdue_result["detail"])

    def test_memory_conflict_check_runs_when_checkpoint_has_current_task(self) -> None:
        mc.write_checkpoint("s1", current_task="billing invoice work", db_path=self.db_path)
        with mock.patch("mc.detect_memory_conflicts", return_value=[{"a": 1, "b": 2}]):
            results = mc.run_maintenance_checks(session_id="s1", db_path=self.db_path)
        conflict_result = next(
            r for r in results if r["check_name"] == "memory_currentness_conflicts"
        )
        self.assertEqual("fail", conflict_result["status"])

    def test_one_broken_check_does_not_abort_the_rest(self) -> None:
        # Only the very first PRAGMA integrity_check call raises; every other
        # _connect() call (including _record()'s own writes) behaves normally.
        real_connect = mc._connect
        calls = {"n": 0}

        def _flaky_connect(db_path=mc.DB_PATH):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_connect(db_path)

        with mock.patch("mc._connect", side_effect=_flaky_connect):
            results = mc.run_maintenance_checks(db_path=self.db_path)
        check_names = {r["check_name"] for r in results}
        integrity_result = next(r for r in results if r["check_name"] == "integrity")
        self.assertEqual("fail", integrity_result["status"])
        self.assertIn("check raised", integrity_result["detail"])
        # the other checks still ran and were recorded despite the first failure
        self.assertIn("orphaned_graph_edges", check_names)
        self.assertIn("orphaned_context_edges", check_names)
        self.assertIn("overdue_reminders", check_names)


class SessionStartMaintenanceWiringTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_session_start_runs_maintenance_checks(self) -> None:
        with mock.patch("session_start.mc.run_maintenance_checks") as run_checks:
            payload = json.dumps({"session_id": "s1", "source": "startup"})
            with mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(
                io.StringIO()
            ):
                session_start.main()
        run_checks.assert_called_once_with("s1")

    def test_session_start_survives_maintenance_checks_raising(self) -> None:
        with mock.patch(
            "session_start.mc.run_maintenance_checks", side_effect=RuntimeError("boom")
        ):
            payload = json.dumps({"session_id": "s1", "source": "startup"})
            with mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(
                io.StringIO()
            ) as buf:
                session_start.main()  # must not raise
        self.assertIn("[bbb-lite] session s1", buf.getvalue())


class StopCheckpointMaintenanceWiringTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _run(self, session_id: str = "s1") -> None:
        payload = json.dumps({"session_id": session_id, "transcript_path": ""})
        with mock.patch("sys.stdin", io.StringIO(payload)):
            stop_checkpoint.main()

    def test_stop_checkpoint_runs_maintenance_checks(self) -> None:
        with mock.patch("stop_checkpoint.mc.write_checkpoint"), mock.patch(
            "stop_checkpoint.mc.record_lifecycle_run"
        ), mock.patch("stop_checkpoint.mc.run_maintenance_checks") as run_checks:
            self._run("s1")
        run_checks.assert_called_once_with("s1")

    def test_stop_checkpoint_survives_maintenance_checks_raising(self) -> None:
        with mock.patch("stop_checkpoint.mc.write_checkpoint") as write_checkpoint, mock.patch(
            "stop_checkpoint.mc.record_lifecycle_run"
        ), mock.patch(
            "stop_checkpoint.mc.run_maintenance_checks", side_effect=RuntimeError("boom")
        ):
            self._run("s1")  # must not raise
        write_checkpoint.assert_called_once()


if __name__ == "__main__":
    unittest.main()
