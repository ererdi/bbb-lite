"""CLI: run a task through a two-role (architect/builder) headless simulation.

`classify()` (mc.py, Ollama-backed) decides whether the task needs an architect
step first; if it can't decide, the task falls back to builder-only. Each role's
turn is a fail-soft call to the Claude Agent SDK's `query()` — see
memory/decisions.md for the move off `claude -p` (Claude Code CLI print-mode).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

ARCHITECT_SYSTEM_PROMPT = (
    "You are the architect in a two-role dev workflow. In 3-4 sentences, give a "
    "short plan for the following task. Do not write code."
)
BUILDER_SYSTEM_PROMPT = (
    "You are the builder in a two-role dev workflow. In 3-4 sentences, describe "
    "the code change you would make for the following task.{plan_context}"
)
DISPATCH_SESSION_ID = "dispatch-cli"


def _safe_mc(default, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        return default


async def run_claude(prompt: str, *, system_prompt: str, timeout: int = 120) -> str | None:
    """Fail-soft call to the Claude Agent SDK; None if unavailable/errors/times out."""

    try:
        return await asyncio.wait_for(_collect_text(prompt, system_prompt), timeout=timeout)
    except Exception:
        return None


async def _collect_text(prompt: str, system_prompt: str) -> str | None:
    options = ClaudeAgentOptions(system_prompt=system_prompt)
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

    change = await run_claude(
        f"Task: {task}",
        system_prompt=BUILDER_SYSTEM_PROMPT.format(plan_context=plan_context),
    )
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
    asyncio.run(dispatch(" ".join(args.task)))


if __name__ == "__main__":
    main()
