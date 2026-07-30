"""PostCompact hook: verify that compaction didn't silently lose MC state.

Stage AI: this logic used to live inline in hooks/session_start.py's
source=="compact" branch (Stage R/AA). Pulled out into its own hook so it
maps onto Claude Code's actual PostCompact event (see decisions.md,
2026-07-29) instead of piggybacking on SessionStart's "compact" matcher.
session_start.py still calls recap() directly for that matcher, so its own
compact behavior is unchanged - this is a code-organization move, not a
behavior change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def recap(session_id: str) -> None:
    """Compare a fresh post-compact snapshot against the pre-compact one and
    report whether anything MC was tracking got lost.

    NOT fail-soft on purpose. If we can't save or compare the post-compact
    snapshot, that is itself a finding — Compact must not be treated as a
    successful recovery when persistence can't be verified (see
    decisions.md, 2026-07-27).
    """

    checkpoint = mc.latest_checkpoint(session_id)
    state = "checkpoint intact" if checkpoint else "no checkpoint found"
    print(f"[bbb-lite] resumed after compaction, {state}")

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


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id", "unknown")

    mc.init_db()
    recap(session_id)


if __name__ == "__main__":
    main()
