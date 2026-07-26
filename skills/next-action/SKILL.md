---
name: next-action
description: Ask "what should I work on next?" — use at the start of a session or after finishing a task, to pick up the oldest still-open task.
---

# /next-action — what's the next open task

## Calls

`mc.pick_next_action()`

## What it does

Returns `"Next: <title>"` for the oldest task whose `status != 'done'`
(oldest by `created_at`, via `list_open_tasks()`), or `None` if there is no
open task at all. It does not mutate anything — purely a read.

## Example

```python
import mc

action = mc.pick_next_action()
if action is None:
    print("no open tasks")
else:
    print(action)  # e.g. "Next: write tests"
```
