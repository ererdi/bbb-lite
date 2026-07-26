"""Tests for bin/assistant_server.py — mocked assistant surface, no real model calls."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "bin"))

import assistant_server


class RoleParsingTestCase(unittest.TestCase):
    def test_declared_role_from_english(self) -> None:
        self.assertEqual("builder", assistant_server._declared_role("you are the builder now"))

    def test_declared_role_from_turkish_architect(self) -> None:
        self.assertEqual("architect", assistant_server._declared_role("sen mimarsın"))

    def test_no_declared_role(self) -> None:
        self.assertIsNone(assistant_server._declared_role("just answer normally"))


class AssistantAnswerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        patches = [
            mock.patch("assistant_server.mc.record_answer_quality", return_value=101),
            mock.patch("assistant_server.mc.record_evidence", return_value=202),
            mock.patch("assistant_server.mc.record_answer_receipt", return_value=303),
        ]
        self._ledger_patches = [patcher.start() for patcher in patches]
        for patcher in patches:
            self.addCleanup(patcher.stop)

    def _mock_context(self):
        return mock.patch(
            "assistant_server.collect_context",
            return_value={
                "session_id": "web",
                "role": "brozzer",
                "permissions": {"code": False, "data": False},
                "declared_role": None,
                "checkpoint": None,
                "keyword_memory": [],
                "semantic_memory": [],
                "graph_memory": [],
                "open_tasks": [],
            },
        )

    def test_empty_message_is_refused(self) -> None:
        self.assertEqual({"error": "message is required"}, assistant_server.answer_message(" "))

    def test_claude_cli_is_first_engine(self) -> None:
        with self._mock_context(), \
            mock.patch("assistant_server.run_claude", return_value="claude answer") as claude, \
            mock.patch("assistant_server.mc.ask_ollama") as ollama, \
            mock.patch("assistant_server.mc.write_checkpoint") as checkpoint, \
            mock.patch("assistant_server.mc.record_turn") as turn, \
            mock.patch("assistant_server.mc.record_event") as event:
            result = assistant_server.answer_message("hello", "web")
        self.assertEqual("claude-cli", result["engine"])
        self.assertIn("Known:", result["answer"])
        self.assertIn("claude answer", result["answer"])
        self.assertIn("quality_score", result)
        self.assertEqual(101, result["quality_score_id"])
        self.assertEqual(202, result["evidence_id"])
        self.assertEqual(303, result["answer_receipt_id"])
        claude.assert_called_once()
        ollama.assert_not_called()
        checkpoint.assert_called_once()
        self.assertEqual(2, turn.call_count)
        event.assert_called_once()

    def test_local_guarded_fallback_when_claude_unavailable(self) -> None:
        with self._mock_context(), \
            mock.patch("assistant_server.run_claude", return_value=None), \
            mock.patch("assistant_server.mc.ask_ollama") as ollama, \
            mock.patch("assistant_server.mc.write_checkpoint"), \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"):
            result = assistant_server.answer_message("hello", "web")
        self.assertEqual("local-guarded", result["engine"])
        self.assertIn("Known:", result["answer"])
        ollama.assert_not_called()

    def test_ollama_is_opt_in_fallback(self) -> None:
        with self._mock_context(), \
            mock.patch.dict("assistant_server.os.environ", {"BBB_ASSISTANT_USE_OLLAMA": "1"}), \
            mock.patch("assistant_server.run_claude", return_value=None), \
            mock.patch("assistant_server.mc.ask_ollama", return_value="ollama answer"), \
            mock.patch("assistant_server.mc.write_checkpoint"), \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"):
            result = assistant_server.answer_message("hello", "web")
        self.assertEqual("ollama", result["engine"])
        self.assertIn("Known:", result["answer"])
        self.assertIn("ollama answer", result["answer"])

    def test_unavailable_when_both_models_fail(self) -> None:
        with self._mock_context(), \
            mock.patch("assistant_server.run_claude", return_value=None), \
            mock.patch("assistant_server.mc.ask_ollama", return_value=None), \
            mock.patch("assistant_server.mc.write_checkpoint"), \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"):
            result = assistant_server.answer_message("hello", "web")
        self.assertEqual("local-guarded", result["engine"])
        self.assertIn("Known:", result["answer"])

    def test_model_output_is_cleaned(self) -> None:
        dirty = "\x1b[?25lhello\x1b[0m\x08"
        self.assertEqual("hello", assistant_server._clean_model_output(dirty))

    def test_explicit_decision_message_creates_packet(self) -> None:
        with self._mock_context(), \
            mock.patch("assistant_server.run_claude", return_value="stored"), \
            mock.patch("assistant_server.mc.write_checkpoint"), \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"), \
            mock.patch(
                "assistant_server.mc.create_decision_packet", return_value="packet-1"
            ) as packet:
            result = assistant_server.answer_message(
                "decision: keep SQLite as local MC", "web"
            )
        self.assertEqual("packet-1", result["decision_packet_id"])
        packet.assert_called_once()


class CollectContextTestCase(unittest.TestCase):
    def test_collect_context_declares_role_and_reads_memory(self) -> None:
        with mock.patch("assistant_server.mc.init_db"), \
            mock.patch("assistant_server.mc.ensure_session") as ensure, \
            mock.patch("assistant_server.mc.set_role") as set_role, \
            mock.patch("assistant_server.mc.get_role", return_value="builder"), \
            mock.patch("assistant_server.mc.search_memory", return_value=[{"title": "kw"}]), \
            mock.patch("assistant_server.mc.semantic_search", return_value=[{"title": "sem"}]), \
            mock.patch("assistant_server.mc.graph_memory_search", return_value=[{"title": "graph"}]), \
            mock.patch("assistant_server.mc.list_tasks", return_value=[{"id": 1, "status": "todo"}]), \
            mock.patch("assistant_server.mc.latest_checkpoint", return_value={"risk": "low"}):
            context = assistant_server.collect_context("you are the builder", "web")
        ensure.assert_called_once()
        set_role.assert_called_once_with("web", "builder", db_path=assistant_server.mc.DB_PATH)
        self.assertEqual("builder", context["role"])
        self.assertEqual([{"title": "kw"}], context["keyword_memory"])
        self.assertEqual([{"title": "sem"}], context["semantic_memory"])
        self.assertEqual([{"title": "graph"}], context["graph_memory"])


class PromptTestCase(unittest.TestCase):
    def test_prompt_states_local_limits(self) -> None:
        prompt = assistant_server.build_prompt(
            "what can you do?",
            {
                "session_id": "web",
                "role": "brozzer",
                "permissions": {"code": False, "data": False},
                "checkpoint": None,
                "keyword_memory": [],
                "semantic_memory": [],
                "graph_memory": [],
                "open_tasks": [],
            },
        )
        self.assertIn("You cannot access HOSNI, voice", prompt)
        self.assertIn("You are a partial local Brozzer", prompt)
        self.assertIn("User message:", prompt)


class BuildStateTestCase(unittest.TestCase):
    def test_state_reports_claude_cli_availability(self) -> None:
        with mock.patch("assistant_server.mc.init_db"), \
            mock.patch("assistant_server.mc.ensure_session"), \
            mock.patch("assistant_server.mc.get_role", return_value="brozzer"), \
            mock.patch("assistant_server.mc.list_tasks", return_value=[]), \
            mock.patch("assistant_server.mc.latest_checkpoint", return_value=None), \
            mock.patch(
                "assistant_server.mc.list_capability_boundaries",
                return_value=[{"capability": "voice", "enabled": False, "reason": "not yet"}],
            ), \
            mock.patch("assistant_server.mc.list_turns", return_value=[]), \
            mock.patch("assistant_server.mc.list_decision_packets", return_value=[]), \
            mock.patch("assistant_server.mc.list_dispatch_runs", return_value=[]), \
            mock.patch("assistant_server.mc.list_agent_outputs", return_value=[]), \
            mock.patch("assistant_server.mc.list_events", return_value=[]), \
            mock.patch("assistant_server.mc.list_evidence", return_value=[]), \
            mock.patch("assistant_server.mc.list_answer_quality_scores", return_value=[]), \
            mock.patch("assistant_server.mc.list_answer_receipts", return_value=[]), \
            mock.patch("assistant_server.mc.list_lifecycle_runs", return_value=[]), \
            mock.patch("assistant_server.mc.graph_stats", return_value={"nodes": 0, "edges": 0}), \
            mock.patch("assistant_server.shutil.which", return_value=r"C:\bin\claude.CMD"):
            state = assistant_server.build_state("web")
        self.assertTrue(state["claude_cli_available"])
        self.assertFalse(state["voice"]["enabled"])
        self.assertEqual("partial-local-brozzer", state["brozzer_parity"]["status"])
        self.assertIn("mc_trace", state)


class HandlerTestCase(unittest.TestCase):
    def _make_handler(self, path: str) -> assistant_server.AssistantRequestHandler:
        handler = assistant_server.AssistantRequestHandler.__new__(
            assistant_server.AssistantRequestHandler
        )
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

    def test_get_root_serves_console_html(self) -> None:
        handler = self._make_handler("/")
        fake_console_path = mock.Mock()
        fake_console_path.read_bytes.return_value = b"<html></html>"
        with mock.patch("assistant_server.CONSOLE_HTML_PATH", fake_console_path):
            handler.do_GET()
        self.assertEqual([200], handler.responses)
        self.assertEqual([b"<html></html>"], handler.sent_html)

    def test_get_state_returns_json(self) -> None:
        handler = self._make_handler("/api/state")
        with mock.patch("assistant_server.build_state", return_value={"role": "brozzer"}):
            handler.do_GET()
        self.assertEqual([200], handler.responses)
        self.assertEqual({"role": "brozzer"}, handler.sent_json[0])

    def test_post_chat_returns_answer(self) -> None:
        handler = self._make_handler("/api/chat")
        payload = {"session_id": "web", "message": "hello"}
        handler.headers = {"Content-Length": str(len(json.dumps(payload)))}  # type: ignore[attr-defined]
        handler.rfile = io.BytesIO(json.dumps(payload).encode("utf-8"))  # type: ignore[attr-defined]
        with mock.patch(
            "assistant_server.answer_message",
            return_value={"answer": "hi", "engine": "claude-cli"},
        ):
            handler.do_POST()
        self.assertEqual([200], handler.responses)
        self.assertEqual("hi", handler.sent_json[0]["answer"])


if __name__ == "__main__":
    unittest.main()
