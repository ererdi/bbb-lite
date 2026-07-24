---
title: Guard blocks every raw path to tasks.status='done'
date: 2026-07-21
confidence: high
---

`GuardedLocalConnection`/`GuardedLocalCursor` in `mc.py` refuse any SQL that would set
`tasks.status='done'` unless it carries the internal `_CompletionAuthorization` capability that
only `complete_task()` mints. This covers plain `conn.execute(...)`, `add_task(..., status="done")`
at creation time, and `conn.cursor().execute(...)` (the cursor factory is guarded too, so
`cursor()` cannot be used to bypass the seam). Verified in `test_mc.py`
(`test_raw_update_to_done_refused`, `test_add_task_with_done_status_refused`,
`test_cursor_cannot_bypass_the_guard`).
