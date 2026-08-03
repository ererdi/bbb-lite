# BUS — ERDI ↔ FABLE

Async message bus between Erdi (and his Claude) and Haroon's BBB team.
**Newest entry at the TOP. Never edit or delete an existing entry — append above.**

Header format, one line: `## [ROLE] YYYY-MM-DD HH:MM TZ — STATUS — subject`
- ROLE: `ERDI` | `FABLE` | `HAROON`
- STATUS: `WORKING` | `BLOCKED` | `RETURNED` | `QUESTION` | `ACK`

**ACK is mandatory.** Nothing counts as delivered until the other side posts an ACK
entry naming what it acknowledges. A file that was written is not a message that was
read. BLOCKED means: state the exact thing you need, then stop — do not build past an
ambiguity. Keep entries short; evidence belongs in the repo or the PR, not here.
Commit and push after every write — the push is the delivery.

---

## [FABLE] 2026-08-03 — WORKING — bus opened; GRAPH-EDGES task assigned

Bus is live. Task sent to Erdi separately (Stage 1: build real edges in the
prototype; Stage 2: port the fix to the BBB estate as a PR).

Verified live on the estate today: graph has 248 docs and 13,599 symbols indexed and
`graph_edges` is EMPTY — zero edges. `bbb_graph_edges` exists and is imported by
`/home/hosni/bbb-graph/bbb-graph-server.py`, so ingestion works and edge-building
either never runs or never writes. Same hole in the prototype.

Awaiting Erdi's ACK.
