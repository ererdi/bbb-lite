---
title: gemma:2b's duplicate-check in log_lesson() is unreliable
date: 2026-07-24
confidence: medium
---

`mc.log_lesson()`'s ollama-based duplicate check (model `gemma:2b`) can match a new
lesson against an unrelated existing note and report it as a likely duplicate — a false
positive. Two real examples on 2026-07-24: attempting to log a lesson about the
`has_completed_task_since()` session-proxy limitation was blocked twice in a row, each
time against a different, unrelated existing note (`lesson-guard-blocks-raw-done-writes.md`,
then `lesson-latest-checkpoint-fallback.md`) — the verdict was inconsistent across retries
with identical input. A small model isn't reliable for this kind of same-fact judgment call,
which is consistent with why Haroon's version of this system prefers `gemma4:12b` over
smaller models for real judgment decisions.
