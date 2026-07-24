---
title: latest_checkpoint() falls back to the global latest when the session has none
date: 2026-07-21
confidence: high
---

`mc.latest_checkpoint(session_id)` first looks for the newest checkpoint scoped to that
`session_id`; if there isn't one, it silently falls back to the newest checkpoint across the
whole `checkpoints` table instead of returning `None`. `hooks/session_start.py` relies on this:
it calls `latest_checkpoint(session_id)` directly for a brand-new session and still gets useful
"last task" context, with no separate "is this session new?" branch needed.
