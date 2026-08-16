"""SessionStart hook: print last checkpoint and open task count."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mc
import post_compact_recap


def main() -> None:
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id", "unknown")
    source = payload.get("source", "unknown")
    cwd = payload.get("cwd") or os.getcwd()
    # Stage AH: not a documented field of Claude Code's real SessionStart
    # payload (checked against the hooks docs, 2026-07-29) - read it
    # defensively so a payload without it behaves exactly as before.
    client_session_uuid = payload.get("client_session_uuid")

    mc.init_db()

    # Stage AU: failure visibility, first thing - a degraded/blocked state
    # (recent checkpoint/db failures, or too many failures of any kind) is
    # worth knowing about before anything else prints. Fail-soft: this is a
    # visibility nudge, not a gate, so a broken health check must not itself
    # block session start.
    try:
        health_state = mc.get_health_state()
    except Exception:
        health_state = "healthy"
    if health_state != "healthy":
        print(
            f"[bbb-lite] SYSTEM HEALTH: {health_state} - see health_events for details"
        )

    # bbb-os-core parity item 3: hook-triggered maintenance battery, the
    # daemon-free local analog of its "Living Contract" self-check. Fail-soft
    # - a broken check must not itself block session start.
    try:
        mc.run_maintenance_checks(session_id)
    except Exception:
        pass

    # Look up any earlier session tied to this client_session_uuid BEFORE
    # ensure_session() below tags the *current* session_id with it - otherwise
    # the lookup would just find itself.
    linked_session_id = (
        mc.find_session_by_client_uuid(client_session_uuid) if client_session_uuid else None
    )
    mc.ensure_session(session_id, client_session_uuid=client_session_uuid)
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
    # the declared role would be lost. Inherit a role instead. NOT fail-soft
    # on purpose: if there is no previous role to inherit either, say so out
    # loud rather than defaulting quietly (see decisions.md, 2026-07-29).
    if source == "clear" and not mc.role_exists(session_id):
        # Stage AH: if client_session_uuid linked us to a specific earlier
        # session, that is a more precise connection than "whatever role was
        # declared most recently anywhere" - prefer it. Otherwise fall back
        # to Stage AF's original behavior unchanged (see decisions.md,
        # 2026-07-29).
        if linked_session_id and linked_session_id != session_id:
            inherited = mc.get_role(linked_session_id)
            mc.set_role(session_id, inherited)
            print(
                f"[bbb-lite] role inherited after /clear via client_session_uuid: {inherited}"
            )
        else:
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
        # Stage AI: moved to hooks/post_compact_recap.py so it maps onto
        # Claude Code's real PostCompact event, not just SessionStart's
        # "compact" matcher - see decisions.md, 2026-07-29. Still called
        # directly here so this matcher's behavior is unchanged.
        post_compact_recap.recap(session_id)
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

        # Stage AT: same fail-soft treatment as the related-memory nudge
        # above — a conflict check that can't run (e.g. broken embedding
        # model) should skip silently, not break session start.
        try:
            conflicts = mc.detect_memory_conflicts(checkpoint["current_task"])
        except Exception:
            conflicts = []
        if conflicts:
            print(
                f"[bbb-lite] CONFLICT WARNING: found {len(conflicts)} conflicting "
                "memory record(s) for this topic - review before trusting"
            )

    # Stage AA: a bounded-package proof — total chars of the recovery info
    # above, so "this is a small nudge, not a context dump" is measured, not
    # just asserted.
    print(f"[bbb-lite] recovery package: {mc.recovery_package_size(session_id)} chars")

    # Stage AL: after everything else, a one-line proof that /clear recovery
    # actually landed - role, open tasks, checkpoint presence (see
    # decisions.md, 2026-07-29).
    if source == "clear":
        verified = mc.clear_recovery_verified(session_id)
        checkpoint_state = "var" if verified["latest_checkpoint"] else "yok"
        print(
            f"[bbb-lite] clear recovery verified: role={verified['role']}, "
            f"{len(verified['open_task_ids'])} open tasks, checkpoint={checkpoint_state}"
        )


if __name__ == "__main__":
    main()
