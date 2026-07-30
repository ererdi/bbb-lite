"""End-to-end tests for Stage R: compact recovery validation across the two
hooks (hooks/pre_compact_safepoint.py, hooks/session_start.py) and mc.py.

Deliberately NOT fail-soft, unlike most of this project's hooks: a missing or
failed snapshot must surface visibly as 'degraded', never pass silently.
See memory/decisions.md, 2026-07-27.

mc's functions default to db_path=mc.DB_PATH, bound at definition time, so
patching mc.DB_PATH after import has no effect on hook code that calls them
with no db_path. _bind_to_test_db() replaces the functions themselves with
functools.partial wrappers pinned to a per-test temp db instead (same
approach as test_role_hooks.py).
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
import pre_compact_safepoint
import session_start


_BOUND_FUNCS = (
    "ensure_session",
    "record_lifecycle_run",
    "get_role",
    "can_write_code",
    "can_write_data",
    "list_open_tasks",
    "latest_checkpoint",
    "write_checkpoint",
    "save_snapshot",
    "compare_snapshots",
    "compact_snapshot",
    "mark_session_degraded",
)


def _bind_to_test_db(db_path: Path) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    for name in _BOUND_FUNCS:
        bound = functools.partial(getattr(mc, name), db_path=db_path)
        stack.enter_context(mock.patch(f"mc.{name}", bound))
    stack.enter_context(mock.patch("pre_compact_safepoint.mc.init_db"))
    stack.enter_context(mock.patch("session_start.mc.init_db"))
    return stack


def _seed_20_tracked_items(db_path: Path, session_id: str, md_path: Path) -> None:
    """5 open tasks + 10 decisions + role declared (1) + 4 checklist items = 20."""

    for i in range(5):
        mc.add_task(f"task {i}", db_path=db_path)
    for i in range(10):
        mc.add_decision(f"decision {i}", session_id=session_id, db_path=db_path, md_path=md_path)
    mc.set_role(session_id, "builder", db_path=db_path)
    mc.write_checkpoint(
        session_id, open_queue=json.dumps(["a", "b", "c", "d"]), db_path=db_path,
    )


def _run_pre_compact(session_id: str) -> None:
    payload = json.dumps({"session_id": session_id})
    with mock.patch("sys.stdin", io.StringIO(payload)):
        pre_compact_safepoint.main()


def _run_session_start_compact(session_id: str) -> str:
    payload = json.dumps({"session_id": session_id, "source": "compact"})
    buffer = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(buffer):
        session_start.main()
    return buffer.getvalue()


class CompactRecoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.md_path = self.db_path.parent / f"{self.db_path.stem}-decisions.md"
        self.session_id = "s-compact"
        mc.ensure_session(self.session_id, db_path=self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)
        self.md_path.unlink(missing_ok=True)

    def _session_status(self) -> str:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            return conn.execute(
                "SELECT status FROM sessions WHERE session_id = ?", (self.session_id,)
            ).fetchone()[0]

    def test_20_of_20_no_loss_prints_verified(self) -> None:
        _seed_20_tracked_items(self.db_path, self.session_id, self.md_path)
        with _bind_to_test_db(self.db_path):
            _run_pre_compact(self.session_id)
            output = _run_session_start_compact(self.session_id)
        self.assertIn("[bbb-lite] compact recovery verified: 20/20 items intact", output)
        self.assertNotIn("DEGRADED", output)
        self.assertEqual("active", self._session_status())

    def test_one_missing_task_is_detected_and_marks_degraded(self) -> None:
        _seed_20_tracked_items(self.db_path, self.session_id, self.md_path)
        task_ids = [t["id"] for t in mc.list_open_tasks(db_path=self.db_path)]
        with _bind_to_test_db(self.db_path):
            _run_pre_compact(self.session_id)
            evidence_id = mc.record_evidence(
                "test", "compact-recovery fixture", "passed", db_path=self.db_path
            )
            mc.complete_task(
                task_ids[0],
                session_id=self.session_id,
                evidence_id=evidence_id,
                completion_summary="task closed for compact-recovery test",
                db_path=self.db_path,
            )  # the "loss"
            output = _run_session_start_compact(self.session_id)
        self.assertIn("[bbb-lite] DEGRADED RECOVERY:", output)
        self.assertIn("tasks: 5 -> 4", output)
        self.assertIn(str(task_ids[0]), output)
        self.assertEqual("degraded", self._session_status())

    def test_snapshot_save_failure_marks_degraded_without_crashing(self) -> None:
        _seed_20_tracked_items(self.db_path, self.session_id, self.md_path)
        with _bind_to_test_db(self.db_path):
            _run_pre_compact(self.session_id)  # succeeds normally
            with mock.patch("mc.save_snapshot", side_effect=RuntimeError("disk full")):
                output = _run_session_start_compact(self.session_id)  # must not raise
        self.assertIn("[bbb-lite] DEGRADED RECOVERY: unable to verify compact recovery", output)
        self.assertEqual("degraded", self._session_status())


if __name__ == "__main__":
    unittest.main()
