"""Tests for bin/daily_check.py — must never call the real Ollama judge on a clean floor."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "bin"))

import daily_check


class DailyCheckTestCase(unittest.TestCase):
    def _run(self) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            daily_check.daily_check()
        return buffer.getvalue()

    def test_clean_floor_skips_judge_call(self) -> None:
        with mock.patch("daily_check.mc.sweep_stale_sessions", return_value=0), \
            mock.patch("daily_check.mc.index_memory_files", return_value=5), \
            mock.patch("daily_check.mc.check_floor", return_value=[]), \
            mock.patch(
                "daily_check.mc.heal_memory", return_value={"groups_found": 0, "merged": 0}
            ), \
            mock.patch("daily_check.mc.retire_low_confidence_lessons", return_value=0), \
            mock.patch("daily_check.mc.classify") as classify_mock:
            output = self._run()
        classify_mock.assert_not_called()
        self.assertIn("[daily-check] swept 0 stale session(s)", output)
        self.assertIn("[daily-check] indexed 5 memory file(s)", output)
        self.assertIn("[daily-check] proposed 0 of 0 similar group(s) for merge", output)
        self.assertIn("[daily-check] retired 0 low-confidence lesson(s)", output)
        self.assertIn("[daily-check] floor clean, no judge call needed", output)
        self.assertNotIn("judge verdict", output)

    def test_findings_trigger_judge_call(self) -> None:
        with mock.patch("daily_check.mc.sweep_stale_sessions", return_value=4), \
            mock.patch("daily_check.mc.index_memory_files", return_value=5), \
            mock.patch(
                "daily_check.mc.check_floor",
                return_value=["4 sessions marked stale"],
            ), \
            mock.patch(
                "daily_check.mc.heal_memory", return_value={"groups_found": 2, "merged": 1}
            ), \
            mock.patch("daily_check.mc.retire_low_confidence_lessons", return_value=1), \
            mock.patch("daily_check.mc.classify", return_value="needs-attention") as classify_mock:
            output = self._run()
        classify_mock.assert_called_once()
        self.assertIn("[daily-check] finding: 4 sessions marked stale", output)
        self.assertIn("[daily-check] proposed 1 of 2 similar group(s) for merge", output)
        self.assertIn("[daily-check] retired 1 low-confidence lesson(s)", output)
        self.assertIn("[daily-check] judge verdict: needs-attention", output)

    def test_findings_with_judge_unavailable_prints_none_verdict(self) -> None:
        with mock.patch("daily_check.mc.sweep_stale_sessions", return_value=0), \
            mock.patch("daily_check.mc.index_memory_files", return_value=0), \
            mock.patch(
                "daily_check.mc.check_floor",
                return_value=["memory_index has 501 rows, consider review"],
            ), \
            mock.patch(
                "daily_check.mc.heal_memory", return_value={"groups_found": 0, "merged": 0}
            ), \
            mock.patch("daily_check.mc.retire_low_confidence_lessons", return_value=0), \
            mock.patch("daily_check.mc.classify", return_value=None) as classify_mock:
            output = self._run()
        classify_mock.assert_called_once()
        self.assertIn("[daily-check] judge verdict: None", output)

    def test_heal_memory_failure_is_fail_soft(self) -> None:
        with mock.patch("daily_check.mc.sweep_stale_sessions", return_value=0), \
            mock.patch("daily_check.mc.index_memory_files", return_value=0), \
            mock.patch("daily_check.mc.check_floor", return_value=[]), \
            mock.patch("daily_check.mc.heal_memory", side_effect=RuntimeError("db locked")), \
            mock.patch("daily_check.mc.retire_low_confidence_lessons", return_value=0), \
            mock.patch("daily_check.mc.classify") as classify_mock:
            output = self._run()  # must not raise
        classify_mock.assert_not_called()
        self.assertIn("[daily-check] memory healing skipped (error)", output)
        self.assertIn("[daily-check] floor clean, no judge call needed", output)

    def test_retire_low_confidence_lessons_failure_is_fail_soft(self) -> None:
        with mock.patch("daily_check.mc.sweep_stale_sessions", return_value=0), \
            mock.patch("daily_check.mc.index_memory_files", return_value=0), \
            mock.patch("daily_check.mc.check_floor", return_value=[]), \
            mock.patch(
                "daily_check.mc.heal_memory", return_value={"groups_found": 0, "merged": 0}
            ), \
            mock.patch(
                "daily_check.mc.retire_low_confidence_lessons",
                side_effect=RuntimeError("db locked"),
            ), \
            mock.patch("daily_check.mc.classify") as classify_mock:
            output = self._run()  # must not raise
        classify_mock.assert_not_called()
        self.assertIn("[daily-check] confidence retirement skipped (error)", output)
        self.assertIn("[daily-check] floor clean, no judge call needed", output)


if __name__ == "__main__":
    unittest.main()
