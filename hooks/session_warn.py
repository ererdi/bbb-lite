"""UserPromptSubmit hook: warn when a session's health score is low.

Stage AK: the last of Codex's 9-item Session Lifecycle list. Uses the
existing mc.session_score() (Stage Y) as-is - no new scoring logic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

SCORE_THRESHOLD = 50


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    if session_id is None:
        return

    try:
        mc.init_db()
        score = mc.session_score(session_id)
    except Exception:
        return  # fail-soft: a scoring error should never block the prompt

    overall = score["overall_score"]
    if overall < SCORE_THRESHOLD:
        print(
            f"[bbb-lite] WARNING: session health low (score={overall}/100) - "
            "consider /clear or wrapping up"
        )


if __name__ == "__main__":
    main()
