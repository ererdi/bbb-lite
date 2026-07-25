"""PreToolUse hook: general guard + log, the non-role-based counterpart to
haroon-bbb-sync's role-guard.py.

Haroon's role-guard.py checks the calling role's permissions; we don't have a
role system, so this enforces one fixed rule instead (mc.db is never touched
by Write/Edit — see memory/decisions.md, 2026-07-24) and logs every other
write/edit attempt.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

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


def check(tool_input: dict) -> dict | None:
    """None means allow (no output needed); a dict is the block payload."""

    file_path = tool_input.get("file_path")
    if not file_path:
        return None

    name = Path(file_path).name
    if name == "mc.db" or name.endswith(".db"):
        return _deny(
            "mc.db is a binary SQLite file; only mc.py may write to it, "
            "never the Write/Edit tool"
        )

    _log(file_path)
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    result = check(payload.get("tool_input", {}))
    if result is not None:
        json.dump(result, sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    main()
