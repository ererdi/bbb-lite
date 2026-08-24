---
title: latest_checkpoint() fallback is scoped to the session's project when known
date: 2026-08-21
confidence: high
---

`mc.latest_checkpoint(session_id)` first looks for the newest checkpoint scoped to that
`session_id`. If there isn't one, the fallback now depends on whether `sessions.project_id`
is known for that session_id:

- **Known project_id**: the fallback is limited to `WHERE project_id = ?` — only checkpoints
  belonging to sessions in the *same* project can surface. A session in one project must never
  show another project's checkpoint as "recovery" context (this was the Adım 1 bug: opening a
  new session in an unrelated project could surface bbb-lite's last checkpoint).
- **Unknown project_id** (the session was never registered via `ensure_session`, or was
  registered with an unmapped cwd — `resolve_project()` returned `status="unmapped"`): falls
  back to the newest checkpoint across the *whole* `checkpoints` table, exactly as before. This
  preserves the original Stage-era design: a brand-new/unclassified session still gets a useful
  "last task" nudge instead of a hard `None`. See
  `test_latest_checkpoint_falls_back_when_session_has_none` in `test_mc.py`, which pins this
  branch — it must keep passing unchanged.

`hooks/session_start.py` relies on this: it calls `mc.resolve_project(cwd)` *before*
`mc.ensure_session(...)` now (order was flipped for this fix) so the resolved project_id can be
passed straight into `ensure_session(..., project_id=...)`, and `latest_checkpoint(session_id)`
can then look it up.
