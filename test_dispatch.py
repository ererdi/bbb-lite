"""Tests for bin/dispatch.py — must never shell out to the real `claude` CLI."""

from __future__ import annotations

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "bin"))

import dispatch


class DispatchTestCase(unittest.TestCase):
    def _run(self, task: str = "add a hello world function") -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            dispatch.dispatch(task)
        return buffer.getvalue()

    def test_builder_only_path(self) -> None:
        with mock.patch("dispatch.mc.classify", return_value="builder") as classify_mock, \
            mock.patch("dispatch.run_claude", return_value="I'd add a function.") as claude_mock:
            output = self._run()
        classify_mock.assert_called_once()
        claude_mock.assert_called_once()  # only the builder turn, no architect turn
        self.assertIn("[dispatch] role=classify -> builder", output)
        self.assertIn("[dispatch] role=builder -> I'd add a function.", output)
        self.assertNotIn("role=architect", output)
        self.assertIn("[dispatch] baton: done", output)

    def test_architect_then_builder_path(self) -> None:
        with mock.patch("dispatch.mc.classify", return_value="architect"), \
            mock.patch(
                "dispatch.run_claude", side_effect=["short plan", "short change"]
            ) as claude_mock:
            output = self._run()
        self.assertEqual(2, claude_mock.call_count)
        self.assertIn("[dispatch] role=architect -> short plan", output)
        self.assertIn("[dispatch] role=builder -> short change", output)
        self.assertIn("[dispatch] baton: done", output)

    def test_classify_none_falls_back_to_builder(self) -> None:
        with mock.patch("dispatch.mc.classify", return_value=None), \
            mock.patch("dispatch.run_claude", return_value="a change") as claude_mock:
            output = self._run()
        claude_mock.assert_called_once()
        self.assertIn("[dispatch] role=classify -> builder", output)

    def test_architect_unavailable_still_reaches_builder(self) -> None:
        with mock.patch("dispatch.mc.classify", return_value="architect"), \
            mock.patch(
                "dispatch.run_claude", side_effect=[None, "a change"]
            ):
            output = self._run()
        self.assertIn("[dispatch] architect unavailable, skipping", output)
        self.assertIn("[dispatch] role=builder -> a change", output)
        self.assertIn("[dispatch] baton: done", output)

    def test_builder_unavailable_still_prints_baton_done(self) -> None:
        with mock.patch("dispatch.mc.classify", return_value="builder"), \
            mock.patch("dispatch.run_claude", return_value=None):
            output = self._run()
        self.assertIn("[dispatch] builder unavailable, skipping", output)
        self.assertIn("[dispatch] baton: done", output)

    def test_run_claude_is_fail_soft_on_missing_binary(self) -> None:
        with mock.patch("dispatch.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(dispatch.run_claude("hello"))

    def test_run_claude_uses_shutil_which_resolved_path(self) -> None:
        completed = subprocess.CompletedProcess(args=["claude"], returncode=0, stdout="ok")
        with mock.patch("dispatch.shutil.which", return_value=r"C:\npm\claude.CMD"), \
            mock.patch("dispatch.subprocess.run", return_value=completed) as mocked:
            self.assertEqual("ok", dispatch.run_claude("hello"))
        args, kwargs = mocked.call_args
        self.assertEqual([r"C:\npm\claude.CMD", "-p", "hello"], args[0])

    def test_run_claude_is_fail_soft_on_timeout(self) -> None:
        with mock.patch(
            "dispatch.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120),
        ):
            self.assertIsNone(dispatch.run_claude("hello"))


if __name__ == "__main__":
    unittest.main()
