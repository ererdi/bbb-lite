"""Stop hook: checkpoint a short summary of the last assistant message. Silent."""

from __future__ import annotations

import json
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


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id", "unknown")
    transcript_path = payload.get("transcript_path", "")

    summary = _last_assistant_summary(transcript_path) or "(no assistant message found)"

    mc.init_db()
    mc.write_checkpoint(session_id, last_decision=summary)


if __name__ == "__main__":
    main()
