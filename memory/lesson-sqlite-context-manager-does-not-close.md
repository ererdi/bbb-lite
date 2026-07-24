---
title: sqlite3's "with conn:" commits/rolls back but never closes the connection
date: 2026-07-21
confidence: high
---

`with _connect(db_path) as conn:` looked like it managed the whole connection lifecycle, but
`sqlite3.Connection.__exit__` only commits or rolls back — it never calls `close()`. On Windows
this leaked file handles and caused `PermissionError: [WinError 32]` when tests tried to delete
their temp db files in `tearDown`. Fixed in `mc.py` by wrapping every connection in
`contextlib.closing(_connect(db_path))` and committing explicitly. Applies to any future code
that opens a `sqlite3.Connection` directly (including ad-hoc scripts/tests using `mc._connect`).
