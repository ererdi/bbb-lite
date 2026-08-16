"""Stop hook: checkpoint a short summary of the last assistant message. Silent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

SUMMARY_LIMIT = 300


def _last_assistant_summary(transcript_path: str) -> str | None:
    path = Path(transcript_path)
    if not transcript_path or not path.exists():
        return None
    summary = None
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            else:
                text = ""
            text = " ".join(text.split())
            if text:
                summary = text
    return summary[:SUMMARY_LIMIT] if summary else None


def _auto_commit(summary: str) -> None:
    """Stage AJ: the git-commit half of Haroon's auto-safepoint.sh (the other
    half - saving session state as a real checkpoint - was already covered by
    write_checkpoint()/record_lifecycle_run() above).

    Opt-in only (BBB_AUTO_COMMIT=1) - auto-committing every turn is risky
    enough that the user should turn it on deliberately, not get it by
    default (see decisions.md, 2026-07-29). Fail-soft: a git error here must
    never affect the checkpoint write that already happened.
    """

    if os.environ.get("BBB_AUTO_COMMIT") != "1":
        return
    try:
        subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"auto-safepoint: {summary[:80]}"],
            check=True,
            capture_output=True,
        )
    except Exception:
        pass


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id", "unknown")
    transcript_path = payload.get("transcript_path", "")

    summary = _last_assistant_summary(transcript_path) or "(no assistant message found)"

    mc.init_db()
    mc.write_checkpoint(session_id, last_decision=summary)
    mc.record_lifecycle_run(
        "stop_checkpoint",
        session_id=session_id,
        source="stop",
        status="ok",
        details={"summary": summary[:120]},
    )

    # bbb-os-core parity item 3: same maintenance battery as session_start.py,
    # so drift introduced mid-session (e.g. new orphaned edges) is caught at
    # Stop too, not only at the next Start. Fail-soft.
    try:
        mc.run_maintenance_checks(session_id)
    except Exception:
        pass

    _auto_commit(summary)


if __name__ == "__main__":
    main()
