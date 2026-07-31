"""Tests for bin/dispatch.py — must never make a real Claude Agent SDK call."""

from __future__ import annotations

import asyncio
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "bin"))

import dispatch
from claude_agent_sdk import AssistantMessage, TextBlock


class ParseCtoResponseTestCase(unittest.TestCase):
    def test_parses_yes_and_no(self) -> None:
        cases = [
            ("TITLE: Fix login bug\nNEEDS_ARCHITECT: yes", ("Fix login bug", True)),
            ("TITLE: Fix login bug\nNEEDS_ARCHITECT: no", ("Fix login bug", False)),
            ("title: add a helper\nneeds_architect: YES", ("add a helper", True)),
            (
                "Some preamble\nTITLE:   spaced title   \nNEEDS_ARCHITECT: no\ntrailing",
                ("spaced title", False),
            ),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(expected, dispatch._parse_cto_response(text))

    def test_returns_none_when_unparseable(self) -> None:
        for text in ("I'd add a function.", "TITLE: only a title", "NEEDS_ARCHITECT: yes"):
            with self.subTest(text=text):
                self.assertIsNone(dispatch._parse_cto_response(text))


class WorktreeTestCase(unittest.TestCase):
    """Stage V: create_worktree()/remove_worktree() — real git commands must
    never run in a test, so subprocess.run itself is mocked here."""

    def test_create_worktree_calls_git_with_correct_args_and_returns_path(self) -> None:
        repo_root = Path(dispatch.__file__).resolve().parent.parent
        completed = subprocess.CompletedProcess(args=["git"], returncode=0)
        with mock.patch("dispatch.subprocess.run", return_value=completed) as run_mock:
            result = dispatch.create_worktree(5)
        args, kwargs = run_mock.call_args
        self.assertEqual(
            ["git", "worktree", "add", "../bbb-lite-work-5", "-b", "work/5"], args[0]
        )
        self.assertEqual(repo_root, kwargs["cwd"])
        self.assertEqual((repo_root / "../bbb-lite-work-5").resolve(), result)

    def test_create_worktree_returns_none_on_failure(self) -> None:
        completed = subprocess.CompletedProcess(args=["git"], returncode=1)
        with mock.patch("dispatch.subprocess.run", return_value=completed):
            self.assertIsNone(dispatch.create_worktree(6))  # branch/worktree already exists

        with mock.patch("dispatch.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(dispatch.create_worktree(7))  # git not installed

    def test_remove_worktree_calls_git_with_correct_args(self) -> None:
        worktree_path = Path("/tmp/bbb-lite-work-5")
        completed = subprocess.CompletedProcess(args=["git"], returncode=0)
        with mock.patch("dispatch.subprocess.run", return_value=completed) as run_mock:
            dispatch.remove_worktree(worktree_path, 5)
        calls = [call.args[0] for call in run_mock.call_args_list]
        self.assertIn(
            ["git", "worktree", "remove", str(worktree_path), "--force"], calls
        )
        self.assertIn(["git", "branch", "-D", "work/5"], calls)

        with mock.patch("dispatch.subprocess.run", side_effect=OSError("no git")):
            dispatch.remove_worktree(worktree_path, 5)  # must not raise

    def test_remove_worktree_deletes_branch_independently_of_worktree_result(self) -> None:
        # Worktree removal fails (non-zero exit) but the branch delete must
        # still be attempted — the two cleanup steps are independent.
        completed = subprocess.CompletedProcess(args=["git"], returncode=1)
        with mock.patch("dispatch.subprocess.run", return_value=completed) as run_mock:
            dispatch.remove_worktree(Path("/tmp/bbb-lite-work-9"), 9)
        calls = [call.args[0] for call in run_mock.call_args_list]
        self.assertIn(["git", "branch", "-D", "work/9"], calls)


class DispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        patches = [
            mock.patch("dispatch.mc.init_db"),
            mock.patch("dispatch.mc.ensure_session"),
            mock.patch("dispatch.mc.create_dispatch_run", return_value="run-1"),
            mock.patch("dispatch.mc.record_agent_output"),
            mock.patch("dispatch.mc.complete_dispatch_run"),
            mock.patch("dispatch.mc.record_evidence"),
            mock.patch("dispatch.mc.add_task", return_value=None),
            mock.patch("dispatch.mc.complete_task"),
            mock.patch("dispatch.create_worktree", return_value=None),
            mock.patch("dispatch.remove_worktree"),
        ]
        self._ledger_patches = [patcher.start() for patcher in patches]
        for patcher in patches:
            self.addCleanup(patcher.stop)
        (
            self.mock_init_db,
            self.mock_ensure_session,
            self.mock_create_dispatch_run,
            self.mock_record_agent_output,
            self.mock_complete_dispatch_run,
            self.mock_record_evidence,
            self.mock_add_task,
            self.mock_complete_task,
            self.mock_create_worktree,
            self.mock_remove_worktree,
        ) = self._ledger_patches

    def _run(self, task: str = "add a hello world function") -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            asyncio.run(dispatch.dispatch(task))
        return buffer.getvalue()

    def test_builder_only_path(self) -> None:
        # CTO turn's reply ("I'd add a function.") doesn't parse, so this
        # falls back to classify() — same as every pre-Stage-U scenario below.
        with mock.patch("dispatch.mc.classify", return_value="builder") as classify_mock, \
            mock.patch("dispatch.run_claude", return_value="I'd add a function.") as claude_mock:
            output = self._run()
        classify_mock.assert_called_once()
        self.assertEqual(2, claude_mock.call_count)  # cto (unparseable) + builder
        self.assertIn("[dispatch] role=cto -> unavailable, falling back to classify()", output)
        self.assertIn("[dispatch] role=classify -> builder", output)
        self.assertIn("[dispatch] role=builder -> I'd add a function.", output)
        self.assertNotIn("role=architect", output)
        self.assertIn("[dispatch] baton: done", output)

    def test_architect_then_builder_path(self) -> None:
        with mock.patch("dispatch.mc.classify", return_value="architect"), \
            mock.patch(
                "dispatch.run_claude",
                side_effect=["not a cto reply", "short plan", "short change"],
            ) as claude_mock:
            output = self._run()
        self.assertEqual(3, claude_mock.call_count)  # cto (unparseable) + architect + builder
        self.assertIn("[dispatch] role=architect -> short plan", output)
        self.assertIn("[dispatch] role=builder -> short change", output)
        self.assertIn("[dispatch] baton: done", output)

    def test_classify_none_falls_back_to_builder(self) -> None:
        with mock.patch("dispatch.mc.classify", return_value=None), \
            mock.patch("dispatch.run_claude", return_value="a change") as claude_mock:
            output = self._run()
        self.assertEqual(2, claude_mock.call_count)  # cto (unparseable) + builder
        self.assertIn("[dispatch] role=classify -> builder", output)

    def test_architect_unavailable_still_reaches_builder(self) -> None:
        with mock.patch("dispatch.mc.classify", return_value="architect"), \
            mock.patch(
                "dispatch.run_claude", side_effect=[None, None, "a change"]
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

    def test_cto_yes_opens_task_runs_architect_and_completes_task_after_builder(self) -> None:
        with mock.patch("dispatch.mc.classify") as classify_mock, \
            mock.patch("dispatch.mc.add_task", return_value=42) as add_task_mock, \
            mock.patch("dispatch.mc.complete_task") as complete_task_mock, \
            mock.patch(
                "dispatch.run_claude",
                side_effect=[
                    "TITLE: Fix the login bug\nNEEDS_ARCHITECT: yes",
                    "short plan",
                    "short change",
                ],
            ):
            output = self._run()
        classify_mock.assert_not_called()  # CTO answered cleanly, no fallback needed
        add_task_mock.assert_called_once_with(
            "Fix the login bug", status="in_progress", session_id=dispatch.DISPATCH_SESSION_ID
        )
        complete_task_mock.assert_called_once_with(
            42,
            session_id=dispatch.DISPATCH_SESSION_ID,
            evidence_id=self.mock_record_evidence.return_value,
            completion_summary="short change",
            require_test_evidence=False,
        )
        self.assertIn("[dispatch] role=cto -> Fix the login bug (architect: yes)", output)
        self.assertIn("[dispatch] role=architect -> short plan", output)
        self.assertIn("[dispatch] role=builder -> short change", output)

    def test_cto_no_opens_task_and_skips_architect(self) -> None:
        with mock.patch("dispatch.mc.classify") as classify_mock, \
            mock.patch("dispatch.mc.add_task", return_value=7) as add_task_mock, \
            mock.patch("dispatch.mc.complete_task") as complete_task_mock, \
            mock.patch(
                "dispatch.run_claude",
                side_effect=["TITLE: Add a helper\nNEEDS_ARCHITECT: no", "short change"],
            ) as claude_mock:
            output = self._run()
        classify_mock.assert_not_called()
        self.assertEqual(2, claude_mock.call_count)  # cto + builder, no architect turn
        add_task_mock.assert_called_once_with(
            "Add a helper", status="in_progress", session_id=dispatch.DISPATCH_SESSION_ID
        )
        complete_task_mock.assert_called_once_with(
            7,
            session_id=dispatch.DISPATCH_SESSION_ID,
            evidence_id=self.mock_record_evidence.return_value,
            completion_summary="short change",
            require_test_evidence=False,
        )
        self.assertIn("[dispatch] role=cto -> Add a helper (architect: no)", output)
        self.assertNotIn("role=architect", output)

    def test_cto_unavailable_falls_back_to_classify_and_never_opens_task(self) -> None:
        with mock.patch("dispatch.mc.classify", return_value="builder") as classify_mock, \
            mock.patch("dispatch.mc.add_task") as add_task_mock, \
            mock.patch("dispatch.mc.complete_task") as complete_task_mock, \
            mock.patch("dispatch.run_claude", side_effect=[None, "a change"]):
            output = self._run()
        classify_mock.assert_called_once()
        add_task_mock.assert_not_called()
        complete_task_mock.assert_not_called()
        self.assertIn("[dispatch] role=cto -> unavailable, falling back to classify()", output)

    def test_dispatch_falls_back_to_main_folder_when_worktree_unavailable(self) -> None:
        # create_worktree defaults to returning None per setUp — i.e. "couldn't
        # create one" — so the builder call must run with cwd=None.
        with mock.patch("dispatch.mc.add_task", return_value=42), \
            mock.patch("dispatch.remove_worktree") as remove_mock, \
            mock.patch(
                "dispatch.run_claude",
                side_effect=["TITLE: Add a helper\nNEEDS_ARCHITECT: no", "a change"],
            ) as claude_mock:
            self._run()
        builder_call = claude_mock.call_args_list[-1]
        self.assertIsNone(builder_call.kwargs["cwd"])
        remove_mock.assert_not_called()

    def test_dispatch_creates_and_always_removes_worktree_for_builder(self) -> None:
        fake_worktree = Path("/tmp/bbb-lite-work-42")
        # Builder turn fails (None) here on purpose: cleanup must still happen
        # "task tamamlansın ya da tamamlanmasın" (succeed or fail alike).
        with mock.patch("dispatch.mc.add_task", return_value=42), \
            mock.patch("dispatch.create_worktree", return_value=fake_worktree), \
            mock.patch("dispatch.remove_worktree") as remove_mock, \
            mock.patch(
                "dispatch.run_claude",
                side_effect=["TITLE: Add a helper\nNEEDS_ARCHITECT: no", None],
            ) as claude_mock:
            output = self._run()
        builder_call = claude_mock.call_args_list[-1]
        self.assertEqual(str(fake_worktree), builder_call.kwargs["cwd"])
        remove_mock.assert_called_once_with(fake_worktree, 42)
        self.assertIn(f"[dispatch] builder worktree: {fake_worktree}", output)
        self.assertIn("[dispatch] builder unavailable, skipping", output)

    def test_run_claude_is_fail_soft_when_query_raises(self) -> None:
        async def _boom(*, prompt, options):
            raise RuntimeError("ANTHROPIC_API_KEY set, auth mismatch")
            yield  # pragma: no cover - makes this an async generator function

        with mock.patch("dispatch.query", side_effect=_boom):
            result = asyncio.run(
                dispatch.run_claude("hello", system_prompt="be terse")
            )
        self.assertIsNone(result)

    def test_run_claude_is_fail_soft_on_timeout(self) -> None:
        async def _hangs(*, prompt, options):
            await asyncio.sleep(10)
            yield  # pragma: no cover - unreachable, query never yields in time

        with mock.patch("dispatch.query", side_effect=_hangs):
            result = asyncio.run(
                dispatch.run_claude("hello", system_prompt="be terse", timeout=0.01)
            )
        self.assertIsNone(result)

    def test_run_claude_extracts_text_from_assistant_message(self) -> None:
        async def _stream(*, prompt, options):
            yield AssistantMessage(
                content=[TextBlock(text="I'd add a function.")], model="test-model"
            )

        with mock.patch("dispatch.query", side_effect=_stream):
            result = asyncio.run(
                dispatch.run_claude("hello", system_prompt="be terse")
            )
        self.assertEqual("I'd add a function.", result)


if __name__ == "__main__":
    unittest.main()
