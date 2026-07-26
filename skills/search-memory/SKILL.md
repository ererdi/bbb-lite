---
name: search-memory
description: Look up prior lessons/decisions in memory/ before repeating work or re-deciding something — use when asking "did we already run into this?" or "what did we decide about X?".
---

# /search-memory — look up prior lessons

## Calls

`mc.search_memory(query, limit=3)` and `mc.semantic_search(query, limit=3)`

## The difference between the two

- `search_memory()` is **keyword search**: it re-scores every row in
  `memory_index` by literal term counts (title hit = 3 points, keywords hit =
  2, content hit = 1). Good when the query shares actual words with the
  lesson — fast, no model call, deterministic.
- `semantic_search()` is **embedding search**: it encodes the query with the
  same `sentence-transformers` model used to index each lesson
  (`all-MiniLM-L6-v2`) and ranks by cosine similarity. Finds a paraphrase or
  related concept even when it shares no words with the query — e.g. "unsafe
  database writes" can match a lesson titled "guard blocks done writes"
  purely on meaning.

Try `search_memory()` first (cheap, no model load); reach for
`semantic_search()` when the keyword search comes back empty or the query is
a rephrasing rather than a term match.

## Example

```python
import mc

mc.index_memory_files()  # keep the index current before searching
hits = mc.search_memory("guard done writes")
if not hits:
    hits = mc.semantic_search("guard done writes")
for hit in hits:
    print(hit["source_file"], hit["title"])
```
