"""CLI: daily floor check — the local, always-on-schedule half of Stage G.

Mirrors haroon-bbb-sync's bbb-hourly-tripwire.py: sweep stale sessions, refresh
the memory index, then run check_floor()'s deterministic checks. If the floor
is clean, this never calls the Ollama judge at all — asking a model to bless
an empty finding list is the exact "don't ask the model during a quiet hour"
waste Haroon's tripwire avoids. Only a non-empty finding list is escalated to
mc.classify() for a normal/needs-attention verdict.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def daily_check() -> None:
    mc.init_db()

    stale_count = mc.sweep_stale_sessions()
    print(f"[daily-check] swept {stale_count} stale session(s)")

    indexed_count = mc.index_memory_files()
    print(f"[daily-check] indexed {indexed_count} memory file(s)")

    findings = mc.check_floor()

    try:
        heal_result = mc.heal_memory()
        print(
            f"[daily-check] proposed {heal_result['merged']} of "
            f"{heal_result['groups_found']} similar group(s) for merge "
            "(pending mc.approve_merge())"
        )
    except Exception:
        print("[daily-check] memory healing skipped (error)")

    try:
        retired_count = mc.retire_low_confidence_lessons()
        print(f"[daily-check] retired {retired_count} low-confidence lesson(s)")
    except Exception:
        print("[daily-check] confidence retirement skipped (error)")

    if not findings:
        print("[daily-check] floor clean, no judge call needed")
        return

    for finding in findings:
        print(f"[daily-check] finding: {finding}")

    verdict = mc.classify(
        "Daily floor check findings:\n" + "\n".join(findings)
        + "\nIs this situation normal or does it need attention?",
        ["normal", "needs-attention"],
    )
    print(f"[daily-check] judge verdict: {verdict}")


if __name__ == "__main__":
    daily_check()
