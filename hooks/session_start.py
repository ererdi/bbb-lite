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

    role = mc.get_role(session_id)
    print(
        f"[bbb-lite] role: {role} "
        f"(code={mc.can_write_code(session_id)}, data={mc.can_write_data(session_id)})"
    )

    if source == "compact":
        checkpoint = mc.latest_checkpoint(session_id)
        state = "checkpoint intact" if checkpoint else "no checkpoint found"
        print(f"[bbb-lite] resumed after compaction, {state}")
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


if __name__ == "__main__":
    main()
