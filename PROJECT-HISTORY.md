# BBB-Lite: Project History, Stakeholder Decisions, and Context

This file exists so a fresh Claude Code session (no prior conversation history)
can understand the full context behind this project without the user having to
re-explain it. Read this once at the start of any new session. Do not delete or
shorten sections when this file is updated in the future; append new context,
keep the old.

## 1. What this project is

BBB-Lite is a small, local, single-user prototype built by Erdi, modeled on
Haroon's real production system, referred to as BBB OS. Haroon is the CTO
who assigned this work. The relationship between the two systems:

- Haroon's real system lives in a GitHub repo called `haroon-bbb-sync`
  (and later a rewritten, more mature version called `bbb-os-core`).
- Haroon's real production system runs on a dedicated server called HOSNI,
  with a workstation called Mini treated as disposable.
- BBB-Lite deliberately has none of that server infrastructure. It runs
  entirely on Erdi's own machine, using SQLite instead of a remote database,
  no daemons, no server processes, no voice/Telegram/web layer.
- The explicit instruction from Haroon at the start was: "separate database,
  separate code, separate folder" — build your own small version, do not
  touch the real system except to read it for reference.

The purpose of this project is partly educational (Erdi learning the real
architecture by rebuilding a small version of it) and partly a genuine
contribution: several fixes and features built here were later ported back
to Haroon's real repos as reviewed pull requests.

## 2. People and roles

- **Haroon**: CTO, owns the real BBB OS system and the company direction
  (Cygility / CygilityAI). Gives tasks, reviews work, holds final authority
  on anything touching the real systems.
- **Erdi**: the primary developer of BBB-Lite, working with Claude Code.
  Originally an SAP SD consultant expanding into AI engineering. Works
  remotely, at his own pace, alongside a day job.
- **Fable**: an AI reviewer/agent on Haroon's side (Claude Fable 5, part of
  Anthropic's Mythos tier) that reviews Erdi's work and assigns tasks via a
  shared file (see section 5, the bus mechanism).
- **Muhammed**: a newer team member, no backend experience, some AI
  familiarity, now working in parallel with Erdi on separate tasks
  (session management side), coordinating through a similar shared file
  (`BUS-ERDI-MUHAMMED.md`).
- **Baran**: a business-side contact; a consulting services section was
  prepared for his website based on BBB OS capabilities.

## 3. Ground rules established early, still in force

- All conversation with the user happens in Turkish; all code, comments,
  and commit messages are in English.
- `haroon-bbb-sync` and `bbb-os-core` are read-only reference material.
  Never cloned in full, never written to, except for the one write below,
  which is flagged honestly as an unresolved question, not a settled
  exception.
- The one write: Erdi's own `bbb-lite` git history was pushed to
  `haroon-bbb-sync` as a new branch, `erdi-bbb-lite-prototype`. This push
  was made without an explicit, written prior approval from Haroon — it was
  a conscious, accepted risk at the time, not an accidental write.
  Retroactively, Haroon has continued to assign tasks via the bus on that
  same branch, which reads as an implicit tolerance of the branch's
  existence, but this has never been confirmed explicitly by Haroon as an
  authorization, and it should not be treated as settled. This is a real
  open question, not resolved history. The `rebuild` and other existing
  branches on that repo were never touched by this push (verified via
  `git ls-remote` before and after).
- Hard boundary, non-negotiable: never touch anything under
  `/home/hosni/elohi-*` or ports 8890/8891. That is a live paying
  customer's production estate, completely separate from the BBB
  development estate.
- Erdi/Claude never has deploy authority on the real systems. Any change
  intended for Haroon's real repos is delivered as a pull request only,
  never pushed live. Deploy authority is held by what Haroon's side calls
  "the K3 seat."
- Only `mc.py` touches the local SQLite database directly. No other file
  runs SQL against it.
- Standard library preferred; the only approved third-party packages are
  `numpy` and `sentence-transformers` (for embeddings). Anything else
  requires asking first.
- No server, no daemon, no background scheduler running independently of
  Windows Task Scheduler or Claude Code hooks. This was flagged early as a
  known tension with Haroon's own rule that "no loop ever runs on a
  workstation, only on HOSNI" — accepted as a deliberate, documented
  exception because this project has only one user and no concurrent
  writers, so the risk that rule protects against does not apply here.

## 4. Architecture philosophy carried over from Haroon's real system

