"""PreToolUse hook: memory/ files are curator-only once they exist.

Port of haroon-bbb-sync's memory-curator-guard.py idea, minus its
role_for_uuid role system — we didn't build one, so "curator" here is just
the BBB_CURATOR=1 environment variable (see memory/decisions.md, 2026-07-24).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MEMORY_DIR = (Path(__file__).resolve().parent.parent / "memory").resolve()
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"


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


def check(tool_input: dict) -> dict | None:
    """None means allow (no output needed); a dict is the block payload."""

    file_path = tool_input.get("file_path")
    if not file_path:
        return None

    path = Path(file_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()

    try:
        path.relative_to(MEMORY_DIR)
    except ValueError:
        return None  # not under memory/, this guard doesn't care

    if os.environ.get("BBB_CURATOR") == "1":
        return None

    if path != MEMORY_INDEX and not path.exists():
        return None  # brand-new memory file, allow

    return _deny(
        f"curator-only: {path.name} is MEMORY.md or already exists under memory/ — "
        "set BBB_CURATOR=1 to edit as curator"
    )


def main() -> None:
    payload = json.load(sys.stdin)
    result = check(payload.get("tool_input", {}))
    if result is not None:
        json.dump(result, sys.stdout)
    sys.exit(0)


if __name__ == "__main__":
    main()
