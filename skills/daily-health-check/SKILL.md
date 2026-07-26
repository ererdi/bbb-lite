---
name: daily-health-check
description: Manually run the deterministic floor checks (stale sessions, memory_index size, long-open tasks, safepoint-stale) outside of the automated bin/daily_check.py schedule — use when asked to check DB/project health right now.
---

# /daily-health-check — run the floor checks on demand

## Calls

`mc.check_floor()`

## What it does

Runs the same deterministic checks `bin/daily_check.py` runs on schedule:
more than 3 stale sessions, `memory_index` over 500 rows, more than 3 tasks
open for 7+ days, and `check_safepoint_stale()` (active sessions with no
recent checkpoint). Returns a list of finding strings — empty if the floor is
clean. Unlike `bin/daily_check.py`, this skill does not sweep stale sessions
or re-index memory first, and does not escalate findings to
`mc.classify()`'s Ollama judge — it's a plain read for a quick manual look,
not the automated pipeline.

## Example

```python
import mc

findings = mc.check_floor()
if not findings:
    print("floor clean")
else:
    for finding in findings:
        print("finding:", finding)
```