- One permanent partner ("Brozzer" in Haroon's system) versus many
  temporary specialist workers (architect, builder, CTO, reviewer) that
  are spun up for one task and discarded. BBB-Lite's dispatch chain
  (`bin/dispatch.py`) is a small, real implementation of this idea using
  the Claude Agent SDK.
- A report of "done" is never the same as acceptance. Completion requires
  fresh evidence checked at the moment of completion, not just a claim.
  This shows up throughout BBB-Lite as the evidence-gated `complete_task()`
  system.
- Reuse before build: before writing new code, search whether something
  equivalent already exists. BBB-Lite's `cyg_exists()` / `cyg_similar()`
  and later `reuse_receipts` table are the local implementation of this.
- Three-layer memory: a database of raw facts, a vector/embedding layer for
  approximate similarity, and a graph layer for relationships between
  things. Haroon described this explicitly in a live meeting as: the
  database holds facts, vector search finds approximately similar things,
  and the graph is what tells you a relationship exists (for example, that
  a person mentioned in a new sentence is the same person already known,
  not a new one). BBB-Lite's `graph_nodes` / `graph_edges` plus person/
  entity deduplication is the direct implementation of that same idea.

## 5. The bus mechanism (async coordination with Haroon's side)

Haroon's side does not communicate through instant chat for task tracking.
Instead, a single markdown file living in the shared git branch acts as an
asynchronous message bus:

- File: `BUS-ERDI-FABLE.md`, in the `erdi-bbb-lite-prototype` branch.
- Rules: newest entry always goes at the very top; existing entries are
  never edited or deleted; every entry is tagged with a role
  (`[ERDI]`, `[FABLE]`, `[HAROON]`), a date, and a status
  (`WORKING`, `BLOCKED`, `RETURNED`, `QUESTION`, `ACK`).
  An `ACK` entry is mandatory as the first response to any new assignment,
  confirming the message was seen, before real work begins.
  A `git commit` + `git push` to this branch is what counts as actually
  delivering a message; writing it locally without pushing does not count.
  If Haroon answers directly in a way that differs from what Fable said
  earlier, Haroon's answer wins.
- The same pattern (one shared file, newest on top, no edits to history)
  was reused for coordinating with Muhammed, in a separate file
  `BUS-ERDI-MUHAMMED.md`, since it proved to work well for asynchronous,
  low-friction task handoff between people who are not online at the same
  time.

## 6. Major stakeholder-assigned task: the graph-edges gap

Haroon personally verified on his own real system (BBB estate) that
`graph_edges` was completely empty (zero rows) despite hundreds of
documents and symbols being indexed. He assigned Erdi a two-stage task via
the bus:

1. Fix the same gap in Erdi's own prototype first: make the graph actually
   build real edges (not just index nodes), define what an edge means, and
   prove recall improves with a real before/after query.
2. Port the same fix to Haroon's real system as a pull request, after
   first reading Haroon's actual code (`bbb_graph_edges.py`,
   `bbb-graph-server.py`) to match its existing conventions rather than
   copying BBB-Lite's own shape onto it.

This was completed. BBB-Lite's graph went from 0 to over 1300 real edges
(`calls` edges from AST call analysis, `references` edges from doc-to-symbol
mentions), proven live via `find_callers()` returning real results. A
draft exporter (`bin/bbb_graph_edge_builder.py`) was built to translate
BBB-Lite's edges into Haroon's real `edges.jsonl` schema, but this was
explicitly flagged as unverified against the real tree (cloning
`haroon-bbb-sync` to test it directly is against the read-only rule), so
the PR stage is still pending Haroon's own dry run or explicit go-ahead.

During the code review that came with this task, Fable also found two real
issues in BBB-Lite that were fixed:
- `heal_memory()` was auto-applying Ollama's merge verdicts with no human
  approval step, the same pattern Haroon's own team had previously removed
  after it erased facts that later mattered. Fixed: merges are now proposed
  into a `pending_merges` table and require an explicit `approve_merge()`
  call.
- The dispatch chain's "builder" step only describes a change in prose, it
  does not actually write code. This is accepted as fine for this stage,
  but must never be presented as a working autonomous coder.

## 7. bbb-os-core: the rewritten reference system

At a later point, Haroon shared a full rewrite of his system called
`bbb-os-core` (also on GitHub, `TrustSourcing/bbb-os-core`), architecturally
much larger than BBB-Lite (SSH tunnels to HOSNI, voice, Telegram, systemd
daemons, a full release/acceptance pipeline, an npm/React web layer). It is
read-only reference material under the exact same rule as `haroon-bbb-sync`.

A structured comparison was done to find genuine logical gaps between
BBB-Lite and bbb-os-core, deliberately scoped to concepts, not
architecture (BBB-Lite's hard constraints, SQLite-only, no server, stdlib
only, stay fixed regardless of what bbb-os-core does). The comparison found
BBB-Lite was already ahead in several areas (evidence records, health
events, decision packets, the entity/decision graph). Seven real gaps were
identified and closed as purely additive changes to `mc.py`:

1. Reuse receipts: recording what a caller actually did with a
   `cyg_exists()` / `cyg_similar()` hit (reuse it, build new, or no match),
   not just returning a search result and forgetting the decision.
