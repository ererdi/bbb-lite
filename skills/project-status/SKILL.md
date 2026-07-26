---
name: project-status
description: Summarize how many sprints and open/done tasks a project has — use when asked "how's project X doing" or "how many open tasks are left in this sprint set".
---

# /project-status — summarize a project's sprints and tasks

## Calls

`mc.project_status(project_id)`, which itself combines `mc.list_projects()`,
`mc.list_sprints(project_id)`, `mc.list_open_tasks()`, and `mc.list_tasks()`.

## What it does

Looks up the project by id (raising `ValueError` if unknown), collects all of
its sprint ids via `list_sprints(project_id)`, then counts how many of that
project's tasks (tasks whose `sprint_id` is one of those sprint ids) are open
vs. done. Returns:

```python
{
    "project_id": int,
    "name": str,
    "sprint_count": int,
    "open_task_count": int,
    "done_task_count": int,
}
```

Tasks with no `sprint_id` (created before Stage L, or never assigned to a
sprint) are not counted against any project.

## Example

```python
import mc

project_id = mc.add_project("bbb-lite")
sprint_id = mc.add_sprint(project_id, "sprint 1")
mc.add_task("write tests", sprint_id=sprint_id)

status = mc.project_status(project_id)
print(f"{status['name']}: {status['open_task_count']} open, "
      f"{status['done_task_count']} done across {status['sprint_count']} sprint(s)")
```
