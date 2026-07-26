"""Tests for mc.py — the only file allowed to touch the database."""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

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
        self.assertEqual(
            {
                "sessions", "checkpoints", "tasks", "memory_index", "task_activity",
                "projects", "sprints", "roles", "turns", "decision_packets",
                "dispatch_runs", "agent_outputs", "evidence", "events_log",
                "capability_boundaries",
            },
            names,
        )

    def test_init_db_adds_embedding_column_to_memory_index(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_index)").fetchall()}
        self.assertIn("embedding", columns)

    def test_init_db_rerun_is_safe_with_existing_embedding_column(self) -> None:
        mc.init_db(self.db_path)  # must not raise on a second run
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(memory_index)").fetchall()]
        self.assertEqual(1, columns.count("embedding"))

    def test_init_db_adds_sprint_id_column_to_tasks(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        self.assertIn("sprint_id", columns)

    def test_init_db_rerun_is_safe_with_existing_sprint_id_column(self) -> None:
        mc.init_db(self.db_path)  # must not raise on a second run
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()]
        self.assertEqual(1, columns.count("sprint_id"))

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

    def test_list_tasks_includes_open_and_done(self) -> None:
        open_id = mc.add_task("write tests", db_path=self.db_path)
        done_id = mc.add_task("ship it", db_path=self.db_path)
        mc.complete_task(done_id, db_path=self.db_path)
        by_id = {task["id"]: task["status"] for task in mc.list_tasks(db_path=self.db_path)}
        self.assertEqual({open_id: "todo", done_id: "done"}, by_id)

    def test_add_and_list_projects(self) -> None:
        mc.add_project("bbb-lite", db_path=self.db_path)
        mc.add_project("side project", status="paused", db_path=self.db_path)
        projects = mc.list_projects(db_path=self.db_path)
        by_name = {project["name"]: project["status"] for project in projects}
        self.assertEqual({"bbb-lite": "active", "side project": "paused"}, by_name)

    def test_add_and_list_sprints_for_project(self) -> None:
        project_id = mc.add_project("bbb-lite", db_path=self.db_path)
        other_project_id = mc.add_project("other", db_path=self.db_path)
        mc.add_sprint(project_id, "sprint 1", db_path=self.db_path)
        mc.add_sprint(other_project_id, "other sprint", db_path=self.db_path)
        names = {sprint["name"] for sprint in mc.list_sprints(project_id, db_path=self.db_path)}
        self.assertEqual({"sprint 1"}, names)

    def test_list_sprints_without_project_id_returns_all(self) -> None:
        project_id = mc.add_project("bbb-lite", db_path=self.db_path)
        other_project_id = mc.add_project("other", db_path=self.db_path)
        mc.add_sprint(project_id, "sprint 1", db_path=self.db_path)
        mc.add_sprint(other_project_id, "other sprint", db_path=self.db_path)
        names = {sprint["name"] for sprint in mc.list_sprints(db_path=self.db_path)}
        self.assertEqual({"sprint 1", "other sprint"}, names)

    def test_add_task_links_to_sprint(self) -> None:
        project_id = mc.add_project("bbb-lite", db_path=self.db_path)
        sprint_id = mc.add_sprint(project_id, "sprint 1", db_path=self.db_path)
        task_id = mc.add_task("write tests", sprint_id=sprint_id, db_path=self.db_path)
        task = next(
            task for task in mc.list_tasks(db_path=self.db_path) if task["id"] == task_id
        )
        self.assertEqual(sprint_id, task["sprint_id"])

    def test_add_task_without_sprint_id_defaults_to_none(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        task = next(
            task for task in mc.list_tasks(db_path=self.db_path) if task["id"] == task_id
        )
        self.assertIsNone(task["sprint_id"])

    def test_project_status_counts_open_and_done_tasks_across_sprints(self) -> None:
        project_id = mc.add_project("bbb-lite", db_path=self.db_path)
        sprint1 = mc.add_sprint(project_id, "sprint 1", db_path=self.db_path)
        sprint2 = mc.add_sprint(project_id, "sprint 2", db_path=self.db_path)
        other_project_id = mc.add_project("other", db_path=self.db_path)
        other_sprint = mc.add_sprint(other_project_id, "other sprint", db_path=self.db_path)

        mc.add_task("task a", sprint_id=sprint1, db_path=self.db_path)
        done_id = mc.add_task("task b", sprint_id=sprint1, db_path=self.db_path)
        mc.complete_task(done_id, db_path=self.db_path)
        mc.add_task("task c", sprint_id=sprint2, db_path=self.db_path)
        mc.add_task("task d", sprint_id=other_sprint, db_path=self.db_path)
        mc.add_task("no sprint", db_path=self.db_path)

        status = mc.project_status(project_id, db_path=self.db_path)
        self.assertEqual("bbb-lite", status["name"])
        self.assertEqual(2, status["sprint_count"])
        self.assertEqual(2, status["open_task_count"])
        self.assertEqual(1, status["done_task_count"])

    def test_project_status_unknown_project_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            mc.project_status(999, db_path=self.db_path)

    def test_list_sessions_returns_all_sessions(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.ensure_session("s2", db_path=self.db_path)
        ids = {session["session_id"] for session in mc.list_sessions(db_path=self.db_path)}
        self.assertEqual({"s1", "s2"}, ids)

    def test_list_task_activity_returns_all_rows(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task("write tests", session_id="s1", db_path=self.db_path)
        mc.complete_task(task_id, session_id="s1", db_path=self.db_path)
        activities = [row["activity"] for row in mc.list_task_activity(db_path=self.db_path)]
        self.assertEqual(["created", "completed"], activities)

    def test_capability_boundaries_are_seeded_disabled(self) -> None:
        boundaries = {
            row["capability"]: row for row in mc.list_capability_boundaries(db_path=self.db_path)
        }
        self.assertIn("voice", boundaries)
        self.assertFalse(boundaries["voice"]["enabled"])
        self.assertIn("out of scope", boundaries["voice"]["reason"].lower())

    def test_upsert_capability_boundary_updates_existing_row(self) -> None:
        mc.upsert_capability_boundary(
            "voice", True, "local harness enabled", db_path=self.db_path
        )
        boundaries = {
            row["capability"]: row for row in mc.list_capability_boundaries(db_path=self.db_path)
        }
        self.assertTrue(boundaries["voice"]["enabled"])
        self.assertEqual("local harness enabled", boundaries["voice"]["reason"])

    def test_record_and_list_turns_for_session(self) -> None:
        mc.record_turn("s1", "user", "hello", role="brozzer", db_path=self.db_path)
        mc.record_turn(
            "s1", "assistant", "hi", role="brozzer", engine="local", db_path=self.db_path
        )
        mc.record_turn("s2", "user", "ignored", db_path=self.db_path)
        turns = mc.list_turns("s1", db_path=self.db_path)
        self.assertEqual(["user", "assistant"], [row["speaker"] for row in turns])
        self.assertEqual("local", turns[-1]["engine"])

    def test_record_and_list_events(self) -> None:
        event_id = mc.record_event(
            "assistant_reply",
            session_id="s1",
            source="assistant_console",
            details={"engine": "local"},
            db_path=self.db_path,
        )
        events = mc.list_events(db_path=self.db_path)
        self.assertEqual(event_id, events[-1]["id"])
        self.assertEqual("assistant_reply", events[-1]["event_type"])
        self.assertIn('"engine": "local"', events[-1]["details_json"])

    def test_create_decision_packet_records_event(self) -> None:
        packet_id = mc.create_decision_packet(
            "s1",
            "Use local SQLite as MC source of truth.",
            context_gist="HOSNI not connected",
            db_path=self.db_path,
        )
        packets = mc.list_decision_packets(db_path=self.db_path)
        events = mc.list_events(db_path=self.db_path)
        self.assertEqual(packet_id, packets[-1]["id"])
        self.assertEqual("new", packets[-1]["status"])
        self.assertEqual("decision_packet_created", events[-1]["event_type"])

    def test_dispatch_run_and_agent_outputs_are_listed(self) -> None:
        run_id = mc.create_dispatch_run(
            "s1", status="queued", summary="local architect dispatch", db_path=self.db_path
        )
        output_id = mc.record_agent_output(
            run_id, "architect", "reviewed boundaries", db_path=self.db_path
        )
        runs = mc.list_dispatch_runs(db_path=self.db_path)
        outputs = mc.list_agent_outputs(run_id, db_path=self.db_path)
        self.assertEqual(run_id, runs[-1]["id"])
        self.assertEqual(output_id, outputs[-1]["id"])
        self.assertEqual("architect", outputs[-1]["role"])

    def test_record_and_list_evidence(self) -> None:
        evidence_id = mc.record_evidence(
            "tests",
            "unit suite",
            "passed",
            details={"passed": 1, "total": 1},
            db_path=self.db_path,
        )
        evidence = mc.list_evidence(db_path=self.db_path)
        self.assertEqual(evidence_id, evidence[-1]["id"])
        self.assertEqual("passed", evidence[-1]["status"])

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

    def test_pick_next_action_none_without_open_tasks(self) -> None:
        self.assertIsNone(mc.pick_next_action(db_path=self.db_path))

    def test_pick_next_action_returns_oldest_open_task(self) -> None:
        mc.add_task("first task", db_path=self.db_path)
        mc.add_task("second task", db_path=self.db_path)
        self.assertEqual("Next: first task", mc.pick_next_action(db_path=self.db_path))

    def test_pick_next_action_skips_done_tasks(self) -> None:
        first_id = mc.add_task("first task", db_path=self.db_path)
        mc.add_task("second task", db_path=self.db_path)
        mc.complete_task(first_id, db_path=self.db_path)
        self.assertEqual("Next: second task", mc.pick_next_action(db_path=self.db_path))

    def test_has_completed_task_since_false_without_completion(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.add_task("write tests", db_path=self.db_path)
        self.assertFalse(mc.has_completed_task_since("s1", db_path=self.db_path))

    def test_has_completed_task_since_true_after_complete_task(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task("write tests", db_path=self.db_path)
        mc.complete_task(task_id, db_path=self.db_path)
        self.assertTrue(mc.has_completed_task_since("s1", db_path=self.db_path))

    def test_has_completed_task_since_false_for_unknown_session(self) -> None:
        self.assertFalse(mc.has_completed_task_since("no-such-session", db_path=self.db_path))

    def test_add_task_without_session_id_writes_no_activity(self) -> None:
        mc.add_task("write tests", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM task_activity").fetchone()[0]
        self.assertEqual(0, count)

    def test_add_task_with_session_id_records_created_activity(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task("write tests", session_id="s1", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT task_id, session_id, activity FROM task_activity"
            ).fetchone()
        self.assertEqual((task_id, "s1", "created"), tuple(row))

    def test_complete_task_without_session_id_writes_no_activity(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        mc.complete_task(task_id, db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM task_activity").fetchone()[0]
        self.assertEqual(0, count)

    def test_complete_task_with_session_id_records_completed_activity(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task("write tests", session_id="s1", db_path=self.db_path)
        mc.complete_task(task_id, session_id="s1", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            activities = {
                row[0]
                for row in conn.execute(
                    "SELECT activity FROM task_activity WHERE task_id = ?", (task_id,)
                ).fetchall()
            }
        self.assertEqual({"created", "completed"}, activities)

    def test_tasks_touched_by_session_dedupes_and_flags_completion(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        done_id = mc.add_task("finish report", session_id="s1", db_path=self.db_path)
        open_id = mc.add_task("review pr", session_id="s1", db_path=self.db_path)
        mc.complete_task(done_id, session_id="s1", db_path=self.db_path)

        touched = mc.tasks_touched_by_session("s1", db_path=self.db_path)
        by_id = {task["task_id"]: task for task in touched}

        self.assertEqual({done_id, open_id}, set(by_id))
        self.assertTrue(by_id[done_id]["completed_by_session"])
        self.assertFalse(by_id[open_id]["completed_by_session"])

    def test_tasks_touched_by_session_empty_for_unknown_session(self) -> None:
        self.assertEqual(
            [], mc.tasks_touched_by_session("no-such-session", db_path=self.db_path)
        )

    def test_session_score_no_tasks_touched_floors_at_100(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        score = mc.session_score("s1", db_path=self.db_path)
        self.assertEqual(0, score["tasks_touched"])
        self.assertEqual(0, score["tasks_completed"])
        self.assertEqual(1.0, score["completion_rate"])
        self.assertEqual(100, score["score"])

    def test_session_score_half_completed_scores_50(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        done_id = mc.add_task("finish report", session_id="s1", db_path=self.db_path)
        mc.add_task("review pr", session_id="s1", db_path=self.db_path)
        mc.complete_task(done_id, session_id="s1", db_path=self.db_path)

        score = mc.session_score("s1", db_path=self.db_path)
        self.assertEqual(2, score["tasks_touched"])
        self.assertEqual(1, score["tasks_completed"])
        self.assertEqual(0.5, score["completion_rate"])
        self.assertEqual(50, score["score"])

    def test_session_score_all_completed_scores_100(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        first_id = mc.add_task("finish report", session_id="s1", db_path=self.db_path)
        second_id = mc.add_task("review pr", session_id="s1", db_path=self.db_path)
        mc.complete_task(first_id, session_id="s1", db_path=self.db_path)
        mc.complete_task(second_id, session_id="s1", db_path=self.db_path)

        score = mc.session_score("s1", db_path=self.db_path)
        self.assertEqual(2, score["tasks_touched"])
        self.assertEqual(2, score["tasks_completed"])
        self.assertEqual(100, score["score"])

    def test_session_score_counts_checkpoints_written(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="first", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="second", db_path=self.db_path)
        score = mc.session_score("s1", db_path=self.db_path)
        self.assertEqual(2, score["checkpoints_written"])

    def test_mark_session_completed_updates_status(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.mark_session_completed("s1", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE session_id = ?", ("s1",)
            ).fetchone()
        self.assertEqual("completed", row["status"])

    def test_cursor_cannot_bypass_the_guard(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        with self.assertRaises(mc.MCCompletionRefused):
            with contextlib.closing(mc._connect(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,)
                )


class StageGTestCase(unittest.TestCase):
    """Stage G: sweep_stale_sessions / check_floor."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _insert_session(self, session_id: str, started_at: str, status: str = "active") -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, started_at, status) VALUES (?, ?, ?)",
                (session_id, started_at, status),
            )
            conn.commit()

    def _insert_task(self, title: str, created_at: str, status: str = "todo") -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO tasks (title, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (title, status, created_at, created_at),
            )
            conn.commit()

    def _insert_memory_row(self, source_file: str) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO memory_index (source_file, title, content, keywords) "
                "VALUES (?, ?, ?, ?)",
                (source_file, "title", "content", "keyword"),
            )
            conn.commit()

    def _insert_checkpoint(self, session_id: str, created_at: str) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO checkpoints (session_id, created_at) VALUES (?, ?)",
                (session_id, created_at),
            )
            conn.commit()

    def _iso(self, *, hours_ago: float = 0, days_ago: float = 0) -> str:
        delta = timedelta(hours=hours_ago, days=days_ago)
        return (datetime.now(timezone.utc) - delta).isoformat()

    def test_sweep_stale_sessions_marks_old_active_sessions_stale(self) -> None:
        self._insert_session("old", self._iso(hours_ago=48))
        changed = mc.sweep_stale_sessions(hours=24, db_path=self.db_path)
        self.assertEqual(1, changed)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE session_id = ?", ("old",)
            ).fetchone()
        self.assertEqual("stale", row["status"])

    def test_sweep_stale_sessions_leaves_recent_sessions_active(self) -> None:
        self._insert_session("recent", self._iso(hours_ago=1))
        changed = mc.sweep_stale_sessions(hours=24, db_path=self.db_path)
        self.assertEqual(0, changed)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE session_id = ?", ("recent",)
            ).fetchone()
        self.assertEqual("active", row["status"])

    def test_sweep_stale_sessions_ignores_already_stale_or_completed(self) -> None:
        self._insert_session("already-stale", self._iso(hours_ago=48), status="stale")
        self._insert_session("completed", self._iso(hours_ago=48), status="completed")
        changed = mc.sweep_stale_sessions(hours=24, db_path=self.db_path)
        self.assertEqual(0, changed)

    def test_check_floor_empty_when_nothing_flagged(self) -> None:
        self.assertEqual([], mc.check_floor(db_path=self.db_path))

    def test_check_floor_flags_many_stale_sessions(self) -> None:
        for i in range(4):
            self._insert_session(f"s{i}", self._iso(hours_ago=48), status="stale")
        findings = mc.check_floor(db_path=self.db_path)
        self.assertIn("4 sessions marked stale", findings)

    def test_check_floor_flags_large_memory_index(self) -> None:
        for i in range(501):
            self._insert_memory_row(f"file{i}.md")
        findings = mc.check_floor(db_path=self.db_path)
        self.assertIn("memory_index has 501 rows, consider review", findings)

    def test_check_floor_flags_old_open_tasks(self) -> None:
        for i in range(4):
            self._insert_task(f"task{i}", self._iso(days_ago=8))
        findings = mc.check_floor(db_path=self.db_path)
        self.assertIn("4 tasks open for 7+ days", findings)

    def test_check_safepoint_stale_flags_active_session_without_checkpoint(self) -> None:
        self._insert_session("no-checkpoint", self._iso(hours_ago=1))
        findings = mc.check_safepoint_stale(hours=2, db_path=self.db_path)
        self.assertIn(
            "session no-checkpoint: no recent checkpoint (possible safepoint-stale)",
            findings,
        )

    def test_check_safepoint_stale_flags_active_session_with_old_checkpoint(self) -> None:
        self._insert_session("stale-checkpoint", self._iso(hours_ago=1))
        self._insert_checkpoint("stale-checkpoint", self._iso(hours_ago=3))
        findings = mc.check_safepoint_stale(hours=2, db_path=self.db_path)
        self.assertIn(
            "session stale-checkpoint: no recent checkpoint (possible safepoint-stale)",
            findings,
        )

    def test_check_safepoint_stale_ignores_session_with_recent_checkpoint(self) -> None:
        self._insert_session("fresh-checkpoint", self._iso(hours_ago=1))
        self._insert_checkpoint("fresh-checkpoint", self._iso(hours_ago=0.5))
        findings = mc.check_safepoint_stale(hours=2, db_path=self.db_path)
        self.assertEqual([], findings)

    def test_check_safepoint_stale_ignores_non_active_sessions(self) -> None:
        self._insert_session("stale-session", self._iso(hours_ago=1), status="stale")
        findings = mc.check_safepoint_stale(hours=2, db_path=self.db_path)
        self.assertEqual([], findings)

    def test_check_safepoint_stale_does_not_use_another_sessions_checkpoint(self) -> None:
        """latest_checkpoint()'s cross-session fallback must not leak in here."""

        self._insert_session("has-no-checkpoint", self._iso(hours_ago=1))
        self._insert_session("has-recent-checkpoint", self._iso(hours_ago=1))
        self._insert_checkpoint("has-recent-checkpoint", self._iso(hours_ago=0.1))
        findings = mc.check_safepoint_stale(hours=2, db_path=self.db_path)
        self.assertIn(
            "session has-no-checkpoint: no recent checkpoint (possible safepoint-stale)",
            findings,
        )
        self.assertNotIn(
            "session has-recent-checkpoint: no recent checkpoint (possible safepoint-stale)",
            findings,
        )

    def test_check_floor_includes_safepoint_stale_findings(self) -> None:
        self._insert_session("no-checkpoint", self._iso(hours_ago=1))
        findings = mc.check_floor(db_path=self.db_path)
        self.assertIn(
            "session no-checkpoint: no recent checkpoint (possible safepoint-stale)",
            findings,
        )


class MemoryHelperTestCase(unittest.TestCase):
    """Stage C: pure helpers — no filesystem or DB involved."""

    def test_significant_words_drops_stopwords_and_short_words(self) -> None:
        words = mc._significant_words("The Guard Blocks Raw Done Writes via SQL")
        self.assertEqual(["guard", "blocks", "done", "writes"], words)

    def test_extract_keywords_dedupes_in_order_and_caps_at_limit(self) -> None:
        text = "guard guard blocks blocks writes checkpoint session task memory index"
        keywords = mc._extract_keywords(text, limit=3)
        self.assertEqual("guard, blocks, writes", keywords)


class ParseMemoryFileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def test_parse_memory_file_uses_frontmatter_title(self) -> None:
        path = Path(self.tmp_dir.name) / "note.md"
        path.write_text(
            "---\ntitle: Guard blocks done\ndate: 2026-07-21\n---\n\nBody text here.\n",
            encoding="utf-8",
        )
        title, body = mc._parse_memory_file(path)
        self.assertEqual("Guard blocks done", title)
        self.assertEqual("Body text here.", body)

    def test_parse_memory_file_without_frontmatter_uses_filename(self) -> None:
        path = Path(self.tmp_dir.name) / "plain-note.md"
        path.write_text("Just some body text.\n", encoding="utf-8")
        title, body = mc._parse_memory_file(path)
        self.assertEqual("plain-note", title)
        self.assertEqual("Just some body text.", body)


class MemoryIndexTestCase(unittest.TestCase):
    """Stage C: index_memory_files / search_memory / log_lesson.

    memory_dir in mc.py is hardcoded to Path(__file__).resolve().parent / "memory",
    so mc.__file__ is patched per test to point into an isolated temp directory —
    this must never touch the project's real memory/ directory.
    """

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

        self.project_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.project_dir.cleanup)
        self.memory_dir = Path(self.project_dir.name) / "memory"
        self.memory_dir.mkdir()

        patcher = mock.patch.object(mc, "__file__", str(Path(self.project_dir.name) / "mc.py"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _write_note(self, filename: str, title: str, body: str) -> None:
        (self.memory_dir / filename).write_text(
            f"---\ntitle: {title}\n---\n\n{body}\n", encoding="utf-8"
        )

    def test_index_memory_files_returns_count_and_populates_index(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        self._write_note("b.md", "Checkpoint note", "Checkpoints record the current task.")
        count = mc.index_memory_files(db_path=self.db_path)
        self.assertEqual(2, count)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute("SELECT source_file, title FROM memory_index").fetchall()
        self.assertEqual(
            {"a.md", "b.md"}, {row["source_file"] for row in rows}
        )

    def test_index_memory_files_upserts_without_duplicating_on_rerun(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        self._write_note("a.md", "Guard note updated", "Updated body about the guard.")
        count = mc.index_memory_files(db_path=self.db_path)
        self.assertEqual(1, count)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT title FROM memory_index WHERE source_file = 'a.md'"
            ).fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual("Guard note updated", rows[0]["title"])

    def test_search_memory_ranks_higher_keyword_count_first(self) -> None:
        self._write_note(
            "a.md", "Checkpoint basics", "Checkpoints record the current task once."
        )
        self._write_note(
            "b.md",
            "Checkpoint deep dive",
            "Checkpoint checkpoint checkpoint — checkpoints, checkpoints everywhere.",
        )
        mc.index_memory_files(db_path=self.db_path)
        results = mc.search_memory("checkpoint", db_path=self.db_path)
        self.assertEqual("b.md", results[0]["source_file"])

    def test_search_memory_title_hit_ranks_above_content_only_hit(self) -> None:
        self._write_note(
            "title_and_content.md",
            "Gizmo overview",
            "The gizmo behaves as expected in most scenarios.",
        )
        self._write_note(
            "content_only.md",
            "Widget overview",
            "The gizmo behaves as expected in most scenarios.",
        )
        mc.index_memory_files(db_path=self.db_path)
        results = mc.search_memory("gizmo", db_path=self.db_path)
        self.assertEqual("title_and_content.md", results[0]["source_file"])

    def test_search_memory_no_match_returns_empty(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        self.assertEqual([], mc.search_memory("nonexistentword", db_path=self.db_path))

    def test_search_memory_respects_limit(self) -> None:
        for i in range(5):
            self._write_note(f"note{i}.md", f"Note {i}", "session session session")
        mc.index_memory_files(db_path=self.db_path)
        results = mc.search_memory("session", limit=2, db_path=self.db_path)
        self.assertEqual(2, len(results))

    def test_log_lesson_writes_new_file_when_no_similar_note_exists(self) -> None:
        path = mc.log_lesson(
            "Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path
        )
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertIn("guard-blocks-done", path.name)
        content = path.read_text(encoding="utf-8")
        self.assertIn("title: Guard blocks done", content)
        self.assertIn("confidence: medium", content)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memory_index").fetchone()[0]
        self.assertEqual(1, count)

    def test_log_lesson_avoids_filename_collision(self) -> None:
        first = mc.log_lesson("Same title", "First body.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama", return_value="no"):
            second = mc.log_lesson("Same title", "Totally different unrelated body.", db_path=self.db_path)
        self.assertNotEqual(first.name, second.name)

    def test_log_lesson_skipped_when_ollama_confirms_duplicate(self) -> None:
        mc.log_lesson("Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama", return_value="Yes, same fact.") as mocked:
            result = mc.log_lesson(
                "Guard blocks done writes",
                "The guard refuses raw done writes too.",
                db_path=self.db_path,
            )
        self.assertIsNone(result)
        mocked.assert_called_once()
        self.assertEqual(1, len(list(self.memory_dir.glob("lesson-*.md"))))

    def test_log_lesson_kept_when_ollama_says_not_duplicate(self) -> None:
        mc.log_lesson("Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama", return_value="no"):
            result = mc.log_lesson(
                "Checkpoints have fallback",
                "latest_checkpoint falls back to the newest checkpoint overall.",
                db_path=self.db_path,
            )
        self.assertIsNotNone(result)
        self.assertEqual(2, len(list(self.memory_dir.glob("lesson-*.md"))))

    def test_log_lesson_kept_when_ollama_unavailable(self) -> None:
        mc.log_lesson("Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama", return_value=None):
            result = mc.log_lesson(
                "Guard blocks done again",
                "The guard refuses raw done writes, restated.",
                db_path=self.db_path,
            )
        self.assertIsNotNone(result)


class SemanticSearchTestCase(unittest.TestCase):
    """Stage E: real sentence-transformers embeddings, no mocking — proves meaning-based recall.

    Same isolation approach as MemoryIndexTestCase: mc.__file__ is patched into a temp
    project dir so memory_dir resolves under it, never the real memory/ directory.
    """

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

        self.project_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.project_dir.cleanup)
        self.memory_dir = Path(self.project_dir.name) / "memory"
        self.memory_dir.mkdir()

        patcher = mock.patch.object(mc, "__file__", str(Path(self.project_dir.name) / "mc.py"))
        patcher.start()
        self.addCleanup(patcher.stop)

        # _get_embedding_model() needs network on its very first call on a machine
        # (to download from the Hugging Face Hub); once cached to disk it's offline.
        # A machine/CI run with neither network nor a warm cache must skip these
        # tests, not error them out.
        try:
            mc._get_embedding_model()
        except Exception as exc:  # noqa: BLE001 - any failure here means "can't test this"
            self.skipTest(f"embedding model unavailable (no network and no local cache?): {exc}")

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _write_note(self, filename: str, title: str, body: str) -> None:
        (self.memory_dir / filename).write_text(
            f"---\ntitle: {title}\n---\n\n{body}\n", encoding="utf-8"
        )

    def test_embed_returns_float32_bytes_of_expected_length(self) -> None:
        vector_bytes = mc._embed("hello world")
        vector = np.frombuffer(vector_bytes, dtype=np.float32)
        self.assertEqual(384, vector.shape[0])  # all-MiniLM-L6-v2 output dimension

    def test_index_memory_files_stores_embedding_blob(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT embedding FROM memory_index WHERE source_file = 'a.md'"
            ).fetchone()
        self.assertIsNotNone(row["embedding"])
        self.assertEqual(384, np.frombuffer(row["embedding"], dtype=np.float32).shape[0])

    def test_semantic_search_finds_paraphrase_with_no_shared_words(self) -> None:
        # No word in common with the query below (guard/blocks/unsafe/database/writes
        # vs. system/refuses/unverified/changes/reach/data/store), only shared meaning.
        self._write_note(
            "guard.md", "Guard blocks writes", "the guard blocks unsafe database writes"
        )
        self._write_note(
            "refusal.md",
            "System refuses changes",
            "the system refuses to let unverified changes reach the data store",
        )
        # Unrelated control note: shares no words AND no meaning with the query.
        self._write_note(
            "coffee.md", "Coffee brewing notes", "grind the beans finely before brewing espresso"
        )
        mc.index_memory_files(db_path=self.db_path)

        query = "the guard blocks unsafe database writes"
        semantic_results = mc.semantic_search(query, limit=3, db_path=self.db_path)
        by_file = {item["source_file"]: item for item in semantic_results}

        print(
            "\n[semantic_search] query:", query,
            "\n  guard.md similarity   =", by_file["guard.md"]["similarity"],
            "\n  refusal.md similarity =", by_file["refusal.md"]["similarity"],
            "\n  coffee.md similarity  =", by_file["coffee.md"]["similarity"],
        )
        # The paraphrase must rank as the clear runner-up to the near-identical
        # text, and well above the topically unrelated control note.
        self.assertEqual(
            ["guard.md", "refusal.md", "coffee.md"],
            [item["source_file"] for item in semantic_results],
        )
        self.assertGreater(by_file["refusal.md"]["similarity"], by_file["coffee.md"]["similarity"])

        # Keyword search over the same data must NOT surface the paraphrase —
        # this is the gap semantic_search closes.
        keyword_results = mc.search_memory(query, db_path=self.db_path)
        keyword_files = {item["source_file"] for item in keyword_results}
        print("[search_memory] keyword hits:", keyword_files)
        self.assertNotIn("refusal.md", keyword_files)


class AskOllamaTestCase(unittest.TestCase):
    """Stage C: ask_ollama must never raise — fail-soft to None."""

    def test_returns_none_when_binary_missing(self) -> None:
        with mock.patch("mc.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(mc.ask_ollama("hello"))

    def test_returns_none_on_timeout(self) -> None:
        with mock.patch(
            "mc.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ollama", timeout=30),
        ):
            self.assertIsNone(mc.ask_ollama("hello"))

    def test_returns_none_on_nonzero_exit(self) -> None:
        completed = subprocess.CompletedProcess(args=["ollama"], returncode=1, stdout="")
        with mock.patch("mc.subprocess.run", return_value=completed):
            self.assertIsNone(mc.ask_ollama("hello"))

    def test_returns_none_on_blank_stdout(self) -> None:
        completed = subprocess.CompletedProcess(args=["ollama"], returncode=0, stdout="   \n")
        with mock.patch("mc.subprocess.run", return_value=completed):
            self.assertIsNone(mc.ask_ollama("hello"))

    def test_returns_stripped_stdout_on_success(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["ollama"], returncode=0, stdout="  yes, same fact  \n"
        )
        with mock.patch("mc.subprocess.run", return_value=completed) as mocked:
            self.assertEqual("yes, same fact", mc.ask_ollama("hello", model="gemma:2b"))
        args, kwargs = mocked.call_args
        self.assertEqual(["ollama", "run", "gemma:2b", "hello"], args[0])


class ClassifyTestCase(unittest.TestCase):
    """Stage F: classify() must map an ask_ollama answer onto one of `options`."""

    def test_returns_none_for_no_options(self) -> None:
        with mock.patch("mc.ask_ollama", return_value="architect"):
            self.assertIsNone(mc.classify("task", []))

    def test_returns_none_when_ollama_unavailable(self) -> None:
        with mock.patch("mc.ask_ollama", return_value=None):
            self.assertIsNone(mc.classify("task", ["architect", "builder"]))

    def test_returns_none_when_answer_matches_no_option(self) -> None:
        with mock.patch("mc.ask_ollama", return_value="not sure"):
            self.assertIsNone(mc.classify("task", ["architect", "builder"]))

    def test_matches_option_case_insensitively(self) -> None:
        with mock.patch("mc.ask_ollama", return_value="Architect") as mocked:
            self.assertEqual("architect", mc.classify("task", ["architect", "builder"]))
        mocked.assert_called_once()

    def test_matches_option_within_extra_wording(self) -> None:
        with mock.patch("mc.ask_ollama", return_value="I'd say builder is enough here."):
            self.assertEqual("builder", mc.classify("task", ["architect", "builder"]))

    def test_returns_original_casing_of_option(self) -> None:
        with mock.patch("mc.ask_ollama", return_value="BUILDER"):
            self.assertEqual("Builder", mc.classify("task", ["Architect", "Builder"]))

    def test_matches_hyphenated_option_despite_dirty_model_output(self) -> None:
        dirty = "Needs attention.\n\nThe presence of stale sessions indicate\x1b[8D\x1b[Ka problem."
        with mock.patch("mc.ask_ollama", return_value=dirty):
            self.assertEqual(
                "needs-attention", mc.classify("task", ["normal", "needs-attention"])
            )


class StageNTestCase(unittest.TestCase):
    """Stage N: roles table, set_role/get_role, can_write_code/can_write_data."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_undeclared_session_defaults_to_brozzer(self) -> None:
        self.assertEqual("brozzer", mc.get_role("s1", db_path=self.db_path))

    def test_undeclared_session_has_no_code_or_data_permission(self) -> None:
        self.assertFalse(mc.can_write_code("s1", db_path=self.db_path))
        self.assertFalse(mc.can_write_data("s1", db_path=self.db_path))

    def test_set_role_rejects_unknown_role(self) -> None:
        with self.assertRaises(ValueError):
            mc.set_role("s1", "wizard", db_path=self.db_path)

    def test_set_role_then_get_role_roundtrips(self) -> None:
        mc.set_role("s1", "builder", db_path=self.db_path)
        self.assertEqual("builder", mc.get_role("s1", db_path=self.db_path))

    def test_set_role_twice_replaces_the_previous_role(self) -> None:
        mc.set_role("s1", "advisor", db_path=self.db_path)
        mc.set_role("s1", "builder", db_path=self.db_path)
        self.assertEqual("builder", mc.get_role("s1", db_path=self.db_path))

    def test_builder_can_write_code_and_data(self) -> None:
        mc.set_role("s1", "builder", db_path=self.db_path)
        self.assertTrue(mc.can_write_code("s1", db_path=self.db_path))
        self.assertTrue(mc.can_write_data("s1", db_path=self.db_path))

    def test_advisor_and_architect_can_write_data_not_code(self) -> None:
        for role in ("advisor", "architect"):
            mc.set_role("s1", role, db_path=self.db_path)
            self.assertFalse(mc.can_write_code("s1", db_path=self.db_path))
            self.assertTrue(mc.can_write_data("s1", db_path=self.db_path))

    def test_roles_are_independent_per_session(self) -> None:
        mc.set_role("s1", "builder", db_path=self.db_path)
        mc.set_role("s2", "advisor", db_path=self.db_path)
        self.assertEqual("builder", mc.get_role("s1", db_path=self.db_path))
        self.assertEqual("advisor", mc.get_role("s2", db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
