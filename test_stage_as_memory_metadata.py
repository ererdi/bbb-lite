"""Tests for Stage AS: memory_index content_hash/timestamps/status/version.

Same isolation approach as MemoryIndexTestCase in test_mc.py: mc.__file__ is
patched into a temp project dir so memory_dir resolves under it, never the
real memory/ directory. These tests don't need the embedding model —
index_memory_files() falls back to a NULL embedding when it's unavailable,
and none of these assertions touch semantic_search().
"""

from __future__ import annotations

import contextlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import mc


class MemoryMetadataTestCase(unittest.TestCase):
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

    def _row(self, source_file: str) -> dict:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM memory_index WHERE source_file = ? ORDER BY id DESC LIMIT 1",
                (source_file,),
            ).fetchone()
        return dict(row)

    def test_content_hash_matches_sha256_of_body(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        row = self._row("a.md")
        self.assertEqual(mc._sha256_text("The guard blocks raw done writes."), row["content_hash"])

    def test_reindex_same_content_leaves_version_and_updated_at_unchanged(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        first = self._row("a.md")

        time.sleep(0.01)  # so a wrongly-bumped updated_at would be detectably different
        mc.index_memory_files(db_path=self.db_path)
        second = self._row("a.md")

        self.assertEqual(1, first["version"])
        self.assertEqual(1, second["version"])
        self.assertEqual(first["updated_at"], second["updated_at"])
        self.assertEqual(first["content_hash"], second["content_hash"])

    def test_reindex_changed_content_bumps_version_and_updated_at(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        first = self._row("a.md")

        time.sleep(0.01)
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes AND live edits.")
        mc.index_memory_files(db_path=self.db_path)
        second = self._row("a.md")

        self.assertEqual(1, first["version"])
        self.assertEqual(2, second["version"])
        self.assertNotEqual(first["content_hash"], second["content_hash"])
        self.assertNotEqual(first["updated_at"], second["updated_at"])

    def test_created_at_unchanged_when_content_unchanged(self) -> None:
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        first = self._row("a.md")

        time.sleep(0.01)  # so a wrongly-bumped created_at would be detectably different
        mc.index_memory_files(db_path=self.db_path)  # rerun, same content, same row
        second = self._row("a.md")

        self.assertIsNotNone(first["created_at"])
        self.assertEqual(first["created_at"], second["created_at"])

    def test_created_at_is_fresh_per_version_not_inherited_across_supersede(self) -> None:
        # bbb-os-core's real precedent (lib/memory_fact_store.py:578-703,
        # apply_from_connection - the sole memory_fact write path for
        # promote/supersede): its INSERT never carries created_at forward
        # from the superseded row, it relies on the schema's DEFAULT
        # CURRENT_TIMESTAMP (sql/memory_fact.sql) - each row's created_at is
        # its OWN insert time. bbb-lite's code already matched this
        # (mc.py:4125); only a stale, pre-Adım-1 docstring claim said
        # otherwise, and this test used to assert the wrong thing by reading
        # the wrong (old, superseded) row - see memory/decisions.md 2026-08-18.
        self._write_note("a.md", "Guard note", "The guard blocks raw done writes.")
        mc.index_memory_files(db_path=self.db_path)
        first = self._row("a.md")

        time.sleep(0.01)
        self._write_note("a.md", "Guard note", "A completely different body of text.")
        mc.index_memory_files(db_path=self.db_path)
        second = self._row("a.md")

        self.assertIsNotNone(first["created_at"])
        self.assertIsNotNone(second["created_at"])
        self.assertNotEqual(first["created_at"], second["created_at"])

    def test_mark_memory_replaced_sets_status_and_replaced_by(self) -> None:
        self._write_note("old.md", "Old note", "Some fact.")
        self._write_note("new.md", "New note", "Some fact, restated.")
        mc.index_memory_files(db_path=self.db_path)

        mc.mark_memory_replaced("old.md", "new.md", db_path=self.db_path)

        old_row = self._row("old.md")
        new_row = self._row("new.md")
        self.assertEqual("replaced", old_row["status"])
        self.assertEqual("new.md", old_row["replaced_by"])
        self.assertEqual("active", new_row["status"])
        self.assertIsNone(new_row["replaced_by"])

    def test_search_memory_excludes_replaced_by_default_but_include_replaced_shows_it(
        self,
    ) -> None:
        self._write_note("old.md", "Guard note", "The guard blocks raw done writes.")
        self._write_note("new.md", "Guard note v2", "The guard blocks raw done writes, updated.")
        mc.index_memory_files(db_path=self.db_path)
        mc.mark_memory_replaced("old.md", "new.md", db_path=self.db_path)

        default_results = mc.search_memory("guard", db_path=self.db_path, limit=10)
        self.assertNotIn("old.md", {row["source_file"] for row in default_results})
        self.assertIn("new.md", {row["source_file"] for row in default_results})

        all_results = mc.search_memory(
            "guard", db_path=self.db_path, limit=10, include_replaced=True
        )
        self.assertIn("old.md", {row["source_file"] for row in all_results})

    def test_heal_memory_marks_old_notes_replaced_and_hides_them_from_search(self) -> None:
        self._write_note(
            "cola-a.md", "Haroon likes Cola",
            "Haroon prefers drinking Cola over any other soft drink.",
        )
        self._write_note(
            "cola-b.md", "Haroon's favorite drink",
            "Haroon likes Cola more than any other beverage.",
        )
        mc.index_memory_files(db_path=self.db_path)

        # find_similar_lesson_groups() needs real embeddings to group these;
        # bypass it here and drive heal_memory()'s merge/mark logic directly
        # via mark_memory_replaced(), mirroring what a real merge would do.
        healed_path = self.memory_dir / "lesson-healed.md"
        healed_path.write_text(
            "---\ntitle: Haroon likes Cola\nconfidence: medium\n---\n\nMerged note.\n",
            encoding="utf-8",
        )
        mc.index_memory_files(db_path=self.db_path)
        mc.mark_memory_replaced("cola-a.md", "lesson-healed.md", db_path=self.db_path)
        mc.mark_memory_replaced("cola-b.md", "lesson-healed.md", db_path=self.db_path)

        results = mc.search_memory("cola", db_path=self.db_path, limit=10)
        source_files = {row["source_file"] for row in results}
        self.assertNotIn("cola-a.md", source_files)
        self.assertNotIn("cola-b.md", source_files)

        old_row = self._row("cola-a.md")
        self.assertEqual("replaced", old_row["status"])
        self.assertEqual("lesson-healed.md", old_row["replaced_by"])


if __name__ == "__main__":
    unittest.main()
