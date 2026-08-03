"""Tests for bin/assistant_server.py — mocked assistant surface, no real model calls."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
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

    def _mock_context(self, **overrides):
        base = {
            "session_id": "web",
            "role": "brozzer",
            "permissions": {"code": False, "data": False},
            "declared_role": None,
            "checkpoint": None,
            "keyword_memory": [],
            "semantic_memory": [],
            "graph_memory": [],
            "open_tasks": [],
            "health": "healthy",
            "session_score": {"overall_score": 83},
            "memory_conflicts": [],
        }
        base.update(overrides)
        return mock.patch("assistant_server.collect_context", return_value=base)

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
            ) as packet, \
            mock.patch("assistant_server.mc.log_lesson") as log_lesson:
            result = assistant_server.answer_message(
                "decision: keep SQLite as local MC", "web"
            )
        self.assertEqual("packet-1", result["decision_packet_id"])
        packet.assert_called_once()
        log_lesson.assert_called_once()

    def test_decision_message_also_writes_a_searchable_memory_lesson(self) -> None:
        """Stage AX: decision_packets alone isn't reachable via search_memory/
        semantic_search (those read memory_index) - the same text must also go
        through mc.log_lesson so a chat decision is actually findable later."""

        with self._mock_context(), \
            mock.patch("assistant_server.run_claude", return_value="stored"), \
            mock.patch("assistant_server.mc.write_checkpoint"), \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"), \
            mock.patch("assistant_server.mc.create_decision_packet", return_value="packet-1"), \
            mock.patch("assistant_server.mc.log_lesson") as log_lesson:
            assistant_server.answer_message("decision: keep SQLite as local MC", "web")

        log_lesson.assert_called_once_with(
            "chat decision: keep SQLite as local MC",
            "keep SQLite as local MC",
            confidence="medium",
            db_path=assistant_server.mc.DB_PATH,
        )


class HealthEventLoggingTestCase(AssistantAnswerTestCase):
    """Stage AU: write_checkpoint's existing fail-soft (never breaks the console
    reply) now also logs a health_events row - same swallow, now visible."""

    def test_write_checkpoint_failure_is_still_swallowed_and_now_logged(self) -> None:
        with self._mock_context(), \
            mock.patch("assistant_server.run_claude", return_value="claude answer"), \
            mock.patch("assistant_server.mc.ask_ollama"), \
            mock.patch(
                "assistant_server.mc.write_checkpoint", side_effect=RuntimeError("db locked")
            ), \
            mock.patch("assistant_server.mc.log_health_event") as health_log, \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"):
            result = assistant_server.answer_message("hello", "web")  # must not raise

        self.assertEqual("claude-cli", result["engine"])  # unaffected, same as before Stage AU
        health_log.assert_called_once()
        args, kwargs = health_log.call_args
        self.assertEqual("checkpoint", args[0])
        self.assertEqual("write_failed", args[1])
        self.assertEqual("web", kwargs.get("session_id"))

    def test_write_checkpoint_success_does_not_log_anything(self) -> None:
        with self._mock_context(), \
            mock.patch("assistant_server.run_claude", return_value="claude answer"), \
            mock.patch("assistant_server.mc.ask_ollama"), \
            mock.patch("assistant_server.mc.write_checkpoint"), \
            mock.patch("assistant_server.mc.log_health_event") as health_log, \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"):
            assistant_server.answer_message("hello", "web")
        health_log.assert_not_called()


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
            mock.patch("assistant_server.mc.latest_checkpoint", return_value={"risk": "low"}), \
            mock.patch("assistant_server.mc.get_health_state", return_value="healthy") as health, \
            mock.patch(
                "assistant_server.mc.session_score", return_value={"overall_score": 90}
            ) as score, \
            mock.patch("assistant_server.mc.detect_memory_conflicts") as conflicts:
            context = assistant_server.collect_context("you are the builder", "web")
        ensure.assert_called_once()
        set_role.assert_called_once_with("web", "builder", db_path=assistant_server.mc.DB_PATH)
        self.assertEqual("builder", context["role"])
        self.assertEqual([{"title": "kw"}], context["keyword_memory"])
        self.assertEqual([{"title": "sem"}], context["semantic_memory"])
        self.assertEqual([{"title": "graph"}], context["graph_memory"])
        self.assertEqual("healthy", context["health"])
        self.assertEqual({"overall_score": 90}, context["session_score"])
        self.assertEqual([], context["memory_conflicts"])
        health.assert_called_once()
        score.assert_called_once()
        # checkpoint has no 'current_task', so conflict detection must not even run
        conflicts.assert_not_called()

    def test_collect_context_checks_conflicts_when_checkpoint_has_current_task(self) -> None:
        with mock.patch("assistant_server.mc.init_db"), \
            mock.patch("assistant_server.mc.ensure_session"), \
            mock.patch("assistant_server.mc.set_role"), \
            mock.patch("assistant_server.mc.get_role", return_value="brozzer"), \
            mock.patch("assistant_server.mc.search_memory", return_value=[]), \
            mock.patch("assistant_server.mc.semantic_search", return_value=[]), \
            mock.patch("assistant_server.mc.graph_memory_search", return_value=[]), \
            mock.patch("assistant_server.mc.list_tasks", return_value=[]), \
            mock.patch(
                "assistant_server.mc.latest_checkpoint",
                return_value={"risk": "low", "current_task": "fix the bug"},
            ), \
            mock.patch("assistant_server.mc.get_health_state", return_value="healthy"), \
            mock.patch("assistant_server.mc.session_score", return_value={}), \
            mock.patch(
                "assistant_server.mc.detect_memory_conflicts", return_value=[{"reason": "x"}]
            ) as conflicts:
            context = assistant_server.collect_context("hello", "web")
        conflicts.assert_called_once_with("fix the bug", db_path=assistant_server.mc.DB_PATH)
        self.assertEqual([{"reason": "x"}], context["memory_conflicts"])


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


class TaskCommandParsingTestCase(unittest.TestCase):
    def test_task_prefix_extracts_title(self) -> None:
        self.assertEqual("fix the bug", assistant_server._task_add_title("task: fix the bug"))

    def test_gorev_prefix_extracts_title(self) -> None:
        self.assertEqual(
            "dokümantasyon yaz", assistant_server._task_add_title("görev: dokümantasyon yaz")
        )

    def test_no_task_prefix_returns_none(self) -> None:
        self.assertIsNone(assistant_server._task_add_title("just chatting about tasks"))

    def test_complete_task_english_pattern(self) -> None:
        self.assertEqual(7, assistant_server._task_complete_id("please complete task 7 now"))

    def test_complete_task_turkish_pattern(self) -> None:
        self.assertEqual(3, assistant_server._task_complete_id("3 numaralı görevi tamamla"))

    def test_no_complete_pattern_returns_none(self) -> None:
        self.assertIsNone(assistant_server._task_complete_id("what is task 7 about?"))

    def test_status_command_recognized(self) -> None:
        self.assertTrue(assistant_server._is_status_command("  Durum  "))
        self.assertTrue(assistant_server._is_status_command("status"))
        self.assertFalse(assistant_server._is_status_command("what is the status of x"))

    def test_confirm_task_with_test_extracts_id_and_detail(self) -> None:
        self.assertEqual(
            (5, "unit tests pass"),
            assistant_server._confirm_task_with_test_match(
                "confirm task 5 with test: unit tests pass"
            ),
        )

    def test_confirm_task_with_test_case_insensitive(self) -> None:
        self.assertEqual(
            (2, "pytest green"),
            assistant_server._confirm_task_with_test_match(
                "Confirm Task 2 WITH TEST: pytest green"
            ),
        )

    def test_no_confirm_task_pattern_returns_none(self) -> None:
        self.assertIsNone(
            assistant_server._confirm_task_with_test_match("complete task 7 now")
        )

    def test_start_task_english_pattern(self) -> None:
        self.assertEqual(4, assistant_server._task_start_id("please start task 4"))

    def test_start_task_turkish_pattern(self) -> None:
        self.assertEqual(6, assistant_server._task_start_id("işe başla 6"))

    def test_no_start_task_pattern_returns_none(self) -> None:
        self.assertIsNone(assistant_server._task_start_id("what is task 4 about?"))


class TaskCommandExecutionTestCase(unittest.TestCase):
    """Stage AV: task: / complete task N must call real mc.py functions, not just
    produce suggestion text."""

    def _mock_context(self, **overrides):
        base = {
            "session_id": "web",
            "role": "builder",
            "permissions": {"code": True, "data": True},
            "declared_role": None,
            "checkpoint": None,
            "keyword_memory": [],
            "semantic_memory": [],
            "graph_memory": [],
            "open_tasks": [{"id": 1, "status": "todo", "title": "existing"}],
            "health": "healthy",
            "session_score": {"overall_score": 83},
            "memory_conflicts": [],
        }
        base.update(overrides)
        return mock.patch("assistant_server.collect_context", return_value=base)

    def _common_patches(self):
        return [
            mock.patch("assistant_server.run_claude", return_value="ok"),
            mock.patch("assistant_server.mc.ask_ollama"),
            mock.patch("assistant_server.mc.write_checkpoint"),
            mock.patch("assistant_server.mc.record_turn"),
            mock.patch("assistant_server.mc.record_event"),
            mock.patch("assistant_server.mc.record_answer_quality", return_value=1),
            mock.patch("assistant_server.mc.record_answer_receipt", return_value=3),
        ]

    def test_task_add_message_calls_mc_add_task(self) -> None:
        patches = self._common_patches()
        with self._mock_context(), \
            mock.patch("assistant_server.mc.record_evidence", return_value=2), \
            mock.patch("assistant_server.mc.add_task", return_value=42) as add_task, \
            contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            result = assistant_server.answer_message("task: yeni test görevi", "web")

        add_task.assert_called_once_with(
            "yeni test görevi", session_id="web", db_path=assistant_server.mc.DB_PATH
        )
        self.assertIn("[task added, id=42]", result["answer"])

    def test_complete_task_command_calls_complete_task_with_real_params(self) -> None:
        patches = self._common_patches()
        with self._mock_context(), \
            mock.patch("assistant_server.mc.record_evidence", return_value=555) as record_evidence, \
            mock.patch(
                "assistant_server.mc.complete_task",
                return_value={"ok": True, "task_id": 1, "status": "done"},
            ) as complete_task, \
            contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            result = assistant_server.answer_message("complete task 1", "web")

        first_call = record_evidence.call_args_list[0]
        self.assertEqual("user_confirmation", first_call.args[0])
        complete_task.assert_called_once_with(
            1,
            session_id="web",
            evidence_id=555,
            completion_summary=mock.ANY,
            db_path=assistant_server.mc.DB_PATH,
        )
        self.assertIn("[task 1 completed]", result["answer"])

    def test_complete_task_guard_failure_is_shown_not_hidden(self) -> None:
        """The evidence recorded from chat is kind='user_confirmation', not a test
        run, so mc.py's require_test_evidence guard refuses it by design - and
        that refusal must reach the user verbatim, not be swallowed."""

        patches = self._common_patches()
        with self._mock_context(), \
            mock.patch("assistant_server.mc.record_evidence", return_value=1), \
            mock.patch(
                "assistant_server.mc.complete_task",
                side_effect=ValueError("completion evidence must include test execution"),
            ), \
            contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            result = assistant_server.answer_message("complete task 9", "web")

        self.assertIn(
            "[complete task failed: completion evidence must include test execution]",
            result["answer"],
        )

    def test_confirm_task_with_test_records_test_evidence_then_completes(self) -> None:
        """Stage AX: unlike 'complete task N' (user_confirmation, refused by the
        guard), 'confirm task N with test: ...' must record kind='test_run'
        evidence and pass that evidence into complete_task."""

        patches = self._common_patches()
        with self._mock_context(), \
            mock.patch("assistant_server.mc.record_evidence", return_value=777) as record_evidence, \
            mock.patch(
                "assistant_server.mc.complete_task",
                return_value={"ok": True, "task_id": 4, "status": "done"},
            ) as complete_task, \
            contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            result = assistant_server.answer_message(
                "confirm task 4 with test: pytest -k task_4 passed", "web"
            )

        first_call = record_evidence.call_args_list[0]
        self.assertEqual(
            mock.call(
                "test_run",
                "chat_test_4",
                "passed",
                details={
                    "session_id": "web",
                    "source": "assistant_console_chat",
                    "detail": "pytest -k task_4 passed",
                },
                db_path=assistant_server.mc.DB_PATH,
            ),
            first_call,
        )
        complete_task.assert_called_once_with(
            4,
            session_id="web",
            evidence_id=777,
            completion_summary="pytest -k task_4 passed",
            require_test_evidence=True,
            db_path=assistant_server.mc.DB_PATH,
        )
        self.assertIn("[task 4 completed via test evidence id=777]", result["answer"])

    def test_footer_appears_on_every_answer(self) -> None:
        patches = self._common_patches()
        with self._mock_context(
            health="degraded", session_score={"overall_score": 55}, memory_conflicts=[{"reason": "x"}]
        ), \
            mock.patch("assistant_server.mc.record_evidence", return_value=1), \
            contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            result = assistant_server.answer_message("hello", "web")

        self.assertIn("[health: degraded | score: 55/100 | conflicts: 1]", result["answer"])


class StatusCommandTestCase(unittest.TestCase):
    def _mock_context(self):
        return mock.patch(
            "assistant_server.collect_context",
            return_value={
                "session_id": "web",
                "role": "builder",
                "permissions": {"code": True, "data": True},
                "declared_role": None,
                "checkpoint": None,
                "keyword_memory": [],
                "semantic_memory": [],
                "graph_memory": [],
                "open_tasks": [{"id": 1, "status": "todo", "title": "t1"}],
                "health": "degraded",
                "session_score": {"overall_score": 70},
                "memory_conflicts": [{"reason": "conflict-x"}],
            },
        )

    def test_status_command_returns_full_detail_without_calling_the_model(self) -> None:
        with self._mock_context(), \
            mock.patch("assistant_server.run_claude") as claude, \
            mock.patch(
                "assistant_server.mc.list_health_events",
                return_value=[
                    {
                        "created_at": "t",
                        "component": "ollama",
                        "event": "unavailable",
                        "detail": None,
                    }
                ],
            ) as health_events, \
            mock.patch("assistant_server.mc.write_checkpoint"), \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"), \
            mock.patch("assistant_server.mc.record_answer_quality", return_value=1), \
            mock.patch("assistant_server.mc.record_evidence", return_value=1), \
            mock.patch("assistant_server.mc.record_answer_receipt", return_value=3):
            result = assistant_server.answer_message("status", "web")

        claude.assert_not_called()
        self.assertEqual("status-command", result["engine"])
        self.assertIn("STATUS REPORT", result["answer"])
        self.assertIn("#1 [todo] t1", result["answer"])
        self.assertIn("degraded", result["answer"])
        self.assertIn("conflict-x", result["answer"])
        health_events.assert_called_once_with(
            limit=3, session_id="web", db_path=assistant_server.mc.DB_PATH
        )


class ExistsCommandTestCase(unittest.TestCase):
    def _mock_context(self):
        return mock.patch(
            "assistant_server.collect_context",
            return_value={
                "session_id": "web",
                "role": "builder",
                "permissions": {"code": True, "data": True},
                "declared_role": None,
                "checkpoint": None,
                "keyword_memory": [],
                "semantic_memory": [],
                "graph_memory": [],
                "open_tasks": [],
                "health": "healthy",
                "session_score": {"overall_score": 90},
                "memory_conflicts": [],
            },
        )

    def test_exists_query_extracted_from_english_prefix(self) -> None:
        self.assertEqual(
            "task queue helper", assistant_server._exists_query("exists: task queue helper")
        )

    def test_exists_query_extracted_from_turkish_prefix(self) -> None:
        self.assertEqual(
            "gorev kuyrugu", assistant_server._exists_query("var mı: gorev kuyrugu")
        )

    def test_exists_query_none_without_prefix(self) -> None:
        self.assertIsNone(assistant_server._exists_query("does this exist somewhere"))

    def test_exists_command_calls_cyg_exists_without_calling_claude(self) -> None:
        with self._mock_context(), \
            mock.patch("assistant_server.run_claude") as claude, \
            mock.patch(
                "assistant_server.mc.cyg_exists",
                return_value={
                    "exists": True,
                    "score": 0.87,
                    "match": {
                        "symbol_name": "add_task",
                        "source_table": "symbol_index",
                    },
                },
            ) as cyg_exists, \
            mock.patch("assistant_server.mc.write_checkpoint"), \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"), \
            mock.patch("assistant_server.mc.record_answer_quality", return_value=1), \
            mock.patch("assistant_server.mc.record_evidence", return_value=1), \
            mock.patch("assistant_server.mc.record_answer_receipt", return_value=3):
            result = assistant_server.answer_message("exists: add_task helper", "web")

        claude.assert_not_called()
        self.assertEqual("exists-command", result["engine"])
        self.assertIn("exists: True (score: 0.870)", result["answer"])
        self.assertIn("add_task", result["answer"])
        cyg_exists.assert_called_once_with(
            "add_task helper", db_path=assistant_server.mc.DB_PATH
        )


class ProjectsCommandTestCase(unittest.TestCase):
    def _mock_context(self):
        return mock.patch(
            "assistant_server.collect_context",
            return_value={
                "session_id": "web",
                "role": "builder",
                "permissions": {"code": True, "data": True},
                "declared_role": None,
                "checkpoint": None,
                "keyword_memory": [],
                "semantic_memory": [],
                "graph_memory": [],
                "open_tasks": [],
                "health": "healthy",
                "session_score": {"overall_score": 90},
                "memory_conflicts": [],
            },
        )

    def test_projects_command_recognized(self) -> None:
        self.assertTrue(assistant_server._is_projects_command("  projects  "))
        self.assertTrue(assistant_server._is_projects_command("projeler"))
        self.assertFalse(assistant_server._is_projects_command("what are the projects here"))

    def test_projects_command_lists_projects_without_calling_claude(self) -> None:
        with self._mock_context(), \
            mock.patch("assistant_server.run_claude") as claude, \
            mock.patch(
                "assistant_server.mc.list_projects",
                return_value=[{"id": 1, "name": "bbb-lite", "status": "active"}],
            ) as list_projects, \
            mock.patch("assistant_server.mc.write_checkpoint"), \
            mock.patch("assistant_server.mc.record_turn"), \
            mock.patch("assistant_server.mc.record_event"), \
            mock.patch("assistant_server.mc.record_answer_quality", return_value=1), \
            mock.patch("assistant_server.mc.record_evidence", return_value=1), \
            mock.patch("assistant_server.mc.record_answer_receipt", return_value=3):
            result = assistant_server.answer_message("projects", "web")

        claude.assert_not_called()
        self.assertEqual("projects-command", result["engine"])
        self.assertIn("#1 bbb-lite [active]", result["answer"])
        list_projects.assert_called_once_with(db_path=assistant_server.mc.DB_PATH)


class DecisionSearchableIntegrationTestCase(unittest.TestCase):
    """Stage AX, no-mock proof: a chat 'decision: ...' message must end up
    findable through the real mc.search_memory(), not just recorded as a
    decision_packets row. Uses a real temp db and a real (isolated) memory/
    directory - mc.log_lesson's memory_dir is hardcoded relative to mc.py's
    own file, so mc.__file__ is patched to point into a temp project dir,
    exactly like test_mc.py's log_lesson tests do."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        assistant_server.mc.init_db(self.db_path)

        self.project_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.project_dir.cleanup)
        (Path(self.project_dir.name) / "memory").mkdir()
        patcher = mock.patch.object(
            assistant_server.mc, "__file__", str(Path(self.project_dir.name) / "mc.py")
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _mock_context(self):
        return mock.patch(
            "assistant_server.collect_context",
            return_value={
                "session_id": "web",
                "role": "builder",
                "permissions": {"code": True, "data": True},
                "declared_role": None,
                "checkpoint": None,
                "keyword_memory": [],
                "semantic_memory": [],
                "graph_memory": [],
                "open_tasks": [],
                "health": "healthy",
                "session_score": {"overall_score": 90},
                "memory_conflicts": [],
            },
        )

    def test_decision_command_is_findable_via_search_memory(self) -> None:
        with self._mock_context(), mock.patch("assistant_server.run_claude", return_value="stored"):
            assistant_server.answer_message(
                "decision: freeze the schema until Stage AX ships",
                "web",
                db_path=self.db_path,
            )

        hits = assistant_server.mc.search_memory(
            "freeze schema Stage AX ships", db_path=self.db_path
        )
        self.assertTrue(hits, "decision text should be indexed and searchable")
        self.assertTrue(
            any("freeze the schema until Stage AX ships" in hit["content"] for hit in hits)
        )


class ConfirmTaskWithTestGuardIntegrationTestCase(unittest.TestCase):
    """Stage AX, no-mock proof: 'confirm task N with test: ...' must genuinely
    pass complete_task's require_test_evidence guard (real evidence row, real
    task row), not just call mocked functions successfully."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        assistant_server.mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _mock_context(self, **overrides):
        base = {
            "session_id": "web",
            "role": "builder",
            "permissions": {"code": True, "data": True},
            "declared_role": None,
            "checkpoint": None,
            "keyword_memory": [],
            "semantic_memory": [],
            "graph_memory": [],
            "open_tasks": [],
            "health": "healthy",
            "session_score": {"overall_score": 90},
            "memory_conflicts": [],
        }
        base.update(overrides)
        return mock.patch("assistant_server.collect_context", return_value=base)

    def test_confirm_task_with_test_really_completes_task_through_the_guard(self) -> None:
        task_id = assistant_server.mc.add_task(
            "ship stage AX", session_id="web", status="in_progress", db_path=self.db_path
        )

        with self._mock_context(), mock.patch("assistant_server.run_claude", return_value="ok"):
            result = assistant_server.answer_message(
                f"confirm task {task_id} with test: pytest full suite green",
                "web",
                db_path=self.db_path,
            )

        self.assertIn(f"[task {task_id} completed via test evidence id=", result["answer"])
        tasks = {t["id"]: t for t in assistant_server.mc.list_tasks(db_path=self.db_path)}
        self.assertEqual("done", tasks[task_id]["status"])

    def test_old_complete_task_command_is_still_refused_by_the_guard(self) -> None:
        """Confirms the two paths stay distinct: plain 'complete task N' has no
        test evidence and must still be refused, exactly as before Stage AX."""

        task_id = assistant_server.mc.add_task(
            "ship stage AX v2", session_id="web", status="in_progress", db_path=self.db_path
        )

        with self._mock_context(), mock.patch("assistant_server.run_claude", return_value="ok"):
            result = assistant_server.answer_message(
                f"complete task {task_id}", "web", db_path=self.db_path
            )

        self.assertIn("[complete task failed:", result["answer"])
        tasks = {t["id"]: t for t in assistant_server.mc.list_tasks(db_path=self.db_path)}
        self.assertEqual("in_progress", tasks[task_id]["status"])

    def test_start_task_then_confirm_with_test_completes_the_full_chat_loop(self) -> None:
        """Stage AY, no-mock proof: 'start task N' really moves a fresh 'todo'
        task to 'in_progress' through the chat surface, and 'confirm task N
        with test: ...' can then complete it - the full todo -> in_progress
        -> done loop, entirely via chat."""

        task_id = assistant_server.mc.add_task(
            "ship stage AY", session_id="web", db_path=self.db_path
        )  # default status="todo"

        with self._mock_context(), mock.patch("assistant_server.run_claude", return_value="ok"):
            start_result = assistant_server.answer_message(
                f"start task {task_id}", "web", db_path=self.db_path
            )
        self.assertIn(f"[task {task_id} started]", start_result["answer"])
        tasks = {t["id"]: t for t in assistant_server.mc.list_tasks(db_path=self.db_path)}
        self.assertEqual("in_progress", tasks[task_id]["status"])

        with self._mock_context(), mock.patch("assistant_server.run_claude", return_value="ok"):
            confirm_result = assistant_server.answer_message(
                f"confirm task {task_id} with test: pytest full suite green",
                "web",
                db_path=self.db_path,
            )
        self.assertIn(
            f"[task {task_id} completed via test evidence id=", confirm_result["answer"]
        )
        tasks = {t["id"]: t for t in assistant_server.mc.list_tasks(db_path=self.db_path)}
        self.assertEqual("done", tasks[task_id]["status"])

    def test_starting_an_already_in_progress_task_via_chat_is_refused(self) -> None:
        task_id = assistant_server.mc.add_task(
            "ship stage AY v2", session_id="web", status="in_progress", db_path=self.db_path
        )

        with self._mock_context(), mock.patch("assistant_server.run_claude", return_value="ok"):
            result = assistant_server.answer_message(
                f"start task {task_id}", "web", db_path=self.db_path
            )

        self.assertIn("[start task failed:", result["answer"])
        tasks = {t["id"]: t for t in assistant_server.mc.list_tasks(db_path=self.db_path)}
        self.assertEqual("in_progress", tasks[task_id]["status"])


if __name__ == "__main__":
    unittest.main()
