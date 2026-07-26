"""CLI: run a task through a two-role (architect/builder) headless simulation.

`classify()` (mc.py, Ollama-backed) decides whether the task needs an architect
step first; if it can't decide, the task falls back to builder-only. Each role's
turn is a fail-soft call to `claude -p` (Claude Code CLI print-mode) — see
memory/decisions.md for why this isn't ssh + the Agent SDK, unlike
haroon-bbb-sync.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

ARCHITECT_PROMPT = (
    "You are the architect in a two-role dev workflow. In 3-4 sentences, give a "
    "short plan for the following task. Do not write code. Task: {task}"
)
BUILDER_PROMPT = (
    "You are the builder in a two-role dev workflow. In 3-4 sentences, describe "
    "the code change you would make for the following task.{plan_context} "
    "Task: {task}"
)
DISPATCH_SESSION_ID = "dispatch-cli"


def _safe_mc(default, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        return default


def run_claude(prompt: str, *, timeout: int = 120) -> str | None:
    """Fail-soft call to `claude -p <prompt>`; None if claude is missing/errors/times out.

    Resolved via shutil.which() first: on Windows, the npm-installed `claude` is
    a .cmd shim, and subprocess won't find it by the bare name without the
    extension (unlike a shell, which resolves PATHEXT itself).
    """

    claude_path = shutil.which("claude") or "claude"
    try:
        result = subprocess.run(
            [claude_path, "-p", prompt],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def dispatch(task: str) -> None:
    _safe_mc(None, mc.init_db)
    _safe_mc(None, mc.ensure_session, DISPATCH_SESSION_ID)
    run_id = _safe_mc(
        None,
        mc.create_dispatch_run,
        DISPATCH_SESSION_ID,
        status="running",
        summary=task,
    )
    role_needed = mc.classify(
        f"Task: {task}\nDoes this task need an architect to plan it first, or can "
        "a builder just implement it directly?",
        ["architect", "builder"],
    )
    if role_needed is None:
        role_needed = "builder"
    print(f"[dispatch] role=classify -> {role_needed}")
    if run_id:
        _safe_mc(None, mc.record_agent_output, run_id, "classifier", role_needed)

    plan_context = ""
    if role_needed == "architect":
        plan = run_claude(ARCHITECT_PROMPT.format(task=task))
        if plan is None:
            print("[dispatch] architect unavailable, skipping")
            if run_id:
                _safe_mc(None, mc.record_agent_output, run_id, "architect", "unavailable")
        else:
            print(f"[dispatch] role=architect -> {plan}")
            if run_id:
                _safe_mc(None, mc.record_agent_output, run_id, "architect", plan)
            # Flattened to one line: the .cmd shim `claude` runs through cmd.exe,
            # which treats an embedded newline in an argument as a command
            # separator and truncates the prompt there.
            plan_context = f" The architect's plan was: {' '.join(plan.split())}"

    change = run_claude(BUILDER_PROMPT.format(task=task, plan_context=plan_context))
    if change is None:
        print("[dispatch] builder unavailable, skipping")
        if run_id:
            _safe_mc(None, mc.record_agent_output, run_id, "builder", "unavailable")
    else:
        print(f"[dispatch] role=builder -> {change}")
        if run_id:
            _safe_mc(None, mc.record_agent_output, run_id, "builder", change)

    if run_id:
        status = "completed" if change is not None else "needs_attention"
        _safe_mc(None, mc.complete_dispatch_run, run_id, status=status, summary=f"{role_needed} -> builder")
        _safe_mc(
            None,
            mc.record_evidence,
            "dispatch",
            "architect_builder_local_dispatch",
            "passed" if change is not None else "needs_review",
            details={"dispatch_run_id": run_id, "role_needed": role_needed},
        )
    print("[dispatch] baton: done")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dispatch a task through an architect/builder role simulation."
    )
    parser.add_argument("task", nargs="+", help="Task description")
    args = parser.parse_args()
    dispatch(" ".join(args.task))


if __name__ == "__main__":
    main()
