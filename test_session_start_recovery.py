"""Tests for Stage AA: the rest of session_start.py's recovery package.

Covers the gaps left after Stage S (see Haroon team doc, item 2): risk,
recent decisions, confidence on related-memory hits, a degraded-recovery
warning, and a bounded-package size proof. Same db-binding approach as
test_session_start_memory.py: mc's functions default to db_path=mc.DB_PATH,
bound at definition time, so they're replaced with functools.partial
wrappers pinned to a temp db for the duration of each test.

current_task is left unset on all checkpoints here so the semantic_search
branch (embedding model, Stage S) never fires — these tests are about the
other lines Stage AA adds, not the related-memory nudge.
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
    "get_role",
    "can_write_code",
    "can_write_data",
    "list_open_tasks",
    "latest_checkpoint",
    "semantic_search",
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


class SessionStartRecoveryPackageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.session_id = "s-stage-aa"
        mc.ensure_session(self.session_id, db_path=self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_risk_printed_when_checkpoint_has_risk(self) -> None:
        mc.write_checkpoint(
            self.session_id, next_action="keep going", risk="schema drift possible", db_path=self.db_path
        )
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertIn("[bbb-lite] risk: schema drift possible", output)

    def test_no_risk_line_when_checkpoint_has_no_risk(self) -> None:
        mc.write_checkpoint(self.session_id, next_action="keep going", db_path=self.db_path)
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertNotIn("[bbb-lite] risk:", output)

    def test_recent_decisions_printed_newest_first(self) -> None:
        mc.add_decision("first decision", db_path=self.db_path, md_path=self.db_path.with_suffix(".md"))
        mc.add_decision("second decision", db_path=self.db_path, md_path=self.db_path.with_suffix(".md"))
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertIn("[bbb-lite] recent decision: second decision", output)
        self.assertIn("[bbb-lite] recent decision: first decision", output)
        self.assertLess(
            output.index("recent decision: second decision"),
            output.index("recent decision: first decision"),
        )

    def test_no_decision_lines_when_none_recorded(self) -> None:
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertNotIn("recent decision:", output)

    def test_degraded_warning_when_checkpoint_owner_is_degraded(self) -> None:
        other_session = "s-earlier-degraded"
        mc.ensure_session(other_session, db_path=self.db_path)
        mc.write_checkpoint(other_session, next_action="finish the thing", db_path=self.db_path)
        mc.mark_session_degraded(other_session, db_path=self.db_path)

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertIn(
            "[bbb-lite] WARNING: last session's recovery was degraded - some info may be missing",
            output,
        )

    def test_no_degraded_warning_when_checkpoint_owner_is_active(self) -> None:
        mc.write_checkpoint(self.session_id, next_action="finish the thing", db_path=self.db_path)
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertNotIn("WARNING", output)

    def test_recovery_package_size_is_small_and_printed(self) -> None:
        mc.write_checkpoint(
            self.session_id, next_action="keep going", risk="minor risk", db_path=self.db_path
        )
        mc.add_decision("a decision", db_path=self.db_path, md_path=self.db_path.with_suffix(".md"))

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
            size = mc.recovery_package_size(self.session_id)

        self.assertIn(f"[bbb-lite] recovery package: {size} chars", output)
        # "Bounded package", not a context dump: a handful of short lines,
        # not thousands of characters.
        self.assertGreater(size, 0)
        self.assertLess(size, 1000)


if __name__ == "__main__":
    unittest.main()
