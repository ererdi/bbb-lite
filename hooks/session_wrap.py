"""SessionEnd hook: warn on unproven "done" claims, then mark the session completed."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

DONE_PATTERN = re.compile(r"\b(done|completed|finished)\b", re.IGNORECASE)


def _assistant_texts(transcript_path: str) -> list[str]:
    path = Path(transcript_path)
    if not transcript_path or not path.exists():
        return []
    texts: list[str] = []
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
                texts.append(text)
    return texts


def _mentions_done_near(title: str, texts: list[str]) -> bool:
    """A "done"-ish phrase in a message that also names this task's title."""

    title_words = [w for w in re.findall(r"[a-zA-Z0-9]+", title.lower()) if len(w) > 3]
    if not title_words:
        return False
    for text in texts:
        if not DONE_PATTERN.search(text):
            continue
        lower = text.lower()
        if any(word in lower for word in title_words):
            return True
    return False


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id", "unknown")
    transcript_path = payload.get("transcript_path", "")

    mc.init_db()
    mc.ensure_session(session_id)

    texts = _assistant_texts(transcript_path)
    # has_completed_task_since() (time-window proxy) is superseded by the
    # per-task task_activity check below — kept in mc.py, no longer called here.
    for task in mc.tasks_touched_by_session(session_id):
        if task["completed_by_session"]:
            continue
        if _mentions_done_near(task["title"], texts):
            print(
                f"[bbb-lite] warning: probably unproven done claim: {task['title']!r}"
            )

    next_action = mc.pick_next_action()
    mc.write_checkpoint(session_id, next_action=next_action)

    mc.mark_session_completed(session_id)
    open_tasks = mc.list_open_tasks()
    mc.record_lifecycle_run(
        "session_wrap",
        session_id=session_id,
        source="session_end",
        status="ok",
        details={"open_tasks": len(open_tasks), "next_action": next_action},
    )
    print(f"[bbb-lite] session {session_id} wrapped | {len(open_tasks)} open task(s) remain")

    score = mc.session_score(session_id)
    print(
        f"[bbb-lite] session score: {score['overall_score']}/100 "
        f"(exec={score['exec_score']}, context={score['context_score']}, "
        f"rules={score['rules_score']})"
    )


if __name__ == "__main__":
    main()
