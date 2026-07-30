"""Tests for Stage AG: project resolution (Codex's "project resolution is
weak" finding).

mc.resolve_project() matches cwd's last folder name against projects.name.
No fuzzy matching, no default project - an unrecognized cwd gets an
explicit {"id": None, "name": "unmapped", "status": "unmapped"} rather than
silently attaching to whatever project happens to exist (Haroon's
"say unmapped out loud" principle).

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
    "record_lifecycle_run",
    "resolve_project",
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


def _run_session_start(session_id: str, cwd: str, source: str = "startup") -> str:
    payload = json.dumps({"session_id": session_id, "source": source, "cwd": cwd})
    buffer = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(buffer):
        session_start.main()
    return buffer.getvalue()


class ResolveProjectTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_matches_cwd_last_folder_name_to_project(self) -> None:
        mc.add_project("bbb-lite", db_path=self.db_path)
        result = mc.resolve_project(r"C:\Users\erdie\source\repos\bbb-lite", db_path=self.db_path)
        self.assertEqual("bbb-lite", result["name"])
        self.assertIsNotNone(result["id"])

    def test_unrecognized_cwd_returns_unmapped(self) -> None:
        mc.add_project("bbb-lite", db_path=self.db_path)
        result = mc.resolve_project(r"C:\Users\erdie\source\repos\some-other-repo", db_path=self.db_path)
        self.assertEqual(
            {"id": None, "name": "unmapped", "status": "unmapped"}, result
        )

    def test_empty_projects_table_returns_unmapped(self) -> None:
        result = mc.resolve_project(r"C:\Users\erdie\source\repos\bbb-lite", db_path=self.db_path)
        self.assertEqual("unmapped", result["status"])


class SessionStartProjectLineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.session_id = "s-stage-ag"
        mc.ensure_session(self.session_id, db_path=self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_prints_matched_project_name(self) -> None:
        mc.add_project("bbb-lite", db_path=self.db_path)
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(
                self.session_id, r"C:\Users\erdie\source\repos\bbb-lite"
            )
        self.assertIn("[bbb-lite] project: bbb-lite", output)

    def test_prints_unmapped_line_for_unrecognized_cwd(self) -> None:
        mc.add_project("bbb-lite", db_path=self.db_path)
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(
                self.session_id, r"C:\Users\erdie\source\repos\some-other-repo"
            )
        self.assertIn(
            "[bbb-lite] project: unmapped (cwd not linked to any project)", output
        )


if __name__ == "__main__":
    unittest.main()
