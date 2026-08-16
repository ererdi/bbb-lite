"""Tests for Stage AU: failure visibility (Haroon's team doc item 8).

log_health_event()/get_health_state() only make EXISTING fail-soft points
visible - none of them change whether a caller crashes. ask_ollama() is the
easiest of the four wired-up points to exercise without the embedding model
or a live memory_dir, so most of the direct-failure coverage below uses it;
session_start's own wiring is covered separately.
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mc

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import session_start  # noqa: E402 - path insert must run first


class HealthStateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_healthy_when_no_events(self) -> None:
        self.assertEqual("healthy", mc.get_health_state(db_path=self.db_path))

    def test_degraded_on_a_single_event(self) -> None:
        mc.log_health_event("ollama", "unavailable", db_path=self.db_path)
        self.assertEqual("degraded", mc.get_health_state(db_path=self.db_path))

    def test_degraded_stays_up_to_threshold(self) -> None:
        for _ in range(3):
            mc.log_health_event("ollama", "unavailable", db_path=self.db_path)
        self.assertEqual("degraded", mc.get_health_state(db_path=self.db_path))

    def test_blocked_when_more_than_threshold(self) -> None:
        for _ in range(4):
            mc.log_health_event("ollama", "unavailable", db_path=self.db_path)
        self.assertEqual("blocked", mc.get_health_state(db_path=self.db_path))

    def test_session_scoping_ignores_other_sessions_events(self) -> None:
        mc.log_health_event("ollama", "unavailable", session_id="s-1", db_path=self.db_path)
        self.assertEqual(
            "healthy", mc.get_health_state(session_id="s-2", db_path=self.db_path)
        )
        self.assertEqual(
            "degraded", mc.get_health_state(session_id="s-1", db_path=self.db_path)
        )

    def test_list_health_events_returns_most_recent_first_then_reversed(self) -> None:
        mc.log_health_event("ollama", "unavailable", db_path=self.db_path)
        mc.log_health_event("checkpoint", "write_failed", db_path=self.db_path)
        mc.log_health_event("db", "write_failed", db_path=self.db_path)

        events = mc.list_health_events(limit=2, db_path=self.db_path)

        self.assertEqual(2, len(events))
        self.assertEqual(["checkpoint", "db"], [e["component"] for e in events])

    def test_list_health_events_scopes_to_session(self) -> None:
        mc.log_health_event("ollama", "unavailable", session_id="s-1", db_path=self.db_path)
        mc.log_health_event("db", "write_failed", session_id="s-2", db_path=self.db_path)

        events = mc.list_health_events(session_id="s-1", db_path=self.db_path)

        self.assertEqual(1, len(events))
        self.assertEqual("ollama", events[0]["component"])

    def test_log_health_event_never_raises_when_db_write_fails(self) -> None:
        bad_db_path = Path(tempfile.mkdtemp())  # a directory, not a file - connect() must fail
        try:
            mc.log_health_event("db", "write_failed", db_path=bad_db_path)  # must not raise
        finally:
            bad_db_path.rmdir()

    def test_ask_ollama_failure_logs_health_event_and_degrades_state(self) -> None:
        with mock.patch("mc.subprocess.run", side_effect=FileNotFoundError()):
            result = mc.ask_ollama("hello", db_path=self.db_path)
        self.assertIsNone(result)

        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute("SELECT component, event FROM health_events").fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual("ollama", rows[0]["component"])

        self.assertEqual("degraded", mc.get_health_state(db_path=self.db_path))

    def test_ask_ollama_success_does_not_log_anything(self) -> None:
        completed = subprocess.CompletedProcess(args=["ollama"], returncode=0, stdout="yes\n")
        with mock.patch("mc.subprocess.run", return_value=completed):
            result = mc.ask_ollama("hello", db_path=self.db_path)
        self.assertEqual("yes", result)
        self.assertEqual("healthy", mc.get_health_state(db_path=self.db_path))


_BOUND_FUNCS = (
    "ensure_session",
    "run_maintenance_checks",
    "record_lifecycle_run",
    "get_role",
    "can_write_code",
    "can_write_data",
    "list_open_tasks",
    "latest_checkpoint",
    "semantic_search",
    "detect_memory_conflicts",
    "list_sessions",
    "recent_decisions",
    "recovery_package_size",
    "get_health_state",
)


def _bind_to_test_db(db_path: Path) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    for name in _BOUND_FUNCS:
        bound = functools.partial(getattr(mc, name), db_path=db_path)
        stack.enter_context(mock.patch(f"mc.{name}", bound))
    stack.enter_context(mock.patch("session_start.mc.init_db"))
    return stack


def _run_session_start(session_id: str, source: str = "startup") -> str:
    payload = json.dumps({"session_id": session_id, "source": source})
    buffer = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(buffer):
        session_start.main()
    return buffer.getvalue()


class SessionStartHealthWarningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.session_id = "s-stage-au"
        mc.ensure_session(self.session_id, db_path=self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_session_start_silent_when_healthy(self) -> None:
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertNotIn("SYSTEM HEALTH", output)

    def test_session_start_prints_warning_when_degraded(self) -> None:
        mc.log_health_event("ollama", "unavailable", db_path=self.db_path)
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertIn(
            "[bbb-lite] SYSTEM HEALTH: degraded - see health_events for details", output
        )

    def test_session_start_prints_warning_when_blocked(self) -> None:
        for _ in range(4):
            mc.log_health_event("db", "write_failed", db_path=self.db_path)
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertIn(
            "[bbb-lite] SYSTEM HEALTH: blocked - see health_events for details", output
        )

    def test_session_start_health_check_is_fail_soft(self) -> None:
        with _bind_to_test_db(self.db_path), \
            mock.patch("mc.get_health_state", side_effect=RuntimeError("boom")):
            output = _run_session_start(self.session_id)  # must not raise
        self.assertNotIn("SYSTEM HEALTH", output)
        self.assertIn(f"[bbb-lite] session {self.session_id}", output)


class IndexMemoryFilesHealthTestCase(unittest.TestCase):
    """A single unreadable file must not abort the rest of the sweep - just skip+log it."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

        self.project_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.project_dir.cleanup)
        self.memory_dir = Path(self.project_dir.name) / "memory"
        self.memory_dir.mkdir()
        patcher = mock.patch.object(mc, "__file__", str(Path(self.project_dir.name) / "mc.py"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_bad_file_is_skipped_logged_and_does_not_block_the_rest(self) -> None:
        (self.memory_dir / "good.md").write_text(
            "---\ntitle: Good note\n---\n\nfine content\n", encoding="utf-8"
        )
        (self.memory_dir / "bad.md").write_bytes(b"\xff\xfe\x00not valid utf-8")

        count = mc.index_memory_files(db_path=self.db_path)

        self.assertEqual(1, count)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            indexed = {
                row["source_file"]
                for row in conn.execute("SELECT source_file FROM memory_index").fetchall()
            }
        self.assertEqual({"good.md"}, indexed)

        with contextlib.closing(mc._connect(self.db_path)) as conn:
            health_rows = conn.execute(
                "SELECT component, event, detail FROM health_events"
            ).fetchall()
        self.assertEqual(1, len(health_rows))
        self.assertEqual("memory_index", health_rows[0]["component"])
        self.assertEqual("file_skipped", health_rows[0]["event"])
        self.assertIn("bad.md", health_rows[0]["detail"])
        self.assertEqual("degraded", mc.get_health_state(db_path=self.db_path))


class EmbeddingModelHealthTestCase(unittest.TestCase):
    """_get_embedding_model()'s module-global cache/flag must be restored after
    forcing a failure - otherwise every other test needing real embeddings in
    this process would start skipping."""

    def setUp(self) -> None:
        self._saved_model = mc._embedding_model
        self._saved_unavailable = mc._embedding_unavailable

    def tearDown(self) -> None:
        mc._embedding_model = self._saved_model
        mc._embedding_unavailable = self._saved_unavailable

    def test_load_failure_logs_health_event_and_still_raises(self) -> None:
        mc._embedding_model = None
        mc._embedding_unavailable = False
        with mock.patch("mc.log_health_event") as health_log, \
            mock.patch(
                "sentence_transformers.SentenceTransformer",
                side_effect=RuntimeError("no network"),
            ):
            with self.assertRaises(RuntimeError):
                mc._get_embedding_model()

        health_log.assert_called_once()
        args, kwargs = health_log.call_args
        self.assertEqual("embedding_model", args[0])
        self.assertEqual("load_failed", args[1])
        self.assertTrue(mc._embedding_unavailable)


if __name__ == "__main__":
    unittest.main()
