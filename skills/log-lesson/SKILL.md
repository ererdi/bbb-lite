---
name: log-lesson
description: Save one durable fact/lesson to memory/ and index it — use when a mistake was found and fixed, or a non-obvious fact was learned that future sessions should know.
---

# /log-lesson — save a lesson to memory

## Calls

`mc.log_lesson(title, content, confidence="medium")`

## What it does

Writes one `memory/lesson-<date>-<slug>.md` file (one fact per file), then
re-indexes `memory/` via `index_memory_files()`. Before writing, it searches
existing lessons with `search_memory()` and asks the local Ollama model
(`ask_ollama()`) whether the new note is a near-duplicate of the closest
match; if the model says yes, the write is skipped and `None` is returned
instead of a new file. If Ollama is unavailable, the lesson is kept (fails
open, not closed — see `memory/lesson-*duplicate-check*unreliable.md`).

## Example

```python
import mc

path = mc.log_lesson(
    title="Guard blocks done writes",
    content="Raw SQL setting tasks.status='done' is refused; complete_task() is the only way.",
    confidence="high",
)
# path is None if judged a duplicate of an existing lesson, else the new file's Path.
```
