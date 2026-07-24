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
            mock.patch("daily_check.mc.classify") as classify_mock:
            output = self._run()
        classify_mock.assert_not_called()
        self.assertIn("[daily-check] swept 0 stale session(s)", output)
        self.assertIn("[daily-check] indexed 5 memory file(s)", output)
        self.assertIn("[daily-check] floor clean, no judge call needed", output)
        self.assertNotIn("judge verdict", output)

    def test_findings_trigger_judge_call(self) -> None:
        with mock.patch("daily_check.mc.sweep_stale_sessions", return_value=4), \
            mock.patch("daily_check.mc.index_memory_files", return_value=5), \
            mock.patch(
                "daily_check.mc.check_floor",
                return_value=["4 sessions marked stale"],
            ), \
            mock.patch("daily_check.mc.classify", return_value="needs-attention") as classify_mock:
            output = self._run()
        classify_mock.assert_called_once()
        self.assertIn("[daily-check] finding: 4 sessions marked stale", output)
        self.assertIn("[daily-check] judge verdict: needs-attention", output)

    def test_findings_with_judge_unavailable_prints_none_verdict(self) -> None:
        with mock.patch("daily_check.mc.sweep_stale_sessions", return_value=0), \
            mock.patch("daily_check.mc.index_memory_files", return_value=0), \
            mock.patch(
                "daily_check.mc.check_floor",
                return_value=["memory_index has 501 rows, consider review"],
            ), \
            mock.patch("daily_check.mc.classify", return_value=None) as classify_mock:
            output = self._run()
        classify_mock.assert_called_once()
        self.assertIn("[daily-check] judge verdict: None", output)


if __name__ == "__main__":
    unittest.main()
