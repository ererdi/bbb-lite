"""Tests for events_log's outbox_status column — the minimal local analog of
bbb-os-core's publication/outbox (bbb-lite parity item 5). No consumer
exists; this only tags rows pending/consumed for a future one."""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path

import mc


class EventsOutboxTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_record_event_defaults_to_pending(self) -> None:
        event_id = mc.record_event("test_event", db_path=self.db_path)
        events = mc.list_events(db_path=self.db_path)
        row = next(e for e in events if e["id"] == event_id)
        self.assertEqual("pending", row["outbox_status"])

    def test_list_pending_events_excludes_consumed(self) -> None:
        id1 = mc.record_event("first", db_path=self.db_path)
        id2 = mc.record_event("second", db_path=self.db_path)
        mc.mark_consumed(id1, db_path=self.db_path)
        pending = mc.list_pending_events(db_path=self.db_path)
        pending_ids = {row["id"] for row in pending}
        self.assertNotIn(id1, pending_ids)
        self.assertIn(id2, pending_ids)

    def test_mark_consumed_flips_status(self) -> None:
        event_id = mc.record_event("first", db_path=self.db_path)
        mc.mark_consumed(event_id, db_path=self.db_path)
        events = mc.list_events(db_path=self.db_path)
        row = next(e for e in events if e["id"] == event_id)
        self.assertEqual("consumed", row["outbox_status"])

    def test_pre_migration_rows_stay_null_not_backfilled(self) -> None:
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            conn.execute(
                """INSERT INTO events_log (event_type, source, details_json, created_at)
                   VALUES ('legacy_event', 'system', '{}', '2026-01-01T00:00:00+00:00')"""
            )
            conn.commit()
        mc.init_db(self.db_path)  # re-run migration
        with contextlib.closing(mc._connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT outbox_status FROM events_log WHERE event_type = 'legacy_event'"
            ).fetchone()
        self.assertIsNone(row["outbox_status"])


if __name__ == "__main__":
    unittest.main()
