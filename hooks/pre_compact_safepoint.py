"""PreCompact hook: write a safety checkpoint right before compaction. Silent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id", "unknown")

    mc.init_db()
    checkpoint = mc.latest_checkpoint(session_id)
    if checkpoint is None:
        mc.write_checkpoint(session_id, current_task="pre-compact safepoint")
        mc.record_lifecycle_run(
            "pre_compact_safepoint",
            session_id=session_id,
            source="compact",
            status="ok",
            details={"checkpoint": "created-empty"},
        )
    else:
        mc.write_checkpoint(
            session_id,
            current_task=checkpoint["current_task"],
            last_decision=checkpoint["last_decision"],
            next_action=checkpoint["next_action"],
            open_queue=checkpoint["open_queue"],
            risk=checkpoint["risk"],
        )
        mc.record_lifecycle_run(
            "pre_compact_safepoint",
            session_id=session_id,
            source="compact",
            status="ok",
            details={"checkpoint": "copied-latest"},
        )

    # Stage R: the before-side of compact recovery validation — see
    # session_start.py's source=="compact" branch for the after-side compare.
    mc.save_snapshot(session_id, "pre")


if __name__ == "__main__":
    main()
