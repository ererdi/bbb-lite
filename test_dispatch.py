"""Tests for bin/dispatch.py — must never make a real Claude Agent SDK call."""

from __future__ import annotations

import asyncio
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "bin"))

import dispatch
from claude_agent_sdk import AssistantMessage, TextBlock


class DispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        patches = [
            mock.patch("dispatch.mc.init_db"),
            mock.patch("dispatch.mc.ensure_session"),
            mock.patch("dispatch.mc.create_dispatch_run", return_value="run-1"),
            mock.patch("dispatch.mc.record_agent_output"),
            mock.patch("dispatch.mc.complete_dispatch_run"),
            mock.patch("dispatch.mc.record_evidence"),
        ]
        self._ledger_patches = [patcher.start() for patcher in patches]
        for patcher in patches:
            self.addCleanup(patcher.stop)

    def _run(self, task: str = "add a hello world function") -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            asyncio.run(dispatch.dispatch(task))
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
