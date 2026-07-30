"""Tests for Stage AJ: the git-commit half of Haroon's auto-safepoint.sh.

hooks/stop_checkpoint.py already covered the other half (saving session
state as a real checkpoint, via write_checkpoint()/record_lifecycle_run()).
This adds an opt-in-only auto-commit step: BBB_AUTO_COMMIT=1 must be set,
default is off, and a git failure must never affect the checkpoint write
that already happened (fail-soft, unlike Stage R's compact-recovery path).

Same db-binding approach as the other hook tests: mc's functions default to
db_path=mc.DB_PATH, bound at definition time, so they're replaced with
functools.partial wrappers pinned to a temp db for the duration of each
test. subprocess.run is mocked so no real git commands run.
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))

import mc
import stop_checkpoint


_BOUND_FUNCS = ("write_checkpoint", "record_lifecycle_run")


def _bind_to_test_db(db_path: Path) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    for name in _BOUND_FUNCS:
        bound = functools.partial(getattr(mc, name), db_path=db_path)
        stack.enter_context(mock.patch(f"mc.{name}", bound))
    stack.enter_context(mock.patch("stop_checkpoint.mc.init_db"))
    return stack


def _run_stop_checkpoint(session_id: str = "s1") -> None:
    payload = json.dumps({"session_id": session_id, "transcript_path": ""})
    with mock.patch("sys.stdin", io.StringIO(payload)):
        stop_checkpoint.main()


class AutoCommitTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        os.environ.pop("BBB_AUTO_COMMIT", None)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self.db_path.unlink(missing_ok=True)

    def test_no_git_commit_attempted_when_env_var_unset(self) -> None:
        with _bind_to_test_db(self.db_path), mock.patch("subprocess.run") as run:
            _run_stop_checkpoint()
        run.assert_not_called()

    def test_git_commit_attempted_when_env_var_set(self) -> None:
        os.environ["BBB_AUTO_COMMIT"] = "1"
        with _bind_to_test_db(self.db_path), mock.patch("subprocess.run") as run:
            _run_stop_checkpoint()
        self.assertEqual(2, run.call_count)
        add_call, commit_call = run.call_args_list
        self.assertEqual(["git", "add", "-A"], add_call.args[0])
        self.assertEqual(["git", "commit"], commit_call.args[0][:2])

    def test_git_failure_does_not_crash_or_affect_checkpoint(self) -> None:
        os.environ["BBB_AUTO_COMMIT"] = "1"
        with _bind_to_test_db(self.db_path), mock.patch(
            "subprocess.run", side_effect=RuntimeError("git not found")
        ):
            _run_stop_checkpoint("s-fail")  # must not raise
        checkpoint = mc.latest_checkpoint("s-fail", db_path=self.db_path)
        self.assertIsNotNone(checkpoint)


if __name__ == "__main__":
    unittest.main()
