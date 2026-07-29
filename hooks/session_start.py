"""SessionStart hook: print last checkpoint and open task count."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id", "unknown")
    source = payload.get("source", "unknown")

    mc.init_db()
    mc.ensure_session(session_id)
    mc.record_lifecycle_run(
        "session_start",
        session_id=session_id,
        source=source,
        status="ok",
        details={"source": source},
    )

    role = mc.get_role(session_id)
    print(
        f"[bbb-lite] role: {role} "
        f"(code={mc.can_write_code(session_id)}, data={mc.can_write_data(session_id)})"
    )

    if source == "compact":
        checkpoint = mc.latest_checkpoint(session_id)
        state = "checkpoint intact" if checkpoint else "no checkpoint found"
        print(f"[bbb-lite] resumed after compaction, {state}")

        # Stage R: NOT fail-soft on purpose. If we can't save or compare the
        # post-compact snapshot, that is itself a finding — Compact must not
        # be treated as a successful recovery when persistence can't be
        # verified (see decisions.md, 2026-07-27).
        try:
            mc.save_snapshot(session_id, "post")
            findings = mc.compare_snapshots(session_id)
        except Exception as exc:
            findings = [f"unable to verify compact recovery: {exc}"]

        if findings:
            mc.mark_session_degraded(session_id)
            print(f"[bbb-lite] DEGRADED RECOVERY: {'; '.join(findings)}")
        else:
            snapshot = mc.compact_snapshot(session_id)
            total = (
                len(snapshot["tasks"])
                + snapshot["decisions"]
                + snapshot["checklist_items"]
                + (1 if snapshot["role_declared"] else 0)
            )
            print(f"[bbb-lite] compact recovery verified: {total}/{total} items intact")
        return

    if source == "clear":
        print("[bbb-lite] role/context reset, restoring from database")

    checkpoint = mc.latest_checkpoint(session_id)
    last_task = checkpoint["current_task"] if checkpoint and checkpoint["current_task"] else "none"
    open_tasks = mc.list_open_tasks()

    print(
        f"[bbb-lite] session {session_id} ({source}) | "
        f"last task: {last_task} | {len(open_tasks)} open task(s)"
    )
    if checkpoint and checkpoint["next_action"]:
        print(f"[bbb-lite] {checkpoint['next_action']}")

    # Stage S: surface memory related to the last known task. Fail-soft on
    # purpose (unlike Stage R) — this is a convenience nudge, not a proof of
    # recovery, so a broken embedding model should just skip the line.
    if checkpoint and checkpoint["current_task"]:
        try:
            hits = mc.semantic_search(checkpoint["current_task"], limit=2)
        except Exception:
            hits = []
        for hit in hits:
            print(f"[bbb-lite] related memory: {hit['title']} ({hit['source_file']})")


if __name__ == "__main__":
    main()
