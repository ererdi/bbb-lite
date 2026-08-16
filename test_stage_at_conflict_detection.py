"""Tests for Stage AT: conflict DETECTION (Haroon's team doc item 7).

detect_memory_conflicts() / detect_decision_conflicts() only surface
candidate pairs — nothing here auto-resolves anything. "Local vs server" is
out of scope (no server in this project, see decisions.md).

Same isolation approach as test_memory_healing.py / test_session_start_memory.py:
mc.__file__ is patched into a temp project dir for the memory-based tests
(so memory_dir never touches the real memory/ directory), and those tests
skip if the embedding model is unavailable — detect_memory_conflicts() is
built on semantic_search(), which needs real embeddings to score similarity.
detect_decision_conflicts() is plain-text word overlap, so its tests don't
need the embedding model at all.
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

import mc

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import session_start  # noqa: E402 - path insert must run first


class MemoryConflictTestCase(unittest.TestCase):
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

        try:
            mc._get_embedding_model()
        except Exception as exc:  # noqa: BLE001 - can't test this without the model
            self.skipTest(f"embedding model unavailable (no network and no local cache?): {exc}")

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _write_note(self, filename: str, title: str, body: str, currentness: str) -> None:
        (self.memory_dir / filename).write_text(
            f"---\ntitle: {title}\ncurrentness: {currentness}\n---\n\n{body}\n",
            encoding="utf-8",
        )

    def test_detect_memory_conflicts_finds_pair_with_different_currentness(self) -> None:
        self._write_note(
            "old.md", "Deploy retries", "The deploy script retries three times before giving up.",
            "superseded",
        )
        self._write_note(
            "new.md", "Deploy retries updated",
            "The deploy script retries three times before it gives up.",
            "current",
        )
        mc.index_memory_files(db_path=self.db_path)

        conflicts = mc.detect_memory_conflicts(
            "the deploy script retries before giving up", db_path=self.db_path
        )

        self.assertEqual(1, len(conflicts))
        source_files = {conflicts[0]["a"]["source_file"], conflicts[0]["b"]["source_file"]}
        self.assertEqual({"old.md", "new.md"}, source_files)
        self.assertIn("currentness", conflicts[0]["reason"])

    def test_detect_memory_conflicts_empty_when_currentness_matches(self) -> None:
        self._write_note(
            "old.md", "Deploy retries", "The deploy script retries three times before giving up.",
            "current",
        )
        self._write_note(
            "new.md", "Deploy retries updated",
            "The deploy script retries three times before it gives up.",
            "current",
        )
        mc.index_memory_files(db_path=self.db_path)

        conflicts = mc.detect_memory_conflicts(
            "the deploy script retries before giving up", db_path=self.db_path
        )
        self.assertEqual([], conflicts)

    def test_recovery_package_size_excludes_replaced_notes(self) -> None:
        session_id = "s-stage-at-recovery"
        mc.ensure_session(session_id, db_path=self.db_path)
        mc.write_checkpoint(
            session_id, current_task="the guard blocks unsafe writes", db_path=self.db_path
        )
        baseline = mc.recovery_package_size(session_id, db_path=self.db_path)

        self._write_note("guard.md", "Guard note", "the guard blocks unsafe writes", "current")
        mc.index_memory_files(db_path=self.db_path)
        with_note = mc.recovery_package_size(session_id, db_path=self.db_path)
        self.assertGreater(with_note, baseline)

        mc.mark_memory_replaced("guard.md", "other.md", db_path=self.db_path)
        after_replace = mc.recovery_package_size(session_id, db_path=self.db_path)
        self.assertEqual(baseline, after_replace)


class DecisionConflictTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.md_path = Path(tmp.name).with_suffix(".decisions.md")
        self.addCleanup(lambda: self.md_path.unlink(missing_ok=True))

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _add(self, text: str, session_id: str | None) -> None:
        mc.add_decision(text, session_id=session_id, db_path=self.db_path, md_path=self.md_path)

    def test_detect_decision_conflicts_flags_high_overlap_across_different_sessions(self) -> None:
        self._add("guard blocks writes always happening", session_id="s-1")
        self._add("guard blocks writes sometimes happening", session_id="s-2")

        conflicts = mc.detect_decision_conflicts(db_path=self.db_path)
        self.assertEqual(1, len(conflicts))

    def test_detect_decision_conflicts_skips_high_overlap_within_same_session(self) -> None:
        self._add("guard blocks writes always happening", session_id="s-1")
        self._add("guard blocks writes sometimes happening", session_id="s-1")

        conflicts = mc.detect_decision_conflicts(db_path=self.db_path)
        self.assertEqual([], conflicts)

    def test_detect_decision_conflicts_still_flags_when_session_id_is_none(self) -> None:
        self._add("guard blocks writes always happening", session_id=None)
        self._add("guard blocks writes sometimes happening", session_id=None)

        conflicts = mc.detect_decision_conflicts(db_path=self.db_path)
        self.assertEqual(1, len(conflicts))

    def test_detect_decision_conflicts_empty_when_no_word_overlap(self) -> None:
        self._add("guard blocks writes always happening", session_id="s-1")
        self._add("coffee tastes great this morning", session_id="s-2")

        conflicts = mc.detect_decision_conflicts(db_path=self.db_path)
        self.assertEqual([], conflicts)


_BOUND_FUNCS = (
    "ensure_session",
    "run_maintenance_checks",
    "record_lifecycle_run",
    "get_role",
    "can_write_code",
    "can_write_data",
    "list_open_tasks",
    "latest_checkpoint",
    "semantic_search",
    "detect_memory_conflicts",
    "list_sessions",
    "recent_decisions",
    "recovery_package_size",
)


def _bind_to_test_db(db_path: Path) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    for name in _BOUND_FUNCS:
        bound = functools.partial(getattr(mc, name), db_path=db_path)
        stack.enter_context(mock.patch(f"mc.{name}", bound))
    stack.enter_context(mock.patch("session_start.mc.init_db"))
    return stack


def _run_session_start(session_id: str, source: str = "startup") -> str:
    payload = json.dumps({"session_id": session_id, "source": source})
    buffer = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(buffer):
        session_start.main()
    return buffer.getvalue()


class SessionStartConflictWarningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.session_id = "s-stage-at"
        mc.ensure_session(self.session_id, db_path=self.db_path)

        self.project_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.project_dir.cleanup)
        self.memory_dir = Path(self.project_dir.name) / "memory"
        self.memory_dir.mkdir()
        patcher = mock.patch.object(mc, "__file__", str(Path(self.project_dir.name) / "mc.py"))
        patcher.start()
        self.addCleanup(patcher.stop)

        try:
            mc._get_embedding_model()
        except Exception as exc:  # noqa: BLE001 - can't test this without the model
            self.skipTest(f"embedding model unavailable (no network and no local cache?): {exc}")

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _write_note(self, filename: str, title: str, body: str, currentness: str) -> None:
        (self.memory_dir / filename).write_text(
            f"---\ntitle: {title}\ncurrentness: {currentness}\n---\n\n{body}\n",
            encoding="utf-8",
        )

    def test_session_start_prints_conflict_warning_when_conflicts_found(self) -> None:
        self._write_note(
            "old.md", "Deploy retries", "The deploy script retries three times before giving up.",
            "superseded",
        )
        self._write_note(
            "new.md", "Deploy retries updated",
            "The deploy script retries three times before it gives up.",
            "current",
        )
        mc.index_memory_files(db_path=self.db_path)
        mc.write_checkpoint(
            self.session_id,
            current_task="the deploy script retries before giving up",
            db_path=self.db_path,
        )

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)

        self.assertIn(
            "[bbb-lite] CONFLICT WARNING: found 1 conflicting memory record(s) for this "
            "topic - review before trusting",
            output,
        )

    def test_session_start_conflict_check_is_fail_soft(self) -> None:
        mc.write_checkpoint(
            self.session_id,
            current_task="the deploy script retries before giving up",
            db_path=self.db_path,
        )

        with _bind_to_test_db(self.db_path), \
            mock.patch("mc.detect_memory_conflicts", side_effect=RuntimeError("boom")):
            output = _run_session_start(self.session_id)  # must not raise

        self.assertNotIn("CONFLICT WARNING", output)
        self.assertIn(f"[bbb-lite] session {self.session_id}", output)


if __name__ == "__main__":
    unittest.main()
