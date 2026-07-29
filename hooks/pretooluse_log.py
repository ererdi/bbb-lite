"""PreToolUse hook: general guard + log, plus (Stage N) real role enforcement.

Always enforces one fixed rule (mc.db is never touched by Write/Edit — see
memory/decisions.md, 2026-07-24) and logs every other write/edit attempt.
When the payload carries a session_id, it also checks mc.can_write_code() for
that session's declared role (see hooks/role_declare.py) and denies the call
if the role has no code-write permission — the counterpart to haroon-bbb-sync's
role-guard.py, minus its persona/UUID machinery.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

LOG_FILE = Path(__file__).resolve().parent / "pretooluse.log"


def _deny(reason: str) -> dict:
    return {
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def _log(file_path: str) -> None:
    try:
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{timestamp} about to write: {file_path}\n")
    except OSError:
        pass  # fail-soft: never block a tool call over a logging error


def _log_denied(session_id: str, reason: str) -> None:
    """Record a denied write attempt, session_id included (mc.session_score reads this)."""
    try:
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{timestamp} denied session={session_id}: {reason}\n")
    except OSError:
        pass  # fail-soft: never block a tool call over a logging error


def check(tool_input: dict, session_id: str | None = None) -> dict | None:
    """None means allow (no output needed); a dict is the block payload.

    session_id is only present when the caller's payload had one; absent
    (None), this falls back to the pre-role behavior — mc.db check only.
    """

    file_path = tool_input.get("file_path")
    if not file_path:
        return None

    name = Path(file_path).name
    if name == "mc.db" or name.endswith(".db"):
        return _deny(
            "mc.db is a binary SQLite file; only mc.py may write to it, "
            "never the Write/Edit tool"
        )

    if session_id is not None:
        mc.init_db()
        if not mc.can_write_code(session_id):
            role = mc.get_role(session_id)
            reason = f"role '{role}' has no code-write permission"
            _log_denied(session_id, reason)
            return _deny(reason)

    _log(file_path)
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    result = check(payload.get("tool_input", {}), payload.get("session_id"))
    if result is not None:
        json.dump(result, sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    main()
