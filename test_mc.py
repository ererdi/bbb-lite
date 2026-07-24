"""Tests for mc.py — the only file allowed to touch the database."""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mc


class McTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()  # let sqlite create it fresh
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_init_db_creates_tables(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
        self.assertEqual(
            {"sessions", "checkpoints", "tasks", "memory_index", "task_activity"}, names
        )

    def test_ensure_session_is_idempotent(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.ensure_session("s1", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = ?", ("s1",)
            ).fetchone()[0]
        self.assertEqual(1, count)

    def test_write_and_read_latest_checkpoint(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="first", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="second", db_path=self.db_path)
        latest = mc.latest_checkpoint("s1", db_path=self.db_path)
        self.assertEqual("second", latest["current_task"])

    def test_latest_checkpoint_falls_back_when_session_has_none(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.write_checkpoint("s1", current_task="only one", db_path=self.db_path)
        latest = mc.latest_checkpoint("unknown-session", db_path=self.db_path)
        self.assertEqual("only one", latest["current_task"])

    def test_add_and_list_open_tasks(self) -> None:
        mc.add_task("write tests", db_path=self.db_path)
        mc.add_task("ship it", db_path=self.db_path)
        titles = {task["title"] for task in mc.list_open_tasks(db_path=self.db_path)}
        self.assertEqual({"write tests", "ship it"}, titles)

    def test_complete_task_marks_done_and_hides_from_open_list(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        result = mc.complete_task(task_id, db_path=self.db_path)
        self.assertEqual("done", result["status"])
        self.assertEqual([], mc.list_open_tasks(db_path=self.db_path))

    def test_complete_task_unknown_id_refused(self) -> None:
        with self.assertRaises(mc.MCCompletionRefused):
            mc.complete_task(999, db_path=self.db_path)

    def test_complete_task_twice_refused(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        mc.complete_task(task_id, db_path=self.db_path)
        with self.assertRaises(mc.MCCompletionRefused):
            mc.complete_task(task_id, db_path=self.db_path)

    def test_add_task_with_done_status_refused(self) -> None:
        with self.assertRaises(mc.MCCompletionRefused):
            mc.add_task("sneaky", status="done", db_path=self.db_path)

    def test_raw_update_to_done_refused(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        with self.assertRaises(mc.MCCompletionRefused):
            with contextlib.closing(mc._connect(self.db_path)) as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,)
                )

    def test_raw_update_to_non_done_status_allowed(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'in_progress' WHERE id = ?", (task_id,)
            )
            conn.commit()
        titles = [
            task["status"] for task in mc.list_open_tasks(db_path=self.db_path)
        ]
        self.assertEqual(["in_progress"], titles)

    def test_pick_next_action_none_without_open_tasks(self) -> None:
        self.assertIsNone(mc.pick_next_action(db_path=self.db_path))

    def test_pick_next_action_returns_oldest_open_task(self) -> None:
        mc.add_task("first task", db_path=self.db_path)
        mc.add_task("second task", db_path=self.db_path)
        self.assertEqual("Next: first task", mc.pick_next_action(db_path=self.db_path))

    def test_pick_next_action_skips_done_tasks(self) -> None:
        first_id = mc.add_task("first task", db_path=self.db_path)
        mc.add_task("second task", db_path=self.db_path)
        mc.complete_task(first_id, db_path=self.db_path)
        self.assertEqual("Next: second task", mc.pick_next_action(db_path=self.db_path))

    def test_has_completed_task_since_false_without_completion(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.add_task("write tests", db_path=self.db_path)
        self.assertFalse(mc.has_completed_task_since("s1", db_path=self.db_path))

    def test_has_completed_task_since_true_after_complete_task(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task("write tests", db_path=self.db_path)
        mc.complete_task(task_id, db_path=self.db_path)
        self.assertTrue(mc.has_completed_task_since("s1", db_path=self.db_path))

    def test_has_completed_task_since_false_for_unknown_session(self) -> None:
        self.assertFalse(mc.has_completed_task_since("no-such-session", db_path=self.db_path))

    def test_add_task_without_session_id_writes_no_activity(self) -> None:
        mc.add_task("write tests", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM task_activity").fetchone()[0]
        self.assertEqual(0, count)

    def test_add_task_with_session_id_records_created_activity(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task("write tests", session_id="s1", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT task_id, session_id, activity FROM task_activity"
            ).fetchone()
        self.assertEqual((task_id, "s1", "created"), tuple(row))

    def test_complete_task_without_session_id_writes_no_activity(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        mc.complete_task(task_id, db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM task_activity").fetchone()[0]
        self.assertEqual(0, count)

    def test_complete_task_with_session_id_records_completed_activity(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        task_id = mc.add_task("write tests", session_id="s1", db_path=self.db_path)
        mc.complete_task(task_id, session_id="s1", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            activities = {
                row[0]
                for row in conn.execute(
                    "SELECT activity FROM task_activity WHERE task_id = ?", (task_id,)
                ).fetchall()
            }
        self.assertEqual({"created", "completed"}, activities)

    def test_tasks_touched_by_session_dedupes_and_flags_completion(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        done_id = mc.add_task("finish report", session_id="s1", db_path=self.db_path)
        open_id = mc.add_task("review pr", session_id="s1", db_path=self.db_path)
        mc.complete_task(done_id, session_id="s1", db_path=self.db_path)

        touched = mc.tasks_touched_by_session("s1", db_path=self.db_path)
        by_id = {task["task_id"]: task for task in touched}

        self.assertEqual({done_id, open_id}, set(by_id))
        self.assertTrue(by_id[done_id]["completed_by_session"])
        self.assertFalse(by_id[open_id]["completed_by_session"])

    def test_tasks_touched_by_session_empty_for_unknown_session(self) -> None:
        self.assertEqual(
            [], mc.tasks_touched_by_session("no-such-session", db_path=self.db_path)
        )

    def test_mark_session_completed_updates_status(self) -> None:
        mc.ensure_session("s1", db_path=self.db_path)
        mc.mark_session_completed("s1", db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE session_id = ?", ("s1",)
            ).fetchone()
        self.assertEqual("completed", row["status"])

    def test_cursor_cannot_bypass_the_guard(self) -> None:
        task_id = mc.add_task("write tests", db_path=self.db_path)
        with self.assertRaises(mc.MCCompletionRefused):
            with contextlib.closing(mc._connect(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,)
                )


class MemoryHelperTestCase(unittest.TestCase):
    """Stage C: pure helpers — no filesystem or DB involved."""

    def test_significant_words_drops_stopwords_and_short_words(self) -> None:
        words = mc._significant_words("The Guard Blocks Raw Done Writes via SQL")
        self.assertEqual(["guard", "blocks", "done", "writes"], words)

    def test_extract_keywords_dedupes_in_order_and_caps_at_limit(self) -> None:
        text = "guard guard blocks blocks writes checkpoint session task memory index"
        keywords = mc._extract_keywords(text, limit=3)
        self.assertEqual("guard, blocks, writes", keywords)


class ParseMemoryFileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def test_parse_memory_file_uses_frontmatter_title(self) -> None:
        path = Path(self.tmp_dir.name) / "note.md"
        path.write_text(
            "---\ntitle: Guard blocks done\ndate: 2026-07-21\n---\n\nBody text here.\n",
            encoding="utf-8",
        )
        title, body = mc._parse_memory_file(path)
        self.assertEqual("Guard blocks done", title)
        self.assertEqual("Body text here.", body)

    def test_parse_memory_file_without_frontmatter_uses_filename(self) -> None:
        path = Path(self.tmp_dir.name) / "plain-note.md"
        path.write_text("Just some body text.\n", encoding="utf-8")
        title, body = mc._parse_memory_file(path)
        self.assertEqual("plain-note", title)
        self.assertEqual("Just some body text.", body)


class MemoryIndexTestCase(unittest.TestCase):
    """Stage C: index_memory_files / search_memory / log_lesson.

    memory_dir in mc.py is hardcoded to Path(__file__).resolve().parent / "memory",
    so mc.__file__ is patched per test to point into an isolated temp directory —
    this must never touch the project's real memory/ directory.
    """

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

    def _write_note(self, filename: str, title: str, body: str) -> None:
        (self.memory_dir / filename).write_text(
            f"---\ntitle: {title}\n---\n\n{body}\n", encoding="utf-8"
        )

    def test_index_memory_files_returns_count_and_populates_index(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        self._write_note("b.md", "Checkpoint note", "Checkpoints record the current task.")
        count = mc.index_memory_files(db_path=self.db_path)
        self.assertEqual(2, count)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute("SELECT source_file, title FROM memory_index").fetchall()
        self.assertEqual(
            {"a.md", "b.md"}, {row["source_file"] for row in rows}
        )

    def test_index_memory_files_upserts_without_duplicating_on_rerun(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        self._write_note("a.md", "Guard note updated", "Updated body about the guard.")
        count = mc.index_memory_files(db_path=self.db_path)
        self.assertEqual(1, count)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT title FROM memory_index WHERE source_file = 'a.md'"
            ).fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual("Guard note updated", rows[0]["title"])

    def test_search_memory_ranks_higher_keyword_count_first(self) -> None:
        self._write_note(
            "a.md", "Checkpoint basics", "Checkpoints record the current task once."
        )
        self._write_note(
            "b.md",
            "Checkpoint deep dive",
            "Checkpoint checkpoint checkpoint — checkpoints, checkpoints everywhere.",
        )
        mc.index_memory_files(db_path=self.db_path)
        results = mc.search_memory("checkpoint", db_path=self.db_path)
        self.assertEqual("b.md", results[0]["source_file"])

    def test_search_memory_title_hit_ranks_above_content_only_hit(self) -> None:
        self._write_note(
            "title_and_content.md",
            "Gizmo overview",
            "The gizmo behaves as expected in most scenarios.",
        )
        self._write_note(
            "content_only.md",
            "Widget overview",
            "The gizmo behaves as expected in most scenarios.",
        )
        mc.index_memory_files(db_path=self.db_path)
        results = mc.search_memory("gizmo", db_path=self.db_path)
        self.assertEqual("title_and_content.md", results[0]["source_file"])

    def test_search_memory_no_match_returns_empty(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        self.assertEqual([], mc.search_memory("nonexistentword", db_path=self.db_path))

    def test_search_memory_respects_limit(self) -> None:
        for i in range(5):
            self._write_note(f"note{i}.md", f"Note {i}", "session session session")
        mc.index_memory_files(db_path=self.db_path)
        results = mc.search_memory("session", limit=2, db_path=self.db_path)
        self.assertEqual(2, len(results))

    def test_log_lesson_writes_new_file_when_no_similar_note_exists(self) -> None:
        path = mc.log_lesson(
            "Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path
        )
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertIn("guard-blocks-done", path.name)
        content = path.read_text(encoding="utf-8")
        self.assertIn("title: Guard blocks done", content)
        self.assertIn("confidence: medium", content)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memory_index").fetchone()[0]
        self.assertEqual(1, count)

    def test_log_lesson_avoids_filename_collision(self) -> None:
        first = mc.log_lesson("Same title", "First body.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama", return_value="no"):
            second = mc.log_lesson("Same title", "Totally different unrelated body.", db_path=self.db_path)
        self.assertNotEqual(first.name, second.name)

    def test_log_lesson_skipped_when_ollama_confirms_duplicate(self) -> None:
        mc.log_lesson("Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama", return_value="Yes, same fact.") as mocked:
            result = mc.log_lesson(
                "Guard blocks done writes",
                "The guard refuses raw done writes too.",
                db_path=self.db_path,
            )
        self.assertIsNone(result)
        mocked.assert_called_once()
        self.assertEqual(1, len(list(self.memory_dir.glob("lesson-*.md"))))

    def test_log_lesson_kept_when_ollama_says_not_duplicate(self) -> None:
        mc.log_lesson("Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama", return_value="no"):
            result = mc.log_lesson(
                "Checkpoints have fallback",
                "latest_checkpoint falls back to the newest checkpoint overall.",
                db_path=self.db_path,
            )
        self.assertIsNotNone(result)
        self.assertEqual(2, len(list(self.memory_dir.glob("lesson-*.md"))))

    def test_log_lesson_kept_when_ollama_unavailable(self) -> None:
        mc.log_lesson("Guard blocks done", "The guard refuses raw done writes.", db_path=self.db_path)
        with mock.patch("mc.ask_ollama", return_value=None):
            result = mc.log_lesson(
                "Guard blocks done again",
                "The guard refuses raw done writes, restated.",
                db_path=self.db_path,
            )
        self.assertIsNotNone(result)


class AskOllamaTestCase(unittest.TestCase):
    """Stage C: ask_ollama must never raise — fail-soft to None."""

    def test_returns_none_when_binary_missing(self) -> None:
        with mock.patch("mc.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(mc.ask_ollama("hello"))

    def test_returns_none_on_timeout(self) -> None:
        with mock.patch(
            "mc.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ollama", timeout=30),
        ):
            self.assertIsNone(mc.ask_ollama("hello"))

    def test_returns_none_on_nonzero_exit(self) -> None:
        completed = subprocess.CompletedProcess(args=["ollama"], returncode=1, stdout="")
        with mock.patch("mc.subprocess.run", return_value=completed):
            self.assertIsNone(mc.ask_ollama("hello"))

    def test_returns_none_on_blank_stdout(self) -> None:
        completed = subprocess.CompletedProcess(args=["ollama"], returncode=0, stdout="   \n")
        with mock.patch("mc.subprocess.run", return_value=completed):
            self.assertIsNone(mc.ask_ollama("hello"))

    def test_returns_stripped_stdout_on_success(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["ollama"], returncode=0, stdout="  yes, same fact  \n"
        )
        with mock.patch("mc.subprocess.run", return_value=completed) as mocked:
            self.assertEqual("yes, same fact", mc.ask_ollama("hello", model="gemma:2b"))
        args, kwargs = mocked.call_args
        self.assertEqual(["ollama", "run", "gemma:2b", "hello"], args[0])


if __name__ == "__main__":
    unittest.main()
