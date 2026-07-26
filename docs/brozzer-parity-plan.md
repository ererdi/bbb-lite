# BBB-Lite Brozzer Parity Plan

## Current position

BBB-Lite is a local, text-first assistant prototype. It proves the core BBB OS
shape locally: Mission Control, role binding, lifecycle checkpointing, memory
recall, semantic search, guarded task completion, a demo console, canvas, and an
assistant chat surface.

It is not a full copy of Haroon's Brozzer yet.

## Why voice is not included in this stage

Voice is deliberately out of scope for this step.

Reason:
- Voice is not just another UI. It needs STT, TTS, microphone permissions,
  audio streaming, latency handling, interruption/barge-in behavior, and a
  separate red/green harness.
- Haroon's voice stack also has port isolation and service boundaries so it
  does not collide with Lucy or other live services.
- Adding voice before the text assistant is stable would hide core bugs behind
  audio complexity.

This stage proves the assistant brain first. Voice can be added later as a
separate stage when the text loop is reliable.

## Why this is not full Haroon Brozzer yet

BBB-Lite currently has a partial local Brozzer:
- It defaults to the `brozzer` role.
- It reads Mission Control context.
- It searches keyword memory and semantic memory.
- It writes lifecycle checkpoints after replies.
- It is honest about local capability limits.

Missing compared with Haroon:
- No HOSNI durable dispatch.
- No voice mouth or room-state loop.
- No shared active-turns log across chat and voice.
- No Rider decision veto window.
- No production bridge.
- No Telegram, CRM, email, calendar, or external operational integrations.
- No full CTO/advisor/architect/builder org pipeline running durably outside
  the chat window.

## How to move closer to Haroon

1. Strengthen the Brozzer persona in text first.
   Brozzer should answer from context, refuse unsupported powers, capture
   decisions, and always name the next operational action.

2. Add local decision packets.
   Before HOSNI, create a local `state/dispatch/` ledger where important
   decisions are written as JSON packets.

3. Add a local active-turns log.
   Store every assistant/user turn in a single append-only JSONL log so memory,
   checkpointing, and future voice can share one trace.

4. Add text dispatch before remote dispatch.
   Simulate Brozzer -> architect -> builder locally, using the existing role
   machinery and MC tasks before introducing any remote runner.

5. Add voice last.
   Only after text behavior, decision capture, local dispatch, and logs are
   stable should voice be introduced as its own service.

## Demo wording

"This is not full Haroon Brozzer yet. I intentionally skipped voice in this
stage because voice is a separate product surface: STT, TTS, microphone, latency,
barge-in, and service isolation. What I built here is the local text assistant
core: Mission Control, lifecycle, roles, memory, semantic search, guarded task
completion, and a chat UI. The next step toward Haroon is not voice first; it is
decision packets, active-turn logs, and local dispatch."
