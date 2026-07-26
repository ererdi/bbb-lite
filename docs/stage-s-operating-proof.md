# Stage S: Operating Proof Hardening

Stage S closes the first seven serious parity gaps against Haroon's BBB OS shape
without pretending that this local project has HOSNI, voice, or production
bridges.

## What Changed

- Mission Control now stores answer quality, answer receipts, and lifecycle run
  proof in SQLite.
- Assistant replies are wrapped by a local Brozzer contract when the model output
  is too generic or weak.
- Every assistant reply can leave a checkpoint, score, evidence row, and receipt.
- Dispatch CLI now writes a durable local run ledger: classifier, architect,
  builder, completion status, and evidence.
- Lifecycle hooks write `lifecycle_runs` rows when they execute.
- Graph edges now carry `currentness`, `confidence`, and `curated` metadata.

## Local Adaptations

- HOSNI is still intentionally absent; SQLite is the local source of truth.
- Voice is still intentionally absent because STT/TTS, microphone permissions,
  latency, barge-in, and a separate voice harness are a separate product surface.
- Claude Code hook settings remain opt-in because this project can otherwise
  observe itself while developing itself in multiple terminals.
- Advisor/Architect/Builder is still local dispatch evidence, not Haroon's full
  durable multi-window remote bridge.

## Demo Proof

In the Assistant Console, the MC Trace panel should show:

- turns
- decision packets
- dispatch runs
- evidence
- quality scores
- answer receipts
- lifecycle runs
- graph stats with curated edge count

The important demo claim is not "the model always answers well." The claim is:
when Claude is limited, unavailable, or weak, BBB-Lite still records the engine,
wraps the answer in a local Brozzer operating contract, scores it, and stores a
receipt that can be inspected later.
