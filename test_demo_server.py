"""Tests for bin/demo_server.py — mocked JSON shape only, no real unittest/subprocess/server calls."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "bin"))

import demo_server


class BuildDemoDataTestCase(unittest.TestCase):
    def setUp(self) -> None:
        demo_server._memory_example_cache = None

    def test_returns_all_seven_sections(self) -> None:
        with mock.patch("demo_server._build_mission_control", return_value={"project_count": 1}), \
            mock.patch("demo_server._build_lifecycle", return_value={"session_id": "s1"}), \
            mock.patch("demo_server._build_roles", return_value={"role_permissions": {}}), \
            mock.patch("demo_server._build_memory", return_value={"file_count": 3}), \
            mock.patch("demo_server._build_daily_health", return_value={"findings": []}), \
            mock.patch("demo_server._build_evidence", return_value={"passed": 1, "total": 1}):
            data = demo_server.build_demo_data()
        self.assertEqual(
            {
                "mission_control", "lifecycle", "roles", "memory",
                "daily_health", "canvas_link", "evidence",
            },
            set(data.keys()),
        )
        self.assertEqual({"project_count": 1}, data["mission_control"])
        self.assertEqual({"session_id": "s1"}, data["lifecycle"])
        self.assertEqual("http://localhost:5173", data["canvas_link"])
        self.assertEqual({"passed": 1, "total": 1}, data["evidence"])

    def test_one_section_failing_does_not_sink_others(self) -> None:
        with mock.patch(
            "demo_server._build_mission_control", side_effect=RuntimeError("db locked")
        ), \
            mock.patch("demo_server._build_lifecycle", return_value={"session_id": "s1"}), \
            mock.patch("demo_server._build_roles", return_value={"role_permissions": {}}), \
            mock.patch("demo_server._build_memory", return_value={"file_count": 3}), \
            mock.patch("demo_server._build_daily_health", return_value={"findings": []}), \
            mock.patch("demo_server._build_evidence", return_value={"passed": 1, "total": 1}):
            data = demo_server.build_demo_data()
        self.assertIn("error", data["mission_control"])
        self.assertIn("db locked", data["mission_control"]["error"])
        self.assertEqual({"session_id": "s1"}, data["lifecycle"])
        self.assertEqual("http://localhost:5173", data["canvas_link"])


class SectionBuilderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        demo_server._memory_example_cache = None

    def test_mission_control_counts_open_and_done(self) -> None:
        tasks = [
            {"id": 1, "status": "todo"},
            {"id": 2, "status": "done"},
            {"id": 3, "status": "in_progress"},
        ]
        with mock.patch("demo_server.mc.list_tasks", return_value=tasks), \
            mock.patch("demo_server.mc.list_projects", return_value=[{"id": 1}]), \
            mock.patch("demo_server.mc.list_sprints", return_value=[{"id": 1}, {"id": 2}]):
            result = demo_server._build_mission_control()
        self.assertEqual(1, result["project_count"])
        self.assertEqual(2, result["sprint_count"])
        self.assertEqual(2, result["task_count_open"])
        self.assertEqual(1, result["task_count_done"])
        self.assertIn("complete_task()", result["guard_note"])

    def test_lifecycle_uses_latest_session(self) -> None:
        sessions = [
            {"session_id": "old", "started_at": "t1", "status": "stale"},
            {"session_id": "new", "started_at": "t2", "status": "active"},
        ]
        with mock.patch("demo_server.mc.list_sessions", return_value=sessions), \
            mock.patch("demo_server.mc.latest_checkpoint", return_value={"risk": "low"}), \
            mock.patch("demo_server.mc.session_score", return_value={"score": 100}):
            result = demo_server._build_lifecycle()
        self.assertEqual("new", result["session_id"])
        self.assertEqual({"risk": "low"}, result["checkpoint"])
        self.assertEqual({"score": 100}, result["score"])

    def test_lifecycle_with_no_sessions(self) -> None:
        with mock.patch("demo_server.mc.list_sessions", return_value=[]):
            result = demo_server._build_lifecycle()
        self.assertIsNone(result["session_id"])
        self.assertIsNone(result["checkpoint"])
        self.assertIsNone(result["score"])

    def test_roles_includes_full_permission_table(self) -> None:
        with mock.patch("demo_server.mc.list_sessions", return_value=[{"session_id": "s1"}]), \
            mock.patch("demo_server.mc.get_role", return_value="builder"):
            result = demo_server._build_roles()
        self.assertEqual(demo_server.mc.ROLE_PERMISSIONS, result["role_permissions"])
        self.assertEqual("builder", result["latest_session_role"])

    def test_memory_search_example_is_cached_across_calls(self) -> None:
        with mock.patch(
            "demo_server.mc.search_memory", return_value=[{"title": "kw"}]
        ) as search_mock, \
            mock.patch(
                "demo_server.mc.semantic_search", return_value=[{"title": "sem"}]
            ) as semantic_mock:
            first = demo_server._memory_search_example()
            second = demo_server._memory_search_example()
        self.assertEqual(first, second)
        search_mock.assert_called_once()
        semantic_mock.assert_called_once()

    def test_daily_health_wraps_check_floor_findings(self) -> None:
        with mock.patch("demo_server.mc.check_floor", return_value=["3 sessions stale"]):
            result = demo_server._build_daily_health()
        self.assertEqual(["3 sessions stale"], result["findings"])


class ParseUnittestOutputTestCase(unittest.TestCase):
    def test_parses_all_passing(self) -> None:
        output = "....\n----------------------------------------------------------------------\nRan 4 tests in 0.010s\n\nOK\n"
        result = demo_server._parse_unittest_output(output)
        self.assertEqual({"passed": 4, "total": 4}, result)

    def test_parses_failures_and_errors(self) -> None:
        output = (
            "....F.E\n----------------------------------------------------------------------\n"
            "Ran 7 tests in 0.020s\n\nFAILED (failures=1, errors=1)\n"
        )
        result = demo_server._parse_unittest_output(output)
        self.assertEqual({"passed": 5, "total": 7}, result)

    def test_no_ran_line_returns_zero_total(self) -> None:
        result = demo_server._parse_unittest_output("nonsense output")
        self.assertEqual({"passed": 0, "total": 0}, result)


class BuildEvidenceTestCase(unittest.TestCase):
    def test_fails_soft_when_subprocess_unavailable(self) -> None:
        with mock.patch("demo_server.subprocess.run", side_effect=FileNotFoundError("no python")):
            result = demo_server._build_evidence()
        self.assertEqual({"tests": "unavailable"}, result)

    def test_fails_soft_when_no_tests_found(self) -> None:
        fake_result = mock.Mock(stdout="", stderr="", returncode=0)
        with mock.patch("demo_server.subprocess.run", return_value=fake_result):
            result = demo_server._build_evidence()
        self.assertEqual({"tests": "unavailable"}, result)

    def test_reports_passed_and_total_on_success(self) -> None:
        fake_result = mock.Mock(
            stdout="",
            stderr="Ran 10 tests in 1.0s\n\nOK\n",
            returncode=0,
        )
        with mock.patch("demo_server.subprocess.run", return_value=fake_result):
            result = demo_server._build_evidence()
        self.assertEqual({"passed": 10, "total": 10, "ok": True}, result)


class DemoRequestHandlerTestCase(unittest.TestCase):
    """Exercise do_GET's routing/response logic without opening a real socket."""

    def _make_handler(self, path: str) -> demo_server.DemoRequestHandler:
        handler = demo_server.DemoRequestHandler.__new__(demo_server.DemoRequestHandler)
        handler.path = path
        handler.responses: list[int] = []
        handler.sent_json: list[dict] = []
        handler.sent_html: list[bytes] = []

        def fake_send_json(status: int, payload: dict) -> None:
            handler.responses.append(status)
            handler.sent_json.append(payload)

        def fake_send_html(status: int, body: bytes) -> None:
            handler.responses.append(status)
            handler.sent_html.append(body)

        handler._send_json = fake_send_json  # type: ignore[method-assign]
        handler._send_html = fake_send_html  # type: ignore[method-assign]
        return handler

    def test_get_unknown_path_returns_404(self) -> None:
        handler = self._make_handler("/nope")
        handler.do_GET()
        self.assertEqual([404], handler.responses)

    def test_get_demo_data_returns_200_with_payload(self) -> None:
        handler = self._make_handler("/api/demo-data")
        payload = {"mission_control": {}, "evidence": {}}
        with mock.patch("demo_server.build_demo_data", return_value=payload):
            handler.do_GET()
        self.assertEqual([200], handler.responses)
        self.assertEqual([payload], handler.sent_json)

    def test_get_demo_data_fails_soft_on_error(self) -> None:
        handler = self._make_handler("/api/demo-data")
        with mock.patch(
            "demo_server.build_demo_data", side_effect=RuntimeError("db locked")
        ):
            handler.do_GET()  # must not raise
        self.assertEqual([500], handler.responses)
        self.assertIn("db locked", handler.sent_json[0]["error"])

    def test_get_root_serves_console_html(self) -> None:
        handler = self._make_handler("/")
        fake_console_path = mock.Mock()
        fake_console_path.read_bytes.return_value = b"<html></html>"
        with mock.patch("demo_server.CONSOLE_HTML_PATH", fake_console_path):
            handler.do_GET()
        self.assertEqual([200], handler.responses)
        self.assertEqual([b"<html></html>"], handler.sent_html)


if __name__ == "__main__":
    unittest.main()
