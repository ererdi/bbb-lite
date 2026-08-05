# bbb-lite

Independent learning project (not affiliated with any production BBB system).

## Rules

- Code (variable names, comments) is written in English. Conversation with the
  user can be in Turkish.
- Database: SQLite only, a single local file, no server.
- Prefer the standard library. Exceptions currently in use: numpy and
  sentence-transformers (Stage E, embedding-based search) — any further
  third-party package needs to be asked about first.
- The `haroon-bbb-sync` GitHub repo may be read for reference only — never
  cloned, never written to.
- Exactly one file, `mc.py`, touches the database directly. No other script
  runs SQL.

## External repo rules

- No operation against the `haroon-bbb-sync` repo (production/BBB estate) may
  ever be a DEPLOY. It may only be proposed as a PR (pull request).
- Nothing under `/home/hosni/elohi-*` and nothing on ports 8890/8891 is ever
  touched. This is a live customer system — a hard boundary.
- These rules were given by Fable in `BUS-ERDI-FABLE.md`.
