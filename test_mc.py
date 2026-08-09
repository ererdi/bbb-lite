"""Tests for mc.py — the only file allowed to touch the database."""

from __future__ import annotations

import contextlib
import json
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
                "task_completions",
                "projects", "sprints", "roles", "turns", "decision_packets",
                "dispatch_runs", "agent_outputs", "evidence", "events_log",
                "capability_boundaries", "graph_nodes", "graph_edges",
                "answer_quality_scores", "answer_receipts", "lifecycle_runs",
                "decisions", "compact_snapshots", "documents",
                "context_nodes", "context_edges",
                "doc_index", "symbol_index",
                "term_aliases", "health_events", "pending_merges",
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
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        result = mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="tests written",
            db_path=self.db_path,
        )
        self.assertEqual("done", result["status"])
        self.assertEqual([], mc.list_open_tasks(db_path=self.db_path))

    def test_complete_task_unknown_id_refused(self) -> None:
        evidence_id = _passing_evidence_id(self.db_path)
        with self.assertRaises(mc.MCCompletionRefused):
            mc.complete_task(
                999,
                session_id="s1",
                evidence_id=evidence_id,
                completion_summary="n/a",
                db_path=self.db_path,
            )

    def test_complete_task_twice_refused(self) -> None:
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="tests written",
            db_path=self.db_path,
        )
        with self.assertRaises(mc.MCCompletionRefused):
            mc.complete_task(
                task_id,
                session_id="s1",
                evidence_id=evidence_id,
                completion_summary="tests written again",
                db_path=self.db_path,
            )

    def test_list_tasks_includes_open_and_done(self) -> None:
        open_id = mc.add_task("write tests", db_path=self.db_path)
        done_id = mc.add_task("ship it", status="in_progress", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            done_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="shipped",
            db_path=self.db_path,
        )
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
        done_id = mc.add_task(
            "task b", status="in_progress", sprint_id=sprint1, db_path=self.db_path
        )
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            done_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="task b done",
            db_path=self.db_path,
        )
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

    def test_add_and_get_document_returns_most_recent_of_type(self) -> None:
        project_id = mc.add_project("bbb-lite", db_path=self.db_path)
        mc.add_document(project_id, "northstar", "v1", "first draft", db_path=self.db_path)
        mc.add_document(project_id, "northstar", "v2", "second draft", db_path=self.db_path)
        doc = mc.get_document(project_id, "northstar", db_path=self.db_path)
        self.assertEqual("v2", doc["title"])

    def test_add_document_unknown_project_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            mc.add_document(999, "northstar", "v1", "content", db_path=self.db_path)

    def test_update_document_changes_content_in_place(self) -> None:
        project_id = mc.add_project("bbb-lite", db_path=self.db_path)
        doc_id = mc.add_document(project_id, "note", "n1", "old content", db_path=self.db_path)
        mc.update_document(doc_id, "new content", db_path=self.db_path)
        docs = mc.list_documents(project_id, db_path=self.db_path)
        self.assertEqual(1, len(docs))
        self.assertEqual("new content", docs[0]["content"])

    def test_list_documents_returns_all_types_for_project(self) -> None:
        project_id = mc.add_project("bbb-lite", db_path=self.db_path)
        other_project_id = mc.add_project("other", db_path=self.db_path)
        mc.add_document(project_id, "northstar", "north", "n", db_path=self.db_path)
        mc.add_document(project_id, "plan", "plan a", "p", db_path=self.db_path)
        mc.add_document(other_project_id, "note", "irrelevant", "x", db_path=self.db_path)
        titles = {doc["title"] for doc in mc.list_documents(project_id, db_path=self.db_path)}
        self.assertEqual({"north", "plan a"}, titles)

    def test_project_status_has_northstar_reflects_document_presence(self) -> None:
        project_id = mc.add_project("bbb-lite", db_path=self.db_path)
        self.assertFalse(mc.project_status(project_id, db_path=self.db_path)["has_northstar"])
        mc.add_document(project_id, "northstar", "north", "n", db_path=self.db_path)
        self.assertTrue(mc.project_status(project_id, db_path=self.db_path)["has_northstar"])

    def test_list_sessions_returns_all_sessions(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.ensure_session("s2", db_path=self.db_path)
        ids = {session["session_id"] for session in mc.list_sessions(db_path=self.db_path)}
        self.assertEqual({"s1", "s2"}, ids)

    def test_list_task_activity_returns_all_rows(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task(
            "write tests", status="in_progress", session_id="s1", db_path=self.db_path
        )
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="tests written",
            db_path=self.db_path,
        )
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

    def test_complete_dispatch_run_closes_run(self) -> None:
        run_id = mc.create_dispatch_run("s1", status="running", db_path=self.db_path)
        mc.complete_dispatch_run(
            run_id, status="completed", summary="builder finished", db_path=self.db_path
        )
        run = mc.list_dispatch_runs(db_path=self.db_path)[-1]
        self.assertEqual("completed", run["status"])
        self.assertEqual("builder finished", run["summary"])
        self.assertIsNotNone(run["completed_at"])

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

    def test_answer_quality_receipt_and_lifecycle_are_listed(self) -> None:
        score = mc.assess_answer_quality(
            "decision: keep SQLite",
            "Known:\n- MC memory checkpoint.\n\nRisk:\n- none.\n\nNext action:\n- create receipt.",
            context={"checkpoint": {"risk": "low"}},
            engine="local",
            decision_packet_id="d1",
        )
        quality_id = mc.record_answer_quality("s1", score, db_path=self.db_path)
        evidence_id = mc.record_evidence(
            "answer", "reply", score["status"], details=score, db_path=self.db_path
        )
        receipt_id = mc.record_answer_receipt(
            "s1",
            "answer-1",
            engine="local",
            role="brozzer",
            decision_packet_id="d1",
            quality_score_id=quality_id,
            evidence_id=evidence_id,
            checkpoint_risk="low",
            next_action="create receipt",
            db_path=self.db_path,
        )
        lifecycle_id = mc.record_lifecycle_run(
            "session_start", session_id="s1", source="test", db_path=self.db_path
        )
        self.assertEqual(quality_id, mc.list_answer_quality_scores("s1", db_path=self.db_path)[-1]["id"])
        self.assertEqual(receipt_id, mc.list_answer_receipts("s1", db_path=self.db_path)[-1]["id"])
        self.assertEqual(lifecycle_id, mc.list_lifecycle_runs("s1", db_path=self.db_path)[-1]["id"])
        self.assertGreaterEqual(score["total_score"], 70)

    def test_answer_quality_flags_unsupported_claim(self) -> None:
        score = mc.assess_answer_quality(
            "schedule a meeting",
            "I scheduled it in calendar and sent email.",
            engine="local",
        )
        self.assertEqual("needs_review", score["status"])
        self.assertEqual(0, score["role_compliance"])

    def test_load_graph_edges_and_traverse_dependencies(self) -> None:
        edge_file = self.db_path.with_suffix(".jsonl")
        edge_file.write_text(
            '{"src":"assistant","dst":"mission-control","type":"depends-on","evidence":"reads MC"}\n'
            '{"src":"mission-control","dst":"guarded-sqlite","type":"depends-on","evidence":"guarded writes"}\n',
            encoding="utf-8",
        )
        try:
            result = mc.load_graph_edges(edge_file, db_path=self.db_path)
            deps = mc.graph_depends_on("assistant", db_path=self.db_path)
            impact = mc.graph_impact("guarded-sqlite", db_path=self.db_path)
        finally:
            edge_file.unlink(missing_ok=True)
        self.assertEqual(2, result["loaded"])
        self.assertEqual(["mission-control", "guarded-sqlite"], [row["node"] for row in deps])
        self.assertEqual(["mission-control", "assistant"], [row["node"] for row in impact])
        self.assertEqual("current", deps[0]["currentness"])
        self.assertEqual("verified", deps[0]["confidence"])

    def test_graph_stats_counts_nodes_edges_and_authority(self) -> None:
        edge_file = self.db_path.with_suffix(".jsonl")
        edge_file.write_text(
            '{"src":"a","dst":"b","type":"depends-on","evidence":"fixture"}\n',
            encoding="utf-8",
        )
        try:
            mc.load_graph_edges(edge_file, db_path=self.db_path)
        finally:
            edge_file.unlink(missing_ok=True)
        stats = mc.graph_stats(db_path=self.db_path)
        self.assertEqual(2, stats["nodes"])
        self.assertEqual(1, stats["edges"])
        self.assertEqual(1, stats["curated_edges"])
        self.assertEqual(1, stats["by_type"]["depends-on"])
        self.assertEqual(1, stats["by_authority"]["trusted-typed"])
        self.assertEqual(1, stats["by_currentness"]["current"])
        self.assertEqual(1, stats["by_confidence"]["verified"])

    def test_curate_graph_edge_updates_quality_fields(self) -> None:
        edge_file = self.db_path.with_suffix(".jsonl")
        edge_file.write_text(
            '{"src":"a","dst":"b","type":"depends-on","evidence":"fixture"}\n',
            encoding="utf-8",
        )
        try:
            mc.load_graph_edges(edge_file, db_path=self.db_path)
        finally:
            edge_file.unlink(missing_ok=True)
        mc.curate_graph_edge(
            "a",
            "b",
            "depends-on",
            currentness="stale",
            confidence="review-needed",
            reason="test",
            db_path=self.db_path,
        )
        edge = mc.list_graph_edges(db_path=self.db_path)[-1]
        self.assertEqual("stale", edge["currentness"])
        self.assertEqual("review-needed", edge["confidence"])
        self.assertEqual(1, edge["curated"])

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
        first_id = mc.add_task("first task", status="in_progress", db_path=self.db_path)
        mc.add_task("second task", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            first_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="first task done",
            db_path=self.db_path,
        )
        self.assertEqual("Next: second task", mc.pick_next_action(db_path=self.db_path))

    def test_has_completed_task_since_false_without_completion(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.add_task("write tests", db_path=self.db_path)
        self.assertFalse(mc.has_completed_task_since("s1", db_path=self.db_path))

    def test_has_completed_task_since_true_after_complete_task(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="tests written",
            db_path=self.db_path,
        )
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

    def test_complete_task_with_session_id_records_completed_activity(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task(
            "write tests", status="in_progress", session_id="s1", db_path=self.db_path
        )
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="tests written",
            db_path=self.db_path,
        )
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
        done_id = mc.add_task(
            "finish report", status="in_progress", session_id="s1", db_path=self.db_path
        )
        open_id = mc.add_task("review pr", session_id="s1", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            done_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="report finished",
            db_path=self.db_path,
        )

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
        done_id = mc.add_task(
            "finish report", status="in_progress", session_id="s1", db_path=self.db_path
        )
        mc.add_task("review pr", session_id="s1", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            done_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="report finished",
            db_path=self.db_path,
        )

        score = mc.session_score("s1", db_path=self.db_path)
        self.assertEqual(2, score["tasks_touched"])
        self.assertEqual(1, score["tasks_completed"])
        self.assertEqual(0.5, score["completion_rate"])
        self.assertEqual(50, score["score"])

    def test_session_score_all_completed_scores_100(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        first_id = mc.add_task(
            "finish report", status="in_progress", session_id="s1", db_path=self.db_path
        )
        second_id = mc.add_task(
            "review pr", status="in_progress", session_id="s1", db_path=self.db_path
        )
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            first_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="report finished",
            db_path=self.db_path,
        )
        mc.complete_task(
            second_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="pr reviewed",
            db_path=self.db_path,
        )

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

    def test_session_score_context_axis_needs_a_checkpoint(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        no_checkpoint = mc.session_score("s1", db_path=self.db_path)
        self.assertEqual(50, no_checkpoint["context_score"])

        mc.write_checkpoint("s1", current_task="first", db_path=self.db_path)
        with_checkpoint = mc.session_score("s1", db_path=self.db_path)
        self.assertEqual(100, with_checkpoint["context_score"])

    def test_session_score_rules_axis_counts_denied_attempts_for_this_session(
        self,
    ) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.ensure_session("s2", db_path=self.db_path)
        log_path = Path(tempfile.mktemp(suffix=".log"))
        log_path.write_text(
            "2026-07-29T10:00:00 denied session=s1: role 'advisor' has no code-write permission\n"
            "2026-07-29T10:00:01 denied session=s2: role 'advisor' has no code-write permission\n"
            "2026-07-29T10:00:02 about to write: foo.py\n",
            encoding="utf-8",
        )
        try:
            score = mc.session_score("s1", db_path=self.db_path, log_path=log_path)
        finally:
            log_path.unlink(missing_ok=True)
        self.assertEqual(1, score["denied_write_attempts"])
        self.assertEqual(80, score["rules_score"])

    def test_session_score_rules_axis_defaults_to_100_when_log_missing(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        score = mc.session_score(
            "s1", db_path=self.db_path, log_path=Path(tempfile.mktemp(suffix=".log"))
        )
        self.assertEqual(0, score["denied_write_attempts"])
        self.assertEqual(100, score["rules_score"])

    def test_session_score_overall_averages_the_three_axes(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        done_id = mc.add_task(
            "finish report", status="in_progress", session_id="s1", db_path=self.db_path
        )
        mc.add_task("review pr", session_id="s1", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            done_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="report finished",
            db_path=self.db_path,
        )
        mc.write_checkpoint("s1", current_task="first", db_path=self.db_path)
        log_path = Path(tempfile.mktemp(suffix=".log"))
        log_path.write_text(
            "2026-07-29T10:00:00 denied session=s1: role 'advisor' has no code-write permission\n",
            encoding="utf-8",
        )
        try:
            score = mc.session_score("s1", db_path=self.db_path, log_path=log_path)
        finally:
            log_path.unlink(missing_ok=True)

        self.assertEqual(50, score["exec_score"])
        self.assertEqual(100, score["context_score"])
        self.assertEqual(80, score["rules_score"])
        self.assertEqual(77, score["overall_score"])

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


class StageAECompletionEvidenceTestCase(McTestCase):
    """Stage AE: complete_task() refuses to mark done without real evidence."""

    def test_complete_task_refuses_unknown_evidence_id(self) -> None:
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        with self.assertRaises(ValueError):
            mc.complete_task(
                task_id,
                session_id="s1",
                evidence_id=999999,
                completion_summary="tests written",
                db_path=self.db_path,
            )
        self.assertEqual(
            "in_progress", mc.list_tasks(db_path=self.db_path)[0]["status"]
        )

    def test_complete_task_refuses_failing_evidence(self) -> None:
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        failing_evidence_id = mc.record_evidence(
            "tests", "unit suite", "fail", db_path=self.db_path
        )
        with self.assertRaises(ValueError):
            mc.complete_task(
                task_id,
                session_id="s1",
                evidence_id=failing_evidence_id,
                completion_summary="tests written",
                db_path=self.db_path,
            )
        self.assertEqual(
            "in_progress", mc.list_tasks(db_path=self.db_path)[0]["status"]
        )

    def test_complete_task_refuses_empty_completion_summary(self) -> None:
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        with self.assertRaises(ValueError):
            mc.complete_task(
                task_id,
                session_id="s1",
                evidence_id=evidence_id,
                completion_summary="   ",
                db_path=self.db_path,
            )
        self.assertEqual(
            "in_progress", mc.list_tasks(db_path=self.db_path)[0]["status"]
        )

    def test_complete_task_writes_task_completions_row(self) -> None:
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="unit suite passed, tests written",
            db_path=self.db_path,
        )
        rows = mc.query_rows(
            "SELECT * FROM task_completions WHERE task_id = ?", (task_id,), db_path=self.db_path
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("s1", rows[0]["session_id"])
        self.assertEqual(evidence_id, rows[0]["evidence_id"])
        self.assertEqual("unit suite passed, tests written", rows[0]["completion_summary"])
        self.assertEqual("brozzer", rows[0]["completed_by_role"])

    def test_complete_task_derives_completed_by_role_from_get_role(self) -> None:
        mc.set_role("s1", "builder", db_path=self.db_path)
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        result = mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="tests written",
            db_path=self.db_path,
        )
        self.assertEqual("builder", result["completed_by_role"])
        row = mc.query_rows(
            "SELECT completed_by_role FROM task_completions WHERE task_id = ?",
            (task_id,),
            db_path=self.db_path,
        )[0]
        self.assertEqual("builder", row["completed_by_role"])

    def test_complete_task_still_refused_for_already_done_task_regardless_of_evidence(self) -> None:
        task_id = mc.add_task("write tests", status="in_progress", db_path=self.db_path)
        first_evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=first_evidence_id,
            completion_summary="tests written",
            db_path=self.db_path,
        )
        second_evidence_id = _passing_evidence_id(self.db_path)
        with self.assertRaises(mc.MCCompletionRefused):
            mc.complete_task(
                task_id,
                session_id="s1",
                evidence_id=second_evidence_id,
                completion_summary="tests written again",
                db_path=self.db_path,
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
        parsed = mc._parse_memory_file(path)
        self.assertEqual("Guard blocks done", parsed["title"])
        self.assertEqual("Body text here.", parsed["body"])
        self.assertEqual(0.5, parsed["confidence"])  # no confidence field -> default

    def test_parse_memory_file_without_frontmatter_uses_filename(self) -> None:
        path = Path(self.tmp_dir.name) / "plain-note.md"
        path.write_text("Just some body text.\n", encoding="utf-8")
        parsed = mc._parse_memory_file(path)
        self.assertEqual("plain-note", parsed["title"])
        self.assertEqual("Just some body text.", parsed["body"])
        self.assertEqual(0.5, parsed["confidence"])
        # Stage AM: no frontmatter at all -> every new field is the default too.
        self.assertEqual("lesson", parsed["doc_type"])
        self.assertEqual("global", parsed["project"])
        self.assertEqual("unknown", parsed["evidence_level"])
        self.assertEqual("current", parsed["currentness"])

    def test_parse_memory_file_maps_confidence_labels(self) -> None:
        cases = {"high": 0.9, "medium": 0.5, "low": 0.2}
        for label, expected in cases.items():
            path = Path(self.tmp_dir.name) / f"{label}.md"
            path.write_text(
                f"---\ntitle: Note\nconfidence: {label}\n---\n\nBody.\n", encoding="utf-8"
            )
            parsed = mc._parse_memory_file(path)
            self.assertEqual(expected, parsed["confidence"])

    def test_parse_memory_file_unrecognized_confidence_defaults_to_medium(self) -> None:
        path = Path(self.tmp_dir.name) / "weird.md"
        path.write_text(
            "---\ntitle: Note\nconfidence: extremely-sure\n---\n\nBody.\n", encoding="utf-8"
        )
        parsed = mc._parse_memory_file(path)
        self.assertEqual(0.5, parsed["confidence"])

    def test_parse_memory_file_reads_stage_am_fields(self) -> None:
        path = Path(self.tmp_dir.name) / "rich.md"
        path.write_text(
            "---\ntitle: Rich note\ntype: feedback\nproject: bbb-lite\n"
            "evidence_level: verified\ncurrentness: historical\n---\n\nBody.\n",
            encoding="utf-8",
        )
        parsed = mc._parse_memory_file(path)
        self.assertEqual("feedback", parsed["doc_type"])
        self.assertEqual("bbb-lite", parsed["project"])
        self.assertEqual("verified", parsed["evidence_level"])
        self.assertEqual("historical", parsed["currentness"])

    def test_parse_memory_file_unrecognized_stage_am_fields_fall_back_to_defaults(
        self,
    ) -> None:
        path = Path(self.tmp_dir.name) / "typo.md"
        path.write_text(
            "---\ntitle: Note\ntype: not-a-real-type\nevidence_level: super-sure\n"
            "currentness: idk\n---\n\nBody.\n",
            encoding="utf-8",
        )
        parsed = mc._parse_memory_file(path)
        self.assertEqual("lesson", parsed["doc_type"])
        self.assertEqual("unknown", parsed["evidence_level"])
        self.assertEqual("current", parsed["currentness"])


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

    def test_index_memory_files_populates_graph_nodes_and_edges(self) -> None:
        self._write_note(
            "guard.md",
            "Guard note",
            "The guard blocks raw done writes and links to [[Mission Control]].",
        )
        mc.index_memory_files(db_path=self.db_path)
        nodes = {row["node_id"]: row for row in mc.list_graph_nodes(db_path=self.db_path)}
        edges = mc.list_graph_edges(db_path=self.db_path)
        self.assertIn("doc:guard-md", nodes)
        self.assertIn("concept:guard", nodes)
        self.assertIn("concept:mission-control", nodes)
        self.assertIn("mentions", {row["type"] for row in edges})
        self.assertIn("links-to", {row["type"] for row in edges})

    def test_graph_memory_search_returns_related_nodes(self) -> None:
        self._write_note("guard.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        results = mc.graph_memory_search("guard", db_path=self.db_path)
        self.assertEqual("guard.md", results[0]["source_file"])
        self.assertTrue(results[0]["related"])
        self.assertEqual("doc:guard-md", results[0]["node_id"])

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

    def test_index_memory_files_stores_confidence_from_frontmatter(self) -> None:
        (self.memory_dir / "sure.md").write_text(
            "---\ntitle: Sure thing\nconfidence: high\n---\n\nBody.\n", encoding="utf-8"
        )
        mc.index_memory_files(db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT confidence FROM memory_index WHERE source_file = 'sure.md'"
            ).fetchone()
        self.assertEqual(0.9, row["confidence"])

    def test_index_memory_files_stores_stage_am_fields(self) -> None:
        (self.memory_dir / "tagged.md").write_text(
            "---\ntitle: Tagged note\ntype: feedback\nproject: bbb-lite\n"
            "evidence_level: verified\ncurrentness: historical\n---\n\nBody.\n",
            encoding="utf-8",
        )
        mc.index_memory_files(db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT doc_type, project, evidence_level, currentness "
                "FROM memory_index WHERE source_file = 'tagged.md'"
            ).fetchone()
        self.assertEqual("feedback", row["doc_type"])
        self.assertEqual("bbb-lite", row["project"])
        self.assertEqual("verified", row["evidence_level"])
        self.assertEqual("historical", row["currentness"])

    def test_index_memory_files_defaults_stage_am_fields_without_frontmatter(self) -> None:
        self._write_note("plain.md", "Plain note", "No special fields here.")
        mc.index_memory_files(db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT doc_type, project, evidence_level, currentness "
                "FROM memory_index WHERE source_file = 'plain.md'"
            ).fetchone()
        self.assertEqual("lesson", row["doc_type"])
        self.assertEqual("global", row["project"])
        self.assertEqual("unknown", row["evidence_level"])
        self.assertEqual("current", row["currentness"])

    def _write_project_note(self, filename: str, title: str, project: str, body: str) -> None:
        (self.memory_dir / filename).write_text(
            f"---\ntitle: {title}\nproject: {project}\n---\n\n{body}\n", encoding="utf-8"
        )

    def test_search_memory_project_filter_only_returns_that_project(self) -> None:
        self._write_project_note("a.md", "Widget note", "project-a", "The widget is great.")
        self._write_project_note("b.md", "Widget note", "project-b", "The widget is great.")
        mc.index_memory_files(db_path=self.db_path)

        results = mc.search_memory("widget", project="project-a", db_path=self.db_path)

        self.assertEqual(1, len(results))
        self.assertEqual("a.md", results[0]["source_file"])

    def test_search_memory_without_project_returns_all(self) -> None:
        self._write_project_note("a.md", "Widget note", "project-a", "The widget is great.")
        self._write_project_note("b.md", "Widget note", "project-b", "The widget is great.")
        mc.index_memory_files(db_path=self.db_path)

        results = mc.search_memory("widget", db_path=self.db_path)

        self.assertEqual(
            {"a.md", "b.md"}, {hit["source_file"] for hit in results}
        )

    def test_semantic_search_project_filter_only_returns_that_project(self) -> None:
        self._write_project_note(
            "a.md", "Widget note", "project-a", "The widget behaves oddly under load."
        )
        self._write_project_note(
            "b.md", "Widget note", "project-b", "The widget behaves oddly under load."
        )
        mc.index_memory_files(db_path=self.db_path)

        results = mc.semantic_search(
            "widget behaving oddly", project="project-b", db_path=self.db_path
        )

        self.assertEqual(1, len(results))
        self.assertEqual("b.md", results[0]["source_file"])

    def test_search_memory_ranks_higher_confidence_above_equal_keyword_score(self) -> None:
        (self.memory_dir / "shaky.md").write_text(
            "---\ntitle: Widget notes\nconfidence: low\n---\n\n"
            "The widget behaves oddly under load.\n",
            encoding="utf-8",
        )
        (self.memory_dir / "solid.md").write_text(
            "---\ntitle: Widget notes\nconfidence: high\n---\n\n"
            "The widget behaves oddly under load.\n",
            encoding="utf-8",
        )
        mc.index_memory_files(db_path=self.db_path)
        results = mc.search_memory("widget", db_path=self.db_path)
        self.assertEqual("solid.md", results[0]["source_file"])
        self.assertEqual("shaky.md", results[1]["source_file"])

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
        # Underscore vs space: same filename slug ("same-title"), but different
        # normalize_term() output ("same_title" vs "same title" — underscore is a \w
        # char so it isn't split on) — so this isn't caught by the Stage AR exact-match
        # check, and still needs the suffix logic below it.
        first = mc.log_lesson("Same_title", "First body.", db_path=self.db_path)
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

    def test_retire_low_confidence_lessons_archives_below_threshold(self) -> None:
        (self.memory_dir / "shaky.md").write_text(
            "---\ntitle: Shaky lesson\nconfidence: 0.1\n---\n\nProbably wrong.\n",
            encoding="utf-8",
        )
        (self.memory_dir / "solid.md").write_text(
            "---\ntitle: Solid lesson\nconfidence: high\n---\n\nStill true.\n",
            encoding="utf-8",
        )
        mc.index_memory_files(db_path=self.db_path)

        retired_count = mc.retire_low_confidence_lessons(threshold=0.3, db_path=self.db_path)

        self.assertEqual(1, retired_count)
        archived_dir = self.memory_dir / "_archived-healed"
        self.assertTrue((archived_dir / "shaky.md").exists())
        self.assertFalse((self.memory_dir / "shaky.md").exists())
        self.assertTrue((self.memory_dir / "solid.md").exists())

        with contextlib.closing(mc._connect(self.db_path)) as conn:
            remaining = {
                row["source_file"]
                for row in conn.execute("SELECT source_file FROM memory_index").fetchall()
            }
        self.assertNotIn("shaky.md", remaining)
        self.assertIn("solid.md", remaining)

    def test_retire_low_confidence_lessons_keeps_at_or_above_threshold(self) -> None:
        (self.memory_dir / "borderline.md").write_text(
            "---\ntitle: Borderline lesson\nconfidence: 0.3\n---\n\nStill on the fence.\n",
            encoding="utf-8",
        )
        mc.index_memory_files(db_path=self.db_path)

        retired_count = mc.retire_low_confidence_lessons(threshold=0.3, db_path=self.db_path)

        self.assertEqual(0, retired_count)
        self.assertTrue((self.memory_dir / "borderline.md").exists())

    def test_retire_low_confidence_lessons_exempts_structural_files(self) -> None:
        (self.memory_dir / "MEMORY.md").write_text("Index content.\n", encoding="utf-8")
        (self.memory_dir / "decisions.md").write_text("Decision log.\n", encoding="utf-8")
        mc.index_memory_files(db_path=self.db_path)

        retired_count = mc.retire_low_confidence_lessons(threshold=0.9, db_path=self.db_path)

        self.assertEqual(0, retired_count)
        self.assertTrue((self.memory_dir / "MEMORY.md").exists())
        self.assertTrue((self.memory_dir / "decisions.md").exists())


class StageARTermMatchingTestCase(unittest.TestCase):
    """Stage AR: normalize_term / term_aliases / the 3 cheap checks in log_lesson().

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

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_normalize_term_lowercases_and_strips_punctuation(self) -> None:
        self.assertEqual("simple moving average", mc.normalize_term("Simple, Moving-Average!"))

    def test_normalize_term_collapses_whitespace(self) -> None:
        self.assertEqual("a b", mc.normalize_term("  a    b  "))

    def test_normalize_term_treats_smas_and_sma_as_equal(self) -> None:
        self.assertEqual(mc.normalize_term("sma"), mc.normalize_term("SMAs"))

    def test_normalize_term_does_not_strip_short_or_double_s_words(self) -> None:
        # len <= 3 ("as") and a trailing "ss" ("class") are left alone by the heuristic.
        self.assertEqual("as", mc.normalize_term("as"))
        self.assertEqual("class", mc.normalize_term("class"))

    def test_add_alias_returns_id_and_is_retrievable_via_match(self) -> None:
        alias_id = mc.add_alias("Simple Moving Average", "SMA", db_path=self.db_path)
        self.assertIsInstance(alias_id, int)
        self.assertEqual(
            "Simple Moving Average", mc._match_alias("The SMA crossed", db_path=self.db_path)
        )

    def test_log_lesson_exact_duplicate_skipped_without_asking_ollama(self) -> None:
        mc.log_lesson("Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama") as mocked:
            result = mc.log_lesson(
                "guard   blocks, DONE!!",  # same fact after normalize_term, different spelling
                "Some unrelated body text.",
                db_path=self.db_path,
            )
        self.assertIsNone(result)
        mocked.assert_not_called()
        self.assertEqual(1, len(list(self.memory_dir.glob("lesson-*.md"))))

    def test_log_lesson_alias_match_expands_title_without_asking_ollama(self) -> None:
        mc.add_alias("Simple Moving Average", "SMA", db_path=self.db_path)
        with mock.patch("mc.ask_ollama") as mocked:
            result = mc.log_lesson(
                "SMA crossover strategy",
                "Buy when the fast SMA crosses above the slow one.",
                db_path=self.db_path,
            )
        self.assertIsNotNone(result)
        mocked.assert_not_called()
        content = result.read_text(encoding="utf-8")
        self.assertIn("SMA crossover strategy (Simple Moving Average)", content)

    def test_log_lesson_falls_back_to_semantic_check_when_no_alias_or_exact_match(self) -> None:
        # Different enough from "Guard blocks done" to dodge the exact-match check, but
        # sharing enough keywords (guard/done/refuses/writes) that search_memory() still
        # surfaces it as the nearest candidate, so the ask_ollama() branch actually runs.
        mc.log_lesson("Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama", return_value="no") as mocked:
            result = mc.log_lesson(
                "Guard prevents done finalization",
                "The guard refuses raw done writes forcibly.",
                db_path=self.db_path,
            )
        self.assertIsNotNone(result)
        mocked.assert_called_once()


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

    def test_set_role_then_get_role_roundtrips_for_claude_code_style_session_id(self) -> None:
        fake_session_id = "a3066b4d-9d92-4700-8806-8020398a57c5"
        mc.set_role(fake_session_id, "builder", db_path=self.db_path)
        self.assertEqual("builder", mc.get_role(fake_session_id, db_path=self.db_path))

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


def _passing_evidence_id(db_path: Path) -> int:
    """A minimal non-'fail' evidence row, for tests that just need complete_task() to pass."""

    return mc.record_evidence("test", "stage-ae fixture", "passed", db_path=db_path)


def _seed_20_tracked_items(db_path: Path, session_id: str) -> None:
    """5 open tasks + 10 decisions + role declared (1) + 4 checklist items = 20."""

    for i in range(5):
        mc.add_task(f"task {i}", status="in_progress", db_path=db_path)
    for i in range(10):
        mc.add_decision(
            f"decision {i}", session_id=session_id, db_path=db_path,
            md_path=db_path.parent / f"{db_path.stem}-decisions.md",
        )
    mc.set_role(session_id, "builder", db_path=db_path)
    mc.write_checkpoint(
        session_id, open_queue=json.dumps(["a", "b", "c", "d"]), db_path=db_path,
    )


class StageRTestCase(unittest.TestCase):
    """Stage R: decisions table, compact_snapshot/save_snapshot/compare_snapshots.

    NOT fail-soft on purpose (unlike most of this project) — a missing
    snapshot must surface as a finding, never pass silently. See
    memory/decisions.md, 2026-07-27.
    """

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.md_path = self.db_path.parent / f"{self.db_path.stem}-decisions.md"
        self.session_id = "s-stage-r"
        mc.ensure_session(self.session_id, db_path=self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)
        self.md_path.unlink(missing_ok=True)

    def test_add_decision_writes_row_and_md_file(self) -> None:
        mc.add_decision(
            "chose SQLite", session_id=self.session_id,
            db_path=self.db_path, md_path=self.md_path,
        )
        rows = mc.list_decisions(self.session_id, db_path=self.db_path)
        self.assertEqual(1, len(rows))
        self.assertEqual("chose SQLite", rows[0]["text"])
        self.assertIn("chose SQLite", self.md_path.read_text(encoding="utf-8"))

    def test_compact_snapshot_counts_20_tracked_items(self) -> None:
        _seed_20_tracked_items(self.db_path, self.session_id)
        snapshot = mc.compact_snapshot(self.session_id, db_path=self.db_path)
        total = (
            len(snapshot["tasks"]) + snapshot["decisions"]
            + snapshot["checklist_items"] + (1 if snapshot["role_declared"] else 0)
        )
        self.assertEqual(20, total)
        self.assertTrue(snapshot["role_declared"])

    def test_save_snapshot_rejects_unknown_phase(self) -> None:
        with self.assertRaises(ValueError):
            mc.save_snapshot(self.session_id, "during", db_path=self.db_path)

    def test_compare_snapshots_20_of_20_no_loss(self) -> None:
        _seed_20_tracked_items(self.db_path, self.session_id)
        mc.save_snapshot(self.session_id, "pre", db_path=self.db_path)
        mc.save_snapshot(self.session_id, "post", db_path=self.db_path)
        self.assertEqual([], mc.compare_snapshots(self.session_id, db_path=self.db_path))

    def test_compare_snapshots_detects_one_missing_task(self) -> None:
        _seed_20_tracked_items(self.db_path, self.session_id)
        task_ids = [t["id"] for t in mc.list_open_tasks(db_path=self.db_path)]
        mc.save_snapshot(self.session_id, "pre", db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            task_ids[0],
            session_id=self.session_id,
            evidence_id=evidence_id,
            completion_summary="task closed for snapshot test",
            db_path=self.db_path,
        )  # the "loss"
        mc.save_snapshot(self.session_id, "post", db_path=self.db_path)
        findings = mc.compare_snapshots(self.session_id, db_path=self.db_path)
        self.assertEqual(1, len(findings))
        self.assertIn("tasks: 5 -> 4", findings[0])
        self.assertIn(str(task_ids[0]), findings[0])

    def test_compare_snapshots_missing_pre_snapshot_is_itself_a_finding(self) -> None:
        mc.save_snapshot(self.session_id, "post", db_path=self.db_path)
        findings = mc.compare_snapshots(self.session_id, db_path=self.db_path)
        self.assertEqual(1, len(findings))
        self.assertIn("no 'pre'", findings[0])

    def test_mark_session_degraded_sets_status(self) -> None:
        mc.mark_session_degraded(self.session_id, db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            status = conn.execute(
                "SELECT status FROM sessions WHERE session_id = ?", (self.session_id,)
            ).fetchone()[0]
        self.assertEqual("degraded", status)


class StageACTestCase(McTestCase):
    """Stage AC: context-map merged into graph_nodes/graph_edges (was Stage
    Z's own context_nodes/context_edges — see decisions.md)."""

    def test_add_curated_edge_refuses_when_a_node_is_still_proposed(self) -> None:
        idea = mc.propose_node("idea", "use SQLite", db_path=self.db_path)
        decision = mc.propose_node("decision", "chose SQLite", db_path=self.db_path)
        mc.approve_node(decision, db_path=self.db_path)  # idea stays proposed
        with self.assertRaises(ValueError):
            mc.add_curated_edge(idea, decision, "supports", db_path=self.db_path)

    def test_add_curated_edge_succeeds_once_both_nodes_are_approved(self) -> None:
        idea = mc.propose_node("idea", "use SQLite", db_path=self.db_path)
        decision = mc.propose_node("decision", "chose SQLite", db_path=self.db_path)
        mc.approve_node(idea, db_path=self.db_path)
        mc.approve_node(decision, db_path=self.db_path)
        mc.add_curated_edge(idea, decision, "supports", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT curated, requires_dual_approval FROM graph_edges "
                "WHERE src = ? AND dst = ? AND type = ?",
                (idea, decision, "supports"),
            ).fetchone()
        self.assertEqual(1, row["curated"])
        self.assertEqual(1, row["requires_dual_approval"])

    def test_list_context_map_only_returns_approved_nodes_by_default(self) -> None:
        approved = mc.propose_node("project", "bbb-lite", db_path=self.db_path)
        proposed = mc.propose_node("idea", "unreviewed idea", db_path=self.db_path)
        mc.approve_node(approved, db_path=self.db_path)
        result = mc.list_context_map(db_path=self.db_path)
        node_ids = {node["node_id"] for node in result["nodes"]}
        self.assertIn(approved, node_ids)
        self.assertNotIn(proposed, node_ids)

    def test_list_context_map_only_approved_false_returns_all_nodes(self) -> None:
        approved = mc.propose_node("project", "bbb-lite", db_path=self.db_path)
        proposed = mc.propose_node("idea", "unreviewed idea", db_path=self.db_path)
        mc.approve_node(approved, db_path=self.db_path)
        result = mc.list_context_map(only_approved=False, db_path=self.db_path)
        node_ids = {node["node_id"] for node in result["nodes"]}
        self.assertEqual({approved, proposed}, node_ids)

    def test_list_context_map_includes_approved_edges(self) -> None:
        idea = mc.propose_node("idea", "use SQLite", db_path=self.db_path)
        decision = mc.propose_node("decision", "chose SQLite", db_path=self.db_path)
        mc.approve_node(idea, db_path=self.db_path)
        mc.approve_node(decision, db_path=self.db_path)
        mc.add_curated_edge(idea, decision, "supports", db_path=self.db_path)
        result = mc.list_context_map(db_path=self.db_path)
        self.assertEqual(1, len(result["edges"]))
        self.assertEqual("supports", result["edges"][0]["type"])

    def test_list_context_map_excludes_edges_only_advisory_curated(self) -> None:
        """curate_graph_edge() (Stage S, advisory) must not leak into the
        context map — only add_curated_edge()'s dual-approval gate can."""
        idea = mc.propose_node("idea", "use SQLite", db_path=self.db_path)
        decision = mc.propose_node("decision", "chose SQLite", db_path=self.db_path)
        mc.approve_node(idea, db_path=self.db_path)
        mc.approve_node(decision, db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            mc._upsert_graph_edge(conn, idea, decision, "mentions")
            conn.commit()
        mc.curate_graph_edge(idea, decision, "mentions", db_path=self.db_path)
        result = mc.list_context_map(db_path=self.db_path)
        self.assertEqual([], result["edges"])

    def test_query_rows_rejects_non_select(self) -> None:
        with self.assertRaises(ValueError):
            mc.query_rows("UPDATE tasks SET status = 'done'", db_path=self.db_path)

    def test_query_rows_returns_dicts_for_select(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO tasks (title, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ("write tests", "open", "2026-07-29T00:00:00+00:00", "2026-07-29T00:00:00+00:00"),
            )
            conn.commit()
        rows = mc.query_rows("SELECT title, status FROM tasks", db_path=self.db_path)
        self.assertEqual([{"title": "write tests", "status": "open"}], rows)

    def test_readback_verify_returns_existing_row(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            cur = conn.execute(
                "INSERT INTO tasks (title, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ("ship it", "open", "2026-07-29T00:00:00+00:00", "2026-07-29T00:00:00+00:00"),
            )
            task_id = cur.lastrowid
            conn.commit()
        row = mc.readback_verify("tasks", task_id, db_path=self.db_path)
        self.assertEqual("ship it", row["title"])

    def test_readback_verify_raises_mcdegraded_for_missing_row(self) -> None:
        with self.assertRaises(mc.MCDegraded):
            mc.readback_verify("tasks", 999999, db_path=self.db_path)

    def test_write_gate_true_for_known_operations(self) -> None:
        self.assertTrue(mc.write_gate("complete_task"))
        self.assertTrue(mc.write_gate("add_curated_edge"))
        self.assertTrue(mc.write_gate("mark_session_degraded"))
        self.assertFalse(mc.write_gate("delete_everything"))

    def test_complete_task_then_readback_verify(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            cur = conn.execute(
                "INSERT INTO tasks (title, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    "finish MC seam", "in_progress",
                    "2026-07-29T00:00:00+00:00", "2026-07-29T00:00:00+00:00",
                ),
            )
            task_id = cur.lastrowid
            conn.commit()
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            task_id,
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="MC seam finished",
            db_path=self.db_path,
        )
        row = mc.readback_verify("tasks", task_id, db_path=self.db_path)
        self.assertEqual("done", row["status"])


class StageAQDbHealthCheckTestCase(McTestCase):
    def test_reachable_when_db_exists(self) -> None:
        result = mc.db_health_check(self.db_path)
        self.assertEqual(
            {"reachable": True, "mode": "local-only", "path": str(self.db_path)},
            result,
        )

    def test_unreachable_when_db_missing(self) -> None:
        missing_path = self.db_path.parent / "does-not-exist.db"
        result = mc.db_health_check(missing_path)
        self.assertEqual(
            {"reachable": False, "mode": "local-only", "path": str(missing_path)},
            result,
        )


class RemindersTestCase(McTestCase):
    """Dated reminders (Stage BUS): tasks.due_date + person-node dedup +
    'involves' edges — the real version of Haroon's Muayyad meeting example."""

    def test_get_or_create_person_node_dedups_same_name(self) -> None:
        first = mc.get_or_create_person_node("Haroon", db_path=self.db_path)
        second = mc.get_or_create_person_node("Haroon", db_path=self.db_path)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["node_id"], second["node_id"])

    def test_get_or_create_person_node_dedups_case_and_whitespace(self) -> None:
        first = mc.get_or_create_person_node("Haroon", db_path=self.db_path)
        second = mc.get_or_create_person_node(" haroon ", db_path=self.db_path)
        self.assertFalse(second["created"])
        self.assertEqual(first["node_id"], second["node_id"])

    def test_get_or_create_person_node_new_name_is_created(self) -> None:
        result = mc.get_or_create_person_node("Muayyad", db_path=self.db_path)
        self.assertTrue(result["created"])
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT node_type, title FROM graph_nodes WHERE node_id = ?",
                (result["node_id"],),
            ).fetchone()
        self.assertEqual("person", row["node_type"])
        self.assertEqual("Muayyad", row["title"])

    def test_create_reminder_sets_due_date_on_task(self) -> None:
        result = mc.create_reminder(
            "follow up with Muayyad", "2026-08-10", ["Muayyad"], db_path=self.db_path
        )
        task = next(t for t in mc.list_tasks(db_path=self.db_path) if t["id"] == result["task_id"])
        self.assertEqual("2026-08-10", task["due_date"])

    def test_create_reminder_erdi_exists_haroon_is_new(self) -> None:
        mc.get_or_create_person_node("Erdi", db_path=self.db_path)
        result = mc.create_reminder(
            "sync with Erdi and Haroon", "2026-08-10", ["Erdi", "Haroon"], db_path=self.db_path,
        )
        self.assertFalse(result["people"]["Erdi"]["created"])
        self.assertTrue(result["people"]["Haroon"]["created"])

    def test_create_reminder_adds_involves_edge_per_person(self) -> None:
        result = mc.create_reminder("plan trip", "2026-08-10", ["Erdi"], db_path=self.db_path)
        task_node_id = mc._graph_node_id("task", str(result["task_id"]))
        person_node_id = result["people"]["Erdi"]["node_id"]
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT type FROM graph_edges WHERE src = ? AND dst = ?",
                (task_node_id, person_node_id),
            ).fetchone()
        self.assertEqual("involves", row["type"])


class EntityDedupTestCase(McTestCase):
    """Entity dedup (Stage BUS follow-up): get_or_create_entity_node()
    generalizes get_or_create_person_node() past "person", and
    link_entities() connects two entities with a 'relates-to' edge — the
    "Genie the company + its client" example from Haroon's meeting."""

    def test_get_or_create_person_node_still_dedups_same_name(self) -> None:
        first = mc.get_or_create_person_node("Haroon", db_path=self.db_path)
        second = mc.get_or_create_person_node("Haroon", db_path=self.db_path)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["node_id"], second["node_id"])
        self.assertEqual("person:haroon", first["node_id"])

    def test_get_or_create_entity_node_dedups_same_name_same_type(self) -> None:
        first = mc.get_or_create_entity_node("Genie", "organization", db_path=self.db_path)
        second = mc.get_or_create_entity_node("Genie", "organization", db_path=self.db_path)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["node_id"], second["node_id"])

    def test_get_or_create_entity_node_different_types_do_not_collide(self) -> None:
        person = mc.get_or_create_entity_node("Genie", "person", db_path=self.db_path)
        org = mc.get_or_create_entity_node("Genie", "organization", db_path=self.db_path)
        self.assertTrue(person["created"])
        self.assertTrue(org["created"])
        self.assertNotEqual(person["node_id"], org["node_id"])
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT node_id, node_type FROM graph_nodes WHERE title = 'Genie' ORDER BY node_type"
            ).fetchall()
        self.assertEqual(2, len(rows))
        self.assertEqual(["organization", "person"], [r["node_type"] for r in rows])

    def test_get_or_create_entity_node_writes_correct_node_type(self) -> None:
        result = mc.get_or_create_entity_node("Acme Client", "client", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT node_type, title FROM graph_nodes WHERE node_id = ?",
                (result["node_id"],),
            ).fetchone()
        self.assertEqual("client", row["node_type"])
        self.assertEqual("Acme Client", row["title"])

    def test_link_entities_creates_relates_to_edge_with_relation_as_evidence(self) -> None:
        result = mc.link_entities(
            "Genie", "works-with", "Erdi", type_a="organization", type_b="person",
            db_path=self.db_path,
        )
        self.assertTrue(result["edge_created"])
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT type, evidence FROM graph_edges WHERE src = ? AND dst = ?",
                (result["a"]["node_id"], result["b"]["node_id"]),
            ).fetchone()
        self.assertEqual("relates-to", row["type"])
        self.assertEqual("works-with", row["evidence"])

    def test_link_entities_dedups_entities_and_edge_on_second_call(self) -> None:
        first = mc.link_entities(
            "Genie", "works-with", "Erdi", type_a="organization", type_b="person",
            db_path=self.db_path,
        )
        second = mc.link_entities(
            "Genie", "works-with", "Erdi", type_a="organization", type_b="person",
            db_path=self.db_path,
        )
        self.assertTrue(first["a"]["created"])
        self.assertFalse(second["a"]["created"])
        self.assertFalse(second["b"]["created"])
        self.assertFalse(second["edge_created"])
        self.assertEqual(first["a"]["node_id"], second["a"]["node_id"])

    def test_create_reminder_does_not_duplicate_person_across_reminders(self) -> None:
        first = mc.create_reminder("reminder one", "2026-08-10", ["Muayyad"], db_path=self.db_path)
        second = mc.create_reminder("reminder two", "2026-08-11", ["Muayyad"], db_path=self.db_path)
        self.assertTrue(first["people"]["Muayyad"]["created"])
        self.assertFalse(second["people"]["Muayyad"]["created"])
        self.assertEqual(
            first["people"]["Muayyad"]["node_id"], second["people"]["Muayyad"]["node_id"],
        )

    def test_list_upcoming_reminders_within_window(self) -> None:
        today = datetime.now(timezone.utc).date()
        mc.create_reminder(
            "due soon", (today + timedelta(days=2)).isoformat(), [], db_path=self.db_path
        )
        mc.create_reminder(
            "due later", (today + timedelta(days=30)).isoformat(), [], db_path=self.db_path
        )
        mc.add_task("no due date", db_path=self.db_path)
        titles = {
            t["title"] for t in mc.list_upcoming_reminders(within_days=7, db_path=self.db_path)
        }
        self.assertEqual({"due soon"}, titles)

    def test_list_upcoming_reminders_includes_people(self) -> None:
        today = datetime.now(timezone.utc).date()
        mc.create_reminder(
            "call Muayyad",
            (today + timedelta(days=2)).isoformat(),
            ["Muayyad", "Erdi"],
            db_path=self.db_path,
        )
        mc.add_task(
            "plain due-date task, no create_reminder",
            due_date=(today + timedelta(days=3)).isoformat(),
            db_path=self.db_path,
        )
        by_title = {
            r["title"]: r for r in mc.list_upcoming_reminders(within_days=7, db_path=self.db_path)
        }
        self.assertEqual({"Erdi", "Muayyad"}, set(by_title["call Muayyad"]["people"]))
        self.assertEqual([], by_title["plain due-date task, no create_reminder"]["people"])

    def test_list_upcoming_reminders_excludes_overdue_and_done(self) -> None:
        today = datetime.now(timezone.utc).date()
        mc.create_reminder(
            "overdue", (today - timedelta(days=1)).isoformat(), [], db_path=self.db_path
        )
        done = mc.create_reminder(
            "already done", (today + timedelta(days=1)).isoformat(), [], db_path=self.db_path
        )
        mc.start_task(done["task_id"], db_path=self.db_path)
        evidence_id = _passing_evidence_id(self.db_path)
        mc.complete_task(
            done["task_id"],
            session_id="s1",
            evidence_id=evidence_id,
            completion_summary="handled",
            db_path=self.db_path,
        )
        titles = {
            t["title"] for t in mc.list_upcoming_reminders(within_days=7, db_path=self.db_path)
        }
        self.assertEqual(set(), titles)


if __name__ == "__main__":
    unittest.main()
