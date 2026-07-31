"""CLI: run a task through a CTO -> architect/builder headless simulation.

The CTO step (Stage U) titles the task, opens it as a real mc.add_task(), and
decides if it needs an architect first — the missing link in Haroon's
Brozzer -> CTO -> architect -> builder chain. If the CTO doesn't answer or its
reply doesn't parse, dispatch() falls back to the old classify()-based
architect/builder split and never opens a task. Each role's turn is a
fail-soft call to the Claude Agent SDK's `query()` — see memory/decisions.md
for the move off `claude -p` (Claude Code CLI print-mode).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

CTO_SYSTEM_PROMPT = (
    "You are the CTO. Given this task request, write a short task title (one "
    "line) and decide if it needs an architect step first or can go straight "
    "to a builder. Respond in this exact format: TITLE: <title>\n"
    "NEEDS_ARCHITECT: yes|no"
)
ARCHITECT_SYSTEM_PROMPT = (
    "You are the architect in a two-role dev workflow. In 3-4 sentences, give a "
    "short plan for the following task. Do not write code."
)
BUILDER_SYSTEM_PROMPT = (
    "You are the builder in a two-role dev workflow. In 3-4 sentences, describe "
    "the code change you would make for the following task.{plan_context}"
)
DISPATCH_SESSION_ID = "dispatch-cli"

_CTO_TITLE_RE = re.compile(r"^\s*TITLE:\s*(.+?)\s*$", re.I | re.M)
_CTO_NEEDS_ARCHITECT_RE = re.compile(r"^\s*NEEDS_ARCHITECT:\s*(yes|no)\s*$", re.I | re.M)


def _parse_cto_response(text: str) -> tuple[str, bool] | None:
    """Parse the CTO's 'TITLE: ...\\nNEEDS_ARCHITECT: yes|no' reply.

    None on anything that doesn't cleanly match both lines (or gives an empty
    title) — dispatch() must fall back to classify() rather than guess at a
    malformed CTO answer or open a task with a blank title.
    """

    title_match = _CTO_TITLE_RE.search(text)
    needs_match = _CTO_NEEDS_ARCHITECT_RE.search(text)
    if not title_match or not needs_match:
        return None
    title = title_match.group(1).strip()
    if not title:
        return None
    return title, needs_match.group(1).lower() == "yes"


def _safe_mc(default, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        return default


def create_worktree(task_id: int | str) -> Path | None:
    """Fail-soft: create an isolated git worktree for the builder to work in.

    None if git is missing, a worktree/branch for this task_id already
    exists, or anything else goes wrong — worktree isolation is an optional
    improvement (see decisions.md, 2026-07-29), never a requirement for
    dispatch to run.
    """

    repo_root = Path(__file__).resolve().parent.parent
    relative = f"../bbb-lite-work-{task_id}"
    try:
        result = subprocess.run(
            ["git", "worktree", "add", relative, "-b", f"work/{task_id}"],
            cwd=repo_root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (repo_root / relative).resolve()


def remove_worktree(worktree_path: Path, task_id: int | str) -> None:
    """Fail-soft cleanup of a builder worktree and its work/<task_id> branch.

    Two independent fail-soft steps, not one: a branch that's gone or can't
    be deleted (e.g. still checked out somewhere) must not stop the worktree
    itself from being removed, or vice versa.
    """

    repo_root = Path(__file__).resolve().parent.parent
    try:
        subprocess.run(
            ["git", "worktree", "remove", str(worktree_path), "--force"],
            cwd=repo_root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        subprocess.run(
            ["git", "branch", "-D", f"work/{task_id}"],
            cwd=repo_root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pass


async def run_claude(
    prompt: str, *, system_prompt: str, cwd: str | None = None, timeout: int = 120
) -> str | None:
    """Fail-soft call to the Claude Agent SDK; None if unavailable/errors/times out."""

    try:
        return await asyncio.wait_for(
            _collect_text(prompt, system_prompt, cwd), timeout=timeout
        )
    except Exception:
        return None


async def _collect_text(prompt: str, system_prompt: str, cwd: str | None = None) -> str | None:
    options = ClaudeAgentOptions(system_prompt=system_prompt, cwd=cwd)
    text_parts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
    output = "".join(text_parts).strip()
    return output or None


async def dispatch(task: str) -> None:
    _safe_mc(None, mc.init_db)
    _safe_mc(None, mc.ensure_session, DISPATCH_SESSION_ID)
    run_id = _safe_mc(
        None,
        mc.create_dispatch_run,
        DISPATCH_SESSION_ID,
        status="running",
        summary=task,
    )
    task_id = None
    cto_reply = await run_claude(f"Task: {task}", system_prompt=CTO_SYSTEM_PROMPT)
    parsed_cto = _parse_cto_response(cto_reply) if cto_reply is not None else None

    if parsed_cto is not None:
        title, needs_architect = parsed_cto
        print(
            f"[dispatch] role=cto -> {title} "
            f"(architect: {'yes' if needs_architect else 'no'})"
        )
        if run_id:
            _safe_mc(None, mc.record_agent_output, run_id, "cto", cto_reply)
        task_id = _safe_mc(
            None, mc.add_task, title, status="in_progress", session_id=DISPATCH_SESSION_ID
        )
        role_needed = "architect" if needs_architect else "builder"
    else:
        print("[dispatch] role=cto -> unavailable, falling back to classify()")
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
        plan = await run_claude(f"Task: {task}", system_prompt=ARCHITECT_SYSTEM_PROMPT)
        if plan is None:
            print("[dispatch] architect unavailable, skipping")
            if run_id:
                _safe_mc(None, mc.record_agent_output, run_id, "architect", "unavailable")
        else:
            print(f"[dispatch] role=architect -> {plan}")
            if run_id:
                _safe_mc(None, mc.record_agent_output, run_id, "architect", plan)
            plan_context = f" The architect's plan was: {plan}"

    # Stage V: give the builder an isolated worktree when there's a real task
    # to isolate for; if it can't be created, fail-soft to the main folder.
    worktree_path = create_worktree(task_id) if task_id is not None else None
    if worktree_path is not None:
        print(f"[dispatch] builder worktree: {worktree_path}")

    try:
        change = await run_claude(
            f"Task: {task}",
            system_prompt=BUILDER_SYSTEM_PROMPT.format(plan_context=plan_context),
            cwd=str(worktree_path) if worktree_path is not None else None,
        )
    finally:
        if worktree_path is not None:
            remove_worktree(worktree_path, task_id)

    if change is None:
        print("[dispatch] builder unavailable, skipping")
        if run_id:
            _safe_mc(None, mc.record_agent_output, run_id, "builder", "unavailable")
    else:
        print(f"[dispatch] role=builder -> {change}")
        if run_id:
            _safe_mc(None, mc.record_agent_output, run_id, "builder", change)

    # Recorded before complete_task() regardless of run_id, since Stage AE
    # requires a real evidence row to complete a task at all.
    evidence_id = _safe_mc(
        None,
        mc.record_evidence,
        "dispatch",
        "architect_builder_local_dispatch",
        "passed" if change is not None else "needs_review",
        details={"dispatch_run_id": run_id, "role_needed": role_needed},
    )

    if task_id is not None and change is not None and evidence_id is not None:
        # Stage AP: this evidence is a dispatch receipt, not a test run - an
        # honest require_test_evidence=False rather than mislabeling the
        # evidence kind as "test" just to pass the new default check.
        _safe_mc(
            None,
            mc.complete_task,
            task_id,
            session_id=DISPATCH_SESSION_ID,
            evidence_id=evidence_id,
            completion_summary=change,
            require_test_evidence=False,
        )

    if run_id:
        status = "completed" if change is not None else "needs_attention"
        _safe_mc(None, mc.complete_dispatch_run, run_id, status=status, summary=f"{role_needed} -> builder")
    print("[dispatch] baton: done")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dispatch a task through an architect/builder role simulation."
    )
    parser.add_argument("task", nargs="+", help="Task description")
    args = parser.parse_args()
    asyncio.run(dispatch(" ".join(args.task)))


if __name__ == "__main__":
    main()
