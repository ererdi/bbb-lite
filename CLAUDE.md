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