2/4/6. A single "task_receipts" acceptance step folding together three
   originally separate ideas from bbb-os-core: a formal accept step
   separate from reporting completion, lightweight in-line acceptance
   guard checks (evidence still valid, health not blocked), and
   role-gated acceptance authority (added an `"accept"` boolean to the
   existing `ROLE_PERMISSIONS` matrix, no new permission system invented).
   Since BBB-Lite is single-user, the accepting session does not need to
   differ from the reporting session, unlike bbb-os-core's approver-is-
   never-the-doer assumption, which is a live multi-person system's rule.
3. A hook-triggered maintenance battery (the "Living Contract" idea from
   bbb-os-core, without any daemon). A real conflict was caught and
   avoided here before it caused a bug: `get_health_state()` counts any
   recent `health_events` row toward its degraded/blocked thresholds, so
   logging every passing check there would have falsely pushed a single
   clean session into "blocked." The fix: only failures get mirrored into
   `health_events`; every check's pass or fail result goes into a new,
   separate `maintenance_runs` table instead.
5 and 7: (kept for completeness, details in `memory/decisions.md` under
   the corresponding dated entries — this history file intentionally does
   not repeat what is already fully documented there).

This work is believed complete as of the last status check, but full
verification (complete test suite passing, real row counts in the live
database, coordination between this work's `state_hash` and any separately
added `git_state_hash()` mechanism) should be re-confirmed rather than
assumed, per this project's own evidence-over-claims rule.

## 8. A recurring, confirmed model behavior to watch for

On at least three separate occasions (the `decision:` chat command, the
`remind:` chat command, and again with a `K3 seat` decision), the assistant
running inside `bin/assistant_server.py` incorrectly told the user it could
not save something because "this session's data permission is false," when
in reality the underlying command (`decision:`, `remind:`, `task:`) never
checks role permissions at all and the write succeeded regardless. This is
a confirmed pattern of the model inventing a plausible-sounding reason for
its own behavior that does not match what the code actually does. The
practical rule this taught: never trust the assistant's own narration about
whether something was saved or blocked; always verify against the real
database (`mc.query_rows(...)`) before accepting either a success or a
failure claim at face value.

## 9. Known, deliberately accepted scope limits (not bugs)

- No task-level deduplication in `create_reminder()`. Calling `remind:`
  twice with identical text and date creates two separate tasks. Person-
  node deduplication works correctly; task-level dedup does not exist yet
  and was consciously left unfixed pending a decision on whether it is
  worth adding.
- No retry logic anywhere. If `ask_ollama()` or the embedding model call
  fails, it is logged and skipped, never retried automatically.
- Memory version history does not store old content, only that something
  changed and when. Recovering exactly what an older version of a note
  said is not currently possible.
- No owner field on memory records (fine for a single user; would need
  revisiting now that Muhammed is contributing too).
- Conflict detection (`detect_memory_conflicts`, `detect_decision_conflicts`)
  only detects and warns; nothing resolves a conflict automatically or
  ranks which record should win.
- The full-history-versus-structured-recovery-package size comparison
  that the "Efficient Memory Recovery" requirement called for was never
  actually built as a test; only an absolute size ceiling is tested.

## 10. Business/consulting context (background, not engineering, but relevant)

In parallel with the engineering work, a set of consulting service
offerings was drafted (memory/context engineering for AI agents, AI agent
reliability and guardrails, local-first AI setup, Claude Code / AI tooling
setup for other teams' engineers, RAG integration, AgentOps-style health
monitoring, personal AI assistant setup for small businesses), intended to
generate revenue before the main BBB OS product is fully launched. A
business-facing capabilities document and an executive-level consulting
brief were prepared and reviewed with Baran for use on his company site.
This is company-strategy context, not something that should influence
engineering decisions in this repository, but it explains why some
requests in past sessions were about report/document preparation rather
than code.

## 11. Current open workstreams as of the last check-in

Two topics were explicitly identified as still open and not yet fully
closed:

**Memory / DB**: version history content storage, owner field, conflict
resolution beyond detection, task/decision-level dedup, retry logic,
full-history comparison test, and fixing or working around the model
self-report hallucination pattern from section 8.

**Session management**: multi-session coordination, role handoff between
sessions, real-time (not just daily-batch) stale session detection, task
prioritization beyond oldest-first, identity awareness tied to the
person/entity graph nodes, a distinct degraded-boot state, and session
merge/split handling. Three of these (task prioritization, degraded boot,
real-time stale detection) were handed to Muhammed as his first three
tasks, deliberately chosen to be small, separate from each other, and not
overlapping with whatever Erdi is working on at the same time, to avoid
merge conflicts between the two of them working in parallel.

This history file should be updated (appended, never shortened) whenever
a new major stakeholder decision, piece of feedback, or architectural
ruling is made, so future sessions do not lose this context.
