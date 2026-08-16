"""Tests for Stage AF: role survives /clear.

/clear hands session_start a brand-new session_id with no row in `roles`.
Before this stage, get_role() would silently fall back to "brozzer" and the
declared role would be lost. Now session_start inherits the most recently
declared role (mc.get_most_recent_role) when source == "clear" and the new
session_id has no role of its own - and says so out loud, in both the
inherited and the no-previous-role case, rather than defaulting quietly.

Same db-binding approach as test_session_start_recovery.py: mc's functions
default to db_path=mc.DB_PATH, bound at definition time, so they're replaced
with functools.partial wrappers pinned to a temp db for the duration of each
test.
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
    "set_role",
    "get_role",
    "role_exists",
    "get_most_recent_role",
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


class RoleSurvivesClearTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_new_session_id_inherits_most_recent_role_after_clear(self) -> None:
        old_session = "s-before-clear"
        new_session = "s-after-clear"
        mc.ensure_session(old_session, db_path=self.db_path)
        mc.set_role(old_session, "builder", db_path=self.db_path)

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(new_session, source="clear")

        self.assertEqual("builder", mc.get_role(new_session, db_path=self.db_path))
        self.assertIn("[bbb-lite] role inherited after /clear: builder", output)
        self.assertIn("[bbb-lite] role: builder", output)

    def test_inherits_the_latest_of_several_declared_roles(self) -> None:
        s1, s2, new_session = "s1", "s2", "s-after-clear"
        mc.ensure_session(s1, db_path=self.db_path)
        mc.ensure_session(s2, db_path=self.db_path)
        mc.set_role(s1, "architect", db_path=self.db_path)
        mc.set_role(s2, "builder", db_path=self.db_path)  # declared later -> latest

        with _bind_to_test_db(self.db_path):
            _run_session_start(new_session, source="clear")

        self.assertEqual("builder", mc.get_role(new_session, db_path=self.db_path))

    def test_no_previous_role_prints_visible_note_and_stays_brozzer(self) -> None:
        new_session = "s-first-ever-clear"

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(new_session, source="clear")

        self.assertEqual("brozzer", mc.get_role(new_session, db_path=self.db_path))
        self.assertIn(
            "[bbb-lite] NOTE: no previous role found, defaulting to brozzer", output
        )
        self.assertNotIn("role inherited after /clear", output)

    def test_does_not_override_a_role_already_declared_for_this_session_id(self) -> None:
        old_session = "s-before-clear"
        new_session = "s-after-clear"
        mc.ensure_session(old_session, db_path=self.db_path)
        mc.set_role(old_session, "architect", db_path=self.db_path)
        mc.ensure_session(new_session, db_path=self.db_path)
        mc.set_role(new_session, "builder", db_path=self.db_path)

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(new_session, source="clear")

        self.assertEqual("builder", mc.get_role(new_session, db_path=self.db_path))
        self.assertNotIn("role inherited after /clear", output)

    def test_non_clear_source_does_not_inherit(self) -> None:
        old_session = "s-before"
        new_session = "s-startup"
        mc.ensure_session(old_session, db_path=self.db_path)
        mc.set_role(old_session, "builder", db_path=self.db_path)

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(new_session, source="startup")

        self.assertEqual("brozzer", mc.get_role(new_session, db_path=self.db_path))
        self.assertNotIn("role inherited after /clear", output)
        self.assertNotIn("no previous role found", output)


class McRoleHelpersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_get_most_recent_role_is_none_when_roles_table_empty(self) -> None:
        self.assertIsNone(mc.get_most_recent_role(db_path=self.db_path))

    def test_role_exists_false_before_declare_true_after(self) -> None:
        self.assertFalse(mc.role_exists("s1", db_path=self.db_path))
        mc.set_role("s1", "builder", db_path=self.db_path)
        self.assertTrue(mc.role_exists("s1", db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
