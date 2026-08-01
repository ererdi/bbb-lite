"""Tests for Stage T: find_similar_lesson_groups() / heal_memory().

Real sentence-transformers embeddings, no mocking of the model — same
isolation approach as test_mc.py's SemanticSearchTestCase: mc.__file__ is
patched into a temp project dir so memory_dir (and memory/_archived-healed/)
resolve under it, never the real memory/ directory. Only ask_ollama is
mocked — a real ollama call is flaky and must never run in a test.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mc


class MemoryHealingTestCase(unittest.TestCase):
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

        try:
            mc._get_embedding_model()
        except Exception as exc:  # noqa: BLE001 - can't test this without the model
            self.skipTest(f"embedding model unavailable (no network and no local cache?): {exc}")

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _write_note(self, filename: str, title: str, body: str) -> None:
        (self.memory_dir / filename).write_text(
            f"---\ntitle: {title}\n---\n\n{body}\n", encoding="utf-8"
        )

    def _seed_two_similar_and_one_unrelated(self) -> None:
        self._write_note(
            "cola-a.md", "Haroon likes Cola",
            "Haroon prefers drinking Cola over any other soft drink.",
        )
        self._write_note(
            "cola-b.md", "Haroon's favorite drink",
            "Haroon likes Cola more than any other beverage.",
        )
        self._write_note(
            "deploy.md", "Deploy retry behavior",
            "The deploy script now retries three times before giving up.",
        )
        mc.index_memory_files(db_path=self.db_path)

    def test_find_similar_lesson_groups_finds_the_two_similar_notes(self) -> None:
        self._seed_two_similar_and_one_unrelated()
        groups = mc.find_similar_lesson_groups(threshold=0.85, db_path=self.db_path)
        self.assertEqual(1, len(groups))
        source_files = {item["source_file"] for item in groups[0]}
        self.assertEqual({"cola-a.md", "cola-b.md"}, source_files)

    def test_find_similar_lesson_groups_excludes_memory_and_decisions_md(self) -> None:
        self._write_note("cola-a.md", "Haroon likes Cola", "Haroon prefers Cola.")
        # Deliberately near-duplicate content, but under the excluded filenames.
        self._write_note("MEMORY.md", "Index", "Haroon prefers Cola.")
        self._write_note("decisions.md", "Decisions", "Haroon prefers Cola.")
        mc.index_memory_files(db_path=self.db_path)
        groups = mc.find_similar_lesson_groups(threshold=0.85, db_path=self.db_path)
        for group in groups:
            source_files = {item["source_file"] for item in group}
            self.assertNotIn("MEMORY.md", source_files)
            self.assertNotIn("decisions.md", source_files)

    def test_heal_memory_merges_and_archives_on_yes_verdict(self) -> None:
        self._seed_two_similar_and_one_unrelated()
        with mock.patch("mc.ask_ollama", return_value="yes"):
            result = mc.heal_memory(db_path=self.db_path)
        self.assertEqual({"groups_found": 1, "merged": 1}, result)

        archived_dir = self.memory_dir / "_archived-healed"
        self.assertTrue((archived_dir / "cola-a.md").exists())
        self.assertTrue((archived_dir / "cola-b.md").exists())
        self.assertFalse((self.memory_dir / "cola-a.md").exists())
        self.assertFalse((self.memory_dir / "cola-b.md").exists())
        healed_files = list(self.memory_dir.glob("*-healed*.md"))
        self.assertEqual(1, len(healed_files))

        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = {
                row["source_file"]: row
                for row in conn.execute(
                    "SELECT source_file, status, replaced_by FROM memory_index"
                ).fetchall()
            }
        # Stage AS: merged-away rows stay in memory_index, marked 'replaced'
        # with replaced_by pointing at the new merged file, instead of being
        # deleted outright.
        self.assertEqual("replaced", rows["cola-a.md"]["status"])
        self.assertEqual(healed_files[0].name, rows["cola-a.md"]["replaced_by"])
        self.assertEqual("replaced", rows["cola-b.md"]["status"])
        self.assertEqual(healed_files[0].name, rows["cola-b.md"]["replaced_by"])
        self.assertIn(healed_files[0].name, rows)
        self.assertEqual("active", rows[healed_files[0].name]["status"])
        self.assertIn("deploy.md", rows)  # the unrelated note is untouched
        self.assertEqual("active", rows["deploy.md"]["status"])

    def test_heal_memory_leaves_files_untouched_on_no_verdict(self) -> None:
        self._seed_two_similar_and_one_unrelated()
        with mock.patch("mc.ask_ollama", return_value="no"):
            result = mc.heal_memory(db_path=self.db_path)
        self.assertEqual({"groups_found": 1, "merged": 0}, result)
        self.assertTrue((self.memory_dir / "cola-a.md").exists())
        self.assertTrue((self.memory_dir / "cola-b.md").exists())
        self.assertFalse((self.memory_dir / "_archived-healed").exists())
        self.assertEqual([], list(self.memory_dir.glob("*-healed*.md")))


if __name__ == "__main__":
    unittest.main()
