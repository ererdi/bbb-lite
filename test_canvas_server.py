"""Tests for bin/canvas_server.py — JSON shape only, no real HTTP/server calls."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "bin"))

import canvas_server


class BuildCanvasDataTestCase(unittest.TestCase):
    def test_combines_sessions_tasks_and_links(self) -> None:
        sessions = [{"session_id": "s1", "started_at": "t", "status": "active"}]
        tasks = [{"id": 1, "title": "write tests", "status": "todo"}]
        links = [{"task_id": 1, "session_id": "s1", "activity": "created", "at": "t"}]
        with mock.patch("canvas_server.mc.list_sessions", return_value=sessions), \
            mock.patch("canvas_server.mc.list_tasks", return_value=tasks), \
            mock.patch("canvas_server.mc.list_task_activity", return_value=links):
            data = canvas_server.build_canvas_data()
        self.assertEqual(sessions, data["sessions"])
        self.assertEqual(tasks, data["tasks"])
        self.assertEqual(links, data["links"])

    def test_propagates_db_errors_to_caller(self) -> None:
        with mock.patch(
            "canvas_server.mc.list_sessions", side_effect=RuntimeError("db locked")
        ):
            with self.assertRaises(RuntimeError):
                canvas_server.build_canvas_data()


class BuildContextMapDataTestCase(unittest.TestCase):
    def test_delegates_to_mc_list_context_map_approved_only(self) -> None:
        payload = {"nodes": [{"id": 1, "status": "approved"}], "edges": []}
        with mock.patch(
            "canvas_server.mc.list_context_map", return_value=payload
        ) as list_context_map:
            data = canvas_server.build_context_map_data()
        list_context_map.assert_called_once_with(only_approved=True, db_path=mock.ANY)
        self.assertEqual(payload, data)


class CanvasRequestHandlerTestCase(unittest.TestCase):
    """Exercise do_GET's routing/response logic without opening a real socket."""

    def _make_handler(self, path: str) -> canvas_server.CanvasRequestHandler:
        handler = canvas_server.CanvasRequestHandler.__new__(canvas_server.CanvasRequestHandler)
        handler.path = path
        handler.responses: list[int] = []
        handler.sent_json: list[dict] = []

        def fake_send_json(status: int, payload: dict) -> None:
            handler.responses.append(status)
            handler.sent_json.append(payload)

        handler._send_json = fake_send_json  # type: ignore[method-assign]
        return handler

    def test_get_unknown_path_returns_404(self) -> None:
        handler = self._make_handler("/nope")
        handler.do_GET()
        self.assertEqual([404], handler.responses)

    def test_get_canvas_data_returns_200_with_payload(self) -> None:
        handler = self._make_handler("/api/canvas-data")
        payload = {"sessions": [], "tasks": [], "links": []}
        with mock.patch("canvas_server.build_canvas_data", return_value=payload):
            handler.do_GET()
        self.assertEqual([200], handler.responses)
        self.assertEqual([payload], handler.sent_json)

    def test_get_canvas_data_fails_soft_on_db_error(self) -> None:
        handler = self._make_handler("/api/canvas-data")
        with mock.patch(
            "canvas_server.build_canvas_data", side_effect=RuntimeError("db locked")
        ):
            handler.do_GET()  # must not raise
        self.assertEqual([500], handler.responses)
        self.assertIn("db locked", handler.sent_json[0]["error"])

    def test_get_context_map_returns_200_with_payload(self) -> None:
        handler = self._make_handler("/api/context-map")
        payload = {"nodes": [], "edges": []}
        with mock.patch("canvas_server.build_context_map_data", return_value=payload):
            handler.do_GET()
        self.assertEqual([200], handler.responses)
        self.assertEqual([payload], handler.sent_json)


if __name__ == "__main__":
    unittest.main()
