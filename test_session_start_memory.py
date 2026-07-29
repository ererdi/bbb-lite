"""Tests for Stage S: session_start.py's related-memory nudge.

Fail-soft on purpose (unlike Stage R): this is a convenience recall, not a
recovery proof, so a broken embedding model must not crash session start —
it should just skip the line. See memory/decisions.md.

Same db-binding approach as test_role_hooks.py / test_compact_recovery.py:
mc's functions default to db_path=mc.DB_PATH, bound at definition time, so
they're replaced with functools.partial wrappers pinned to a temp db.
memory_dir is faked the same way test_mc.py's SemanticSearchTestCase does —
by patching mc.__file__ into a temp project dir — so index_memory_files()
never touches the real memory/ directory.
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))

import mc
import session_start


_BOUND_FUNCS = (
    "ensure_session",
    "record_lifecycle_run",
    "get_role",
    "can_write_code",
    "can_write_data",
    "list_open_tasks",
    "latest_checkpoint",
    "semantic_search",
    "list_sessions",
    "recent_decisions",
    "recovery_package_size",
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


class SessionStartRelatedMemoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)
        self.session_id = "s-stage-s"
        mc.ensure_session(self.session_id, db_path=self.db_path)

        self.project_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.project_dir.cleanup)
        memory_dir = Path(self.project_dir.name) / "memory"
        memory_dir.mkdir()
        patcher = mock.patch.object(mc, "__file__", str(Path(self.project_dir.name) / "mc.py"))
        patcher.start()
        self.addCleanup(patcher.stop)
        (memory_dir / "guard.md").write_text(
            "---\ntitle: Guard blocks writes\n---\n\nthe guard blocks unsafe database writes\n",
            encoding="utf-8",
        )

        try:
            mc._get_embedding_model()
        except Exception as exc:  # noqa: BLE001 - can't test this without the model
            self.skipTest(f"embedding model unavailable (no network and no local cache?): {exc}")
        mc.index_memory_files(db_path=self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_matching_current_task_prints_related_memory(self) -> None:
        mc.write_checkpoint(
            self.session_id,
            current_task="the guard blocks unsafe database writes",
            db_path=self.db_path,
        )
        with _bind_to_test_db(self.db_path):
            output = _run_session_start(self.session_id)
        self.assertIn(
            "[bbb-lite] related memory: Guard blocks writes (guard.md, confidence=0.5)", output
        )

    def test_empty_current_task_prints_nothing_and_skips_search(self) -> None:
        mc.write_checkpoint(self.session_id, current_task=None, db_path=self.db_path)
        with _bind_to_test_db(self.db_path), mock.patch("mc.semantic_search") as search_mock:
            output = _run_session_start(self.session_id)
        search_mock.assert_not_called()
        self.assertNotIn("related memory", output)

    def test_semantic_search_failure_does_not_crash_session_start(self) -> None:
        mc.write_checkpoint(
            self.session_id,
            current_task="the guard blocks unsafe database writes",
            db_path=self.db_path,
        )
        with _bind_to_test_db(self.db_path), \
            mock.patch("mc.semantic_search", side_effect=RuntimeError("model unavailable")):
            output = _run_session_start(self.session_id)  # must not raise
        self.assertNotIn("related memory", output)
        self.assertIn(f"[bbb-lite] session {self.session_id}", output)


if __name__ == "__main__":
    unittest.main()
