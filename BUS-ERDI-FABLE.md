# BUS — ERDI ↔ FABLE

Async message bus between Erdi (with his Claude) and Haroon's BBB team.
Erdi: read this whole file with your Claude before starting. Everything you need is here.

**Newest entry at the TOP. Never edit or delete an existing entry — append above.**
Header, one line: `## [ROLE] YYYY-MM-DD — STATUS — subject`
ROLE: `ERDI` | `FABLE` | `HAROON` · STATUS: `WORKING` | `BLOCKED` | `RETURNED` | `QUESTION` | `ACK`

**ACK is mandatory.** Nothing is delivered until the other side posts an ACK entry naming
what it acknowledges — a file that was written is not a message that was read. **BLOCKED**
means: state the exact thing you need, then stop; do not build past an ambiguity. Keep
entries short — evidence goes in the repo or the PR, not here. Commit and push after every
write; that push is the delivery. Haroon and Fable read this when active. If Haroon
answers directly and Fable said otherwise, Haroon wins.

---

# YOUR PROTOTYPE — REVIEW

Good work. It runs cold, 578 of your 586 tests pass unmodified (the 8 failures are Windows
path fixtures in `test_project_resolution.py` / `test_client_session_uuid.py` /
`test_compact_recovery.py`, not logic), and `memory/decisions.md` correcting its own earlier
false claims in place is the single best habit in the repo.

Two corrections you should know:
1. **`heal_memory()` auto-applies** an Ollama yes/no verdict with no approval step
   (`mc.py:4013-4064`). That is the exact pattern BBB removed after it erased facts that
   later mattered. Keep your archive-not-delete design; never keep the auto-apply trigger.
2. **Your dispatch chain is a simulation** — the "builder" role is prompted to *describe* a
   change (`bin/dispatch.py:39`), not write code. Fine as a stage; just don't treat it as
   working dispatch.

One boundary to confirm: your own `CLAUDE.md` says `haroon-bbb-sync` is read-only for you
and must never be written to, yet your prototype branch was pushed onto it. Fine as
delivery — just confirm it was deliberate so the rule is clear for both sides.

# YOUR TASK — GRAPH EDGES, TWO STAGES

You were right that the graph is not working. Verified live on the BBB estate today:
**248 docs and 13,599 symbols indexed, and `graph_edges` is EMPTY — zero edges.** The
module `bbb_graph_edges` exists and is imported by `bbb-graph-server.py`, so ingestion
works and edge-building either never runs or never writes. Your prototype has the same
shape and the same hole: `bbb_graph_ingest` and `bbb_graph_doc_upsert` index symbols and
docs, no edges.

**Stage 1 — in your own prototype.** Make the graph actually build edges. Decide and
document what an edge means (call, import, doc→symbol reference — your call, justify it),
write them, and prove recall improves: one query that returns something useful with edges
and returned nothing without them. Put your reasoning in `decisions.md` as you go.

**Stage 2 — against the BBB estate, as a pull request.** Port the same fix.
- Server: `/home/hosni/bbb-graph/bbb-graph-server.py`, with `bbb_graph_edges` alongside it
- DB: `bbb-graph.db` · Health: `127.0.0.1:8889/health` (reports `edges`)
- Success: `edges` goes from 0 to a real number, and `/search` demonstrably uses them
- Read our code first and match its existing recall contract. Do not restructure it to
  look like yours.

**Rules.** You deliver a PR, never a deploy — the K3 seat holds all deploy authority.
Read-only on the servers. **Never touch anything under `/home/hosni/elohi-*` or ports
8890/8891 — that is a live customer estate, hard boundary.** Work at your own pace; nothing
on the BBB critical path blocks on this.

**Why this task:** same gap on both sides, fully isolated, and it walks you through BBB's
recall contract — the fastest way to get fluent in the real codebase.

---

## [FABLE] 2026-08-03 — WORKING — bus opened; GRAPH-EDGES assigned

Task and review above. Evidence for the zero-edges claim is a live readback of
`127.0.0.1:8889/health` on the estate today.

Awaiting your ACK entry.
