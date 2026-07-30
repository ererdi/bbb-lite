"""SessionStart hook: print last checkpoint and open task count."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id", "unknown")
    source = payload.get("source", "unknown")
    cwd = payload.get("cwd") or os.getcwd()

    mc.init_db()
    mc.ensure_session(session_id)
    mc.record_lifecycle_run(
        "session_start",
        session_id=session_id,
        source=source,
        status="ok",
        details={"source": source},
    )

    # Stage AG: match cwd's folder name against projects.name. An unmapped
    # cwd is said out loud, not silently attached to some existing project
    # (Haroon's "unmapped" principle - see decisions.md, 2026-07-29).
    project = mc.resolve_project(cwd)
    if project["status"] == "unmapped":
        print("[bbb-lite] project: unmapped (cwd not linked to any project)")
    else:
        print(f"[bbb-lite] project: {project['name']}")

    # Stage AF: /clear hands session_start a brand-new session_id (verified
    # live: two distinct UUIDs across a /clear), which has no row in `roles`
    # yet. Left alone, get_role() would silently fall back to "brozzer" and
    # the declared role would be lost. Inherit the most recently declared
    # role instead. NOT fail-soft on purpose: if there is no previous role to
    # inherit either, say so out loud rather than defaulting quietly (see
    # decisions.md, 2026-07-29).
    if source == "clear" and not mc.role_exists(session_id):
        inherited = mc.get_most_recent_role()
        if inherited:
            mc.set_role(session_id, inherited)
            print(f"[bbb-lite] role inherited after /clear: {inherited}")
        else:
            print("[bbb-lite] NOTE: no previous role found, defaulting to brozzer")

    role = mc.get_role(session_id)
    print(
        f"[bbb-lite] role: {role} "
        f"(code={mc.can_write_code(session_id)}, data={mc.can_write_data(session_id)})"
    )

    if source == "compact":
        checkpoint = mc.latest_checkpoint(session_id)
        state = "checkpoint intact" if checkpoint else "no checkpoint found"
        print(f"[bbb-lite] resumed after compaction, {state}")

        # Stage R: NOT fail-soft on purpose. If we can't save or compare the
        # post-compact snapshot, that is itself a finding — Compact must not
        # be treated as a successful recovery when persistence can't be
        # verified (see decisions.md, 2026-07-27).
        try:
            mc.save_snapshot(session_id, "post")
            findings = mc.compare_snapshots(session_id)
        except Exception as exc:
            findings = [f"unable to verify compact recovery: {exc}"]

        if findings:
            mc.mark_session_degraded(session_id)
            print(f"[bbb-lite] DEGRADED RECOVERY: {'; '.join(findings)}")
        else:
            snapshot = mc.compact_snapshot(session_id)
            total = (
                len(snapshot["tasks"])
                + snapshot["decisions"]
                + snapshot["checklist_items"]
                + (1 if snapshot["role_declared"] else 0)
            )
            print(f"[bbb-lite] compact recovery verified: {total}/{total} items intact")
        return

    if source == "clear":
        print("[bbb-lite] role/context reset, restoring from database")

    checkpoint = mc.latest_checkpoint(session_id)
    last_task = checkpoint["current_task"] if checkpoint and checkpoint["current_task"] else "none"
    open_tasks = mc.list_open_tasks()

    print(
        f"[bbb-lite] session {session_id} ({source}) | "
        f"last task: {last_task} | {len(open_tasks)} open task(s)"
    )
    if checkpoint and checkpoint["next_action"]:
        print(f"[bbb-lite] {checkpoint['next_action']}")

    if checkpoint and checkpoint["risk"]:
        print(f"[bbb-lite] risk: {checkpoint['risk']}")

    # Stage AA: warn if the session that owns the last checkpoint (possibly a
    # different, earlier session — latest_checkpoint() falls back across
    # sessions) never got a clean compact recovery (see mark_session_degraded,
    # Stage R).
    if checkpoint:
        owner_status = next(
            (
                row["status"]
                for row in mc.list_sessions()
                if row["session_id"] == checkpoint["session_id"]
            ),
            None,
        )
        if owner_status == "degraded":
            print(
                "[bbb-lite] WARNING: last session's recovery was degraded - "
                "some info may be missing"
            )

    for text in mc.recent_decisions(limit=2):
        print(f"[bbb-lite] recent decision: {text}")

    # Stage S: surface memory related to the last known task. Fail-soft on
    # purpose (unlike Stage R) — this is a convenience nudge, not a proof of
    # recovery, so a broken embedding model should just skip the line.
    if checkpoint and checkpoint["current_task"]:
        try:
            hits = mc.semantic_search(checkpoint["current_task"], limit=2)
        except Exception:
            hits = []
        for hit in hits:
            print(
                f"[bbb-lite] related memory: {hit['title']} "
                f"({hit['source_file']}, confidence={hit['confidence']})"
            )

    # Stage AA: a bounded-package proof — total chars of the recovery info
    # above, so "this is a small nudge, not a context dump" is measured, not
    # just asserted.
    print(f"[bbb-lite] recovery package: {mc.recovery_package_size(session_id)} chars")


if __name__ == "__main__":
    main()
