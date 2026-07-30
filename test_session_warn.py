"""Tests for Stage AK: hooks/session_warn.py.

Codex's 9th and last Session Lifecycle sub-item. Uses the existing
mc.session_score() (Stage Y) as-is - no new scoring logic, just a
UserPromptSubmit warning when overall_score drops below 50.

Same db-binding approach as the other hook tests: mc.session_score defaults
to db_path=mc.DB_PATH and log_path=mc.PRETOOLUSE_LOG_PATH, both bound at
definition time, so it's replaced with a functools.partial wrapper pinned to
a temp db and a temp (empty, unless a test writes to it) log file for the
duration of each test - this keeps tests isolated from the real
hooks/pretooluse.log.
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
import session_warn


def _bind_to_test_db(db_path: Path, log_path: Path) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    bound = functools.partial(mc.session_score, db_path=db_path, log_path=log_path)
    stack.enter_context(mock.patch("mc.session_score", bound))
    stack.enter_context(mock.patch("session_warn.mc.init_db"))
    return stack


def _run_session_warn(session_id) -> str:
    payload = json.dumps({"session_id": session_id})
    buffer = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(buffer):
        session_warn.main()
    return buffer.getvalue()


class SessionWarnTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.log_path = Path(tempfile.mktemp(suffix=".log"))

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)
        self.log_path.unlink(missing_ok=True)

    def test_no_warning_for_a_healthy_session(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="doing fine", db_path=self.db_path)
        with _bind_to_test_db(self.db_path, self.log_path):
            output = _run_session_warn("s1")
        self.assertEqual("", output)

    def test_warning_printed_for_a_low_scoring_session(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        self.log_path.write_text(
            "2026-07-29T10:00:00 denied session=s1: role 'advisor' has no code-write permission\n"
            "2026-07-29T10:00:01 denied session=s1: role 'advisor' has no code-write permission\n"
            "2026-07-29T10:00:02 denied session=s1: role 'advisor' has no code-write permission\n"
            "2026-07-29T10:00:03 denied session=s1: role 'advisor' has no code-write permission\n"
            "2026-07-29T10:00:04 denied session=s1: role 'advisor' has no code-write permission\n",
            encoding="utf-8",
        )
        # No checkpoint written -> context_score=50; no tasks touched ->
        # exec_score=100; 5 denials -> rules_score=0.
        # overall = round((100 + 50 + 0) / 3) = 50 -> not yet below threshold,
        # so add a touched-but-incomplete task to push exec_score down too.
        mc.add_task("something", session_id="s1", db_path=self.db_path)
        with _bind_to_test_db(self.db_path, self.log_path):
            output = _run_session_warn("s1")
            score = mc.session_score("s1")
        self.assertLess(score["overall_score"], 50)
        self.assertIn(
            f"[bbb-lite] WARNING: session health low (score={score['overall_score']}/100) - "
            "consider /clear or wrapping up",
            output,
        )

    def test_missing_session_id_is_silent(self) -> None:
        buffer = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps({}))), contextlib.redirect_stdout(
            buffer
        ):
            session_warn.main()
        self.assertEqual("", buffer.getvalue())

    def test_session_score_error_does_not_crash(self) -> None:
        with mock.patch("session_warn.mc.init_db"), mock.patch(
            "mc.session_score", side_effect=RuntimeError("boom")
        ):
            output = _run_session_warn("s1")  # must not raise
        self.assertEqual("", output)


if __name__ == "__main__":
    unittest.main()
