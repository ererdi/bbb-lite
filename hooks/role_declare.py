"""UserPromptSubmit hook: declare a persistent role for this session.

Small but real counterpart to haroon-bbb-sync's bbb_roles.py — no persona
text, just a permission role (mc.ROLE_PERMISSIONS) stored per session_id so
later PreToolUse calls can enforce it, not merely remind the model of it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

_EN_PATTERN = re.compile(r"\byou are the (architect|builder|advisor|brozzer)\b", re.I)
_TR_PATTERN = re.compile(r"\bsen (mimarsın|builder'sın|advisor'sın|brozzer'sın)\b", re.I)
_TR_ROLE_BY_TOKEN = {
    "mimarsın": "architect",
    "builder'sın": "builder",
    "advisor'sın": "advisor",
    "brozzer'sın": "brozzer",
}


def find_declared_role(prompt: str) -> str | None:
    """The role named by a "you are the X" / "sen X'sın" pattern, or None."""

    match = _EN_PATTERN.search(prompt)
    if match:
        return match.group(1).lower()
    match = _TR_PATTERN.search(prompt)
    if match:
        return _TR_ROLE_BY_TOKEN[match.group(1).lower()]
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    prompt = payload.get("prompt", "")

    role = find_declared_role(prompt)
    if role is None or session_id is None:
        return

    mc.init_db()
    mc.set_role(session_id, role)
    print(f"[bbb-lite] role declared: {role}")


if __name__ == "__main__":
    main()
