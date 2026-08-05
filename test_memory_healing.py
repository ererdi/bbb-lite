"""Tests for Stage T: find_similar_lesson_groups() / heal_memory() /
approve_merge().

Real sentence-transformers embeddings, no mocking of the model — same
isolation approach as test_mc.py's SemanticSearchTestCase: mc.__file__ is
patched into a temp project dir so memory_dir (and memory/_archived-healed/)
resolve under it, never the real memory/ directory. Only ask_ollama is
mocked — a real ollama call is flaky and must never run in a test.

Fable's BUS review (decisions.md, 2026-08-05) found heal_memory() applying
merges the moment Ollama said "yes", with no approval step. heal_memory()
now only queues a pending_merges proposal; approve_merge() is the sole path
that actually rewrites/archives files and marks memory_index rows
'replaced'.
"""

from __future__ import annotations

import contextlib
import json
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

    def test_heal_memory_only_proposes_on_yes_verdict_nothing_merged_yet(self) -> None:
        """A "yes" verdict must only queue a pending_merges row. No file may
        be written, archived, or marked 'replaced' until approve_merge() is
        called by hand (Fable's BUS review, decisions.md 2026-08-05)."""
        self._seed_two_similar_and_one_unrelated()
        with mock.patch("mc.ask_ollama", return_value="yes"):
            result = mc.heal_memory(db_path=self.db_path)
        self.assertEqual({"groups_found": 1, "merged": 1}, result)

        # Nothing on disk changed: no archive dir, no *-healed*.md file, and
        # both original notes are still exactly where they were.
        self.assertFalse((self.memory_dir / "_archived-healed").exists())
        self.assertEqual([], list(self.memory_dir.glob("*-healed*.md")))
        self.assertTrue((self.memory_dir / "cola-a.md").exists())
        self.assertTrue((self.memory_dir / "cola-b.md").exists())

        # Nothing in memory_index changed either — everything still 'active'.
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = {
                row["source_file"]: row
                for row in conn.execute(
                    "SELECT source_file, status, replaced_by FROM memory_index"
                ).fetchall()
            }
        self.assertEqual("active", rows["cola-a.md"]["status"])
        self.assertIsNone(rows["cola-a.md"]["replaced_by"])
        self.assertEqual("active", rows["cola-b.md"]["status"])
        self.assertIsNone(rows["cola-b.md"]["replaced_by"])

        # The proposal itself was recorded, pending approval.
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            pending = conn.execute(
                "SELECT source_files, title, status FROM pending_merges"
            ).fetchall()
        self.assertEqual(1, len(pending))
        self.assertEqual("pending", pending[0]["status"])
        self.assertEqual(
            {"cola-a.md", "cola-b.md"}, set(json.loads(pending[0]["source_files"]))
        )

    def test_heal_memory_does_not_requeue_an_already_pending_group(self) -> None:
        self._seed_two_similar_and_one_unrelated()
        with mock.patch("mc.ask_ollama", return_value="yes") as mock_ollama:
            mc.heal_memory(db_path=self.db_path)
            result = mc.heal_memory(db_path=self.db_path)
        self.assertEqual({"groups_found": 1, "merged": 0}, result)
        self.assertEqual(1, mock_ollama.call_count)  # second run skipped, no re-ask
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM pending_merges"
            ).fetchone()["n"]
        self.assertEqual(1, count)

    def test_heal_memory_leaves_files_untouched_on_no_verdict(self) -> None:
        self._seed_two_similar_and_one_unrelated()
        with mock.patch("mc.ask_ollama", return_value="no"):
            result = mc.heal_memory(db_path=self.db_path)
        self.assertEqual({"groups_found": 1, "merged": 0}, result)
        self.assertTrue((self.memory_dir / "cola-a.md").exists())
        self.assertTrue((self.memory_dir / "cola-b.md").exists())
        self.assertFalse((self.memory_dir / "_archived-healed").exists())
        self.assertEqual([], list(self.memory_dir.glob("*-healed*.md")))
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM pending_merges"
            ).fetchone()["n"]
        self.assertEqual(0, count)

    def test_approve_merge_applies_the_proposal_and_marks_it_approved(self) -> None:
        self._seed_two_similar_and_one_unrelated()
        with mock.patch("mc.ask_ollama", return_value="yes"):
            mc.heal_memory(db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            merge_id = conn.execute(
                "SELECT id FROM pending_merges WHERE status = 'pending'"
            ).fetchone()["id"]

        result = mc.approve_merge(merge_id, db_path=self.db_path)
        self.assertTrue(result["merged"])

        archived_dir = self.memory_dir / "_archived-healed"
        self.assertTrue((archived_dir / "cola-a.md").exists())
        self.assertTrue((archived_dir / "cola-b.md").exists())
        self.assertFalse((self.memory_dir / "cola-a.md").exists())
        self.assertFalse((self.memory_dir / "cola-b.md").exists())
        healed_files = list(self.memory_dir.glob("*-healed*.md"))
        self.assertEqual(1, len(healed_files))
        self.assertEqual(result["healed_file"], healed_files[0].name)

        with contextlib.closing(mc._connect(self.db_path)) as conn:
            rows = {
                row["source_file"]: row
                for row in conn.execute(
                    "SELECT source_file, status, replaced_by FROM memory_index"
                ).fetchall()
            }
            merge_status = conn.execute(
                "SELECT status FROM pending_merges WHERE id = ?", (merge_id,)
            ).fetchone()["status"]
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
        self.assertEqual("approved", merge_status)

    def test_approve_merge_rejects_unknown_id(self) -> None:
        with self.assertRaises(ValueError):
            mc.approve_merge(999999, db_path=self.db_path)

    def test_approve_merge_rejects_already_approved_id(self) -> None:
        self._seed_two_similar_and_one_unrelated()
        with mock.patch("mc.ask_ollama", return_value="yes"):
            mc.heal_memory(db_path=self.db_path)
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            merge_id = conn.execute(
                "SELECT id FROM pending_merges WHERE status = 'pending'"
            ).fetchone()["id"]
        mc.approve_merge(merge_id, db_path=self.db_path)
        with self.assertRaises(ValueError):
            mc.approve_merge(merge_id, db_path=self.db_path)


if __name__ == "__main__":
    unittest.main()
