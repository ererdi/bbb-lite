"""Tests for Stage AH: client_session_uuid support (Codex's sub-items 1 and 4
- read it from the payload, and tie the session row to it too).

client_session_uuid is not a documented field of Claude Code's real
SessionStart payload (checked against the hooks docs, 2026-07-29) - it may
simply never be present. session_start.py must read it defensively (None
when absent) and behave exactly as Stage AF did before when it's missing.
When present and it links to an earlier session, that link is a more
precise connection than Stage AF's "most recently declared role anywhere"
fallback, so it takes priority - but only within the same source=="clear"
and not-yet-role-declared gate Stage AF already used.

Same db-binding approach as test_role_inheritance.py.
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
    "find_session_by_client_uuid",
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


def _run_session_start(
    session_id: str, source: str = "startup", client_session_uuid: str | None = None
) -> str:
    payload = {"session_id": session_id, "source": source}
    if client_session_uuid is not None:
        payload["client_session_uuid"] = client_session_uuid
    buffer = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(
        buffer
    ):
        session_start.main()
    return buffer.getvalue()


class McClientSessionUuidTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_ensure_session_records_client_session_uuid(self) -> None:
        mc.ensure_session("s1", client_session_uuid="uuid-123", db_path=self.db_path)
        self.assertEqual(
            "s1", mc.find_session_by_client_uuid("uuid-123", db_path=self.db_path)
        )

    def test_find_session_by_client_uuid_returns_none_when_unknown(self) -> None:
        self.assertIsNone(
            mc.find_session_by_client_uuid("never-seen", db_path=self.db_path)
        )

    def test_ensure_session_without_client_session_uuid_still_works(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        rows = mc.list_sessions(db_path=self.db_path)
        self.assertEqual(1, len(rows))
        self.assertIsNone(mc.find_session_by_client_uuid("anything", db_path=self.db_path))


class SessionStartClientSessionUuidTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_missing_client_session_uuid_falls_back_to_stage_af_behavior(self) -> None:
        old_session, new_session = "s-before-clear", "s-after-clear"
        mc.ensure_session(old_session, db_path=self.db_path)
        mc.set_role(old_session, "builder", db_path=self.db_path)

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(new_session, source="clear")

        self.assertEqual("builder", mc.get_role(new_session, db_path=self.db_path))
        self.assertIn("[bbb-lite] role inherited after /clear: builder", output)
        self.assertNotIn("client_session_uuid", output)

    def test_matching_client_session_uuid_inherits_that_sessions_role_directly(self) -> None:
        old_session, new_session = "s-before-clear", "s-after-clear"
        other_session = "s-unrelated-more-recent"

        mc.ensure_session(old_session, client_session_uuid="uuid-abc", db_path=self.db_path)
        mc.set_role(old_session, "architect", db_path=self.db_path)
        # A more-recently-declared role elsewhere, to prove the direct link
        # wins over Stage AF's "most recent role anywhere" fallback.
        mc.ensure_session(other_session, db_path=self.db_path)
        mc.set_role(other_session, "builder", db_path=self.db_path)

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(
                new_session, source="clear", client_session_uuid="uuid-abc"
            )

        self.assertEqual("architect", mc.get_role(new_session, db_path=self.db_path))
        self.assertIn(
            "[bbb-lite] role inherited after /clear via client_session_uuid: architect", output
        )

    def test_unrecognized_client_session_uuid_falls_back_to_most_recent_role(self) -> None:
        old_session, new_session = "s-before-clear", "s-after-clear"
        mc.ensure_session(old_session, db_path=self.db_path)
        mc.set_role(old_session, "advisor", db_path=self.db_path)

        with _bind_to_test_db(self.db_path):
            output = _run_session_start(
                new_session, source="clear", client_session_uuid="never-seen-uuid"
            )

        self.assertEqual("advisor", mc.get_role(new_session, db_path=self.db_path))
        self.assertIn("[bbb-lite] role inherited after /clear: advisor", output)
        self.assertNotIn("via client_session_uuid", output)


if __name__ == "__main__":
    unittest.main()
