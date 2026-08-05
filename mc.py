"""Mission Control access — the only file allowed to touch the database.

The guarded connection below is ported from haroon-bbb-sync's
lib/bbb_mc_access.py (rebuild branch, GuardedLocalConnection/GuardedLocalCursor
and its SQL-shape parser). Remote bridge, the write-gate nonce/marker system,
board hygiene, and human-approval scaffolding were stripped — this project is
a single local sqlite file with local mode as the only mode. What's kept:
a connection (and its cursor()) that refuses any raw SQL setting
tasks.status='done'; only complete_task() may do that.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import re
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

DB_PATH = Path(__file__).resolve().parent / "mc.db"
DECISIONS_MD_PATH = Path(__file__).resolve().parent / "memory" / "decisions.md"
PRETOOLUSE_LOG_PATH = Path(__file__).resolve().parent / "hooks" / "pretooluse.log"
_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
_embedding_model: Any = None
_embedding_unavailable = False
GRAPH_EDGE_TYPES = ("supersedes", "depends-on", "evidence-for", "contradicts", "derived-from")
GRAPH_MAX_HOPS = 2
UNSUPPORTED_CLAIM_TERMS = (
    "hosni",
    "voice",
    "telegram",
    "calendar",
    "email",
    "crm",
    "production bridge",
)
UNSUPPORTED_ACTION_TERMS = (
    "sent",
    "created",
    "scheduled",
    "connected",
    "accessed",
    "gonderdim",
    "gönderdim",
    "olusturdum",
    "oluşturdum",
    "baglandim",
    "bağlandım",
    "eristim",
    "eriştim",
)

DEFAULT_CAPABILITY_BOUNDARIES = {
    "hosni": "Local prototype only; HOSNI durable dispatch is not connected.",
    "voice": "Out of scope for this stage; needs STT/TTS, mic, latency, and voice harness work.",
    "telegram": "No Telegram bridge in bbb-lite.",
    "crm": "No CRM integration in bbb-lite.",
    "production_bridge": "No production MC bridge; SQLite is the local source of truth.",
    "calendar": "No calendar write integration in bbb-lite.",
    "email": "No email integration in bbb-lite.",
}


class MCAccessError(RuntimeError):
    """Base error for guarded MC access failures."""


class MCCompletionRefused(MCAccessError):
    """A task transition to done was attempted outside complete_task()."""


class MCDegraded(Exception):
    """A read or write could not reach the database, or failed a guard check."""


@dataclass(frozen=True)
class _CompletionAuthorization:
    """Internal capability minted only by complete_task()."""

    task_id: str


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def _split_sql_csv(text: str) -> list[str]:
    """Small SQL CSV splitter for a single INSERT values list."""

    out: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
        i += 1
    out.append(text[start:].strip())
    return out


def _sql_parenthesized(text: str, open_at: int) -> tuple[str, int] | None:
    """Return balanced SQL parenthesis content + closing index."""

    if open_at < 0 or open_at >= len(text) or text[open_at] != "(":
        return None
    depth = 0
    quote: str | None = None
    i = open_at
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i], i
        i += 1
    return None


def _bound_value(token: str, sql_prefix: str, params: Iterable[Any]) -> tuple[bool, Any]:
    """Return (known, value) for a literal or positional placeholder."""

    token = token.strip()
    if len(token) >= 2 and token[0] in {"'", '"'} and token[-1] == token[0]:
        return True, token[1:-1]
    if token == "?":
        values = list(params)
        index = sql_prefix.count("?")
        if index < len(values):
            return True, values[index]
        return False, None
    if re.fullmatch(r"NULL", token, re.I):
        return True, None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
        return True, token
    return False, None


def _insert_values(sql: str, params: Iterable[Any]) -> tuple[str | None, dict[str, tuple[bool, Any, str]]]:
    """Return table + simple single-row INSERT assignments; unknown shape is empty."""

    clean = _strip_sql_comments(sql)
    match = re.search(
        r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+(?:\w+\.)?"
        r"[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?\s*\(",
        clean,
        re.I,
    )
    if not match:
        return None, {}
    columns_group = _sql_parenthesized(clean, match.end() - 1)
    if columns_group is None:
        return match.group(1).lower(), {}
    columns_text, columns_end = columns_group
    values_match = re.match(r"\s*VALUES\s*\(", clean[columns_end + 1:], re.I)
    if not values_match:
        return match.group(1).lower(), {}
    values_open = columns_end + 1 + values_match.end() - 1
    values_group = _sql_parenthesized(clean, values_open)
    if values_group is None:
        return match.group(1).lower(), {}
    values_text, values_end = values_group
    if not re.fullmatch(r"\s*;?\s*", clean[values_end + 1:]):
        return match.group(1).lower(), {}
    columns = [part.strip().strip('"`[]').lower() for part in _split_sql_csv(columns_text)]
    values = _split_sql_csv(values_text)
    if len(columns) != len(values):
        return match.group(1).lower(), {}
    out: dict[str, tuple[bool, Any, str]] = {}
    search_from = values_open + 1
    for column, token in zip(columns, values):
        token_at = clean.find(token, search_from)
        if token_at < 0:
            token_at = search_from
        known, value = _bound_value(token, clean[:token_at], params)
        out[column] = (known, value, token.strip())
        search_from = token_at + len(token)
    return match.group(1).lower(), out


def _is_task_done_write(sql: str, params: Iterable[Any] = ()) -> bool:
    """Conservatively identify a tasks.status transition that may produce done.

    Unknown expressions are treated as potentially-done. Ordinary updates to
    another task column, or a statically-known non-done status, are unaffected.
    """

    clean = _strip_sql_comments(sql)
    values = list(params)
    update = re.search(r"\bUPDATE\s+(?:OR\s+\w+\s+)?(?:\w+\.)?[\"`\[]?tasks[\"`\]]?\s+SET\s+", clean, re.I)
    if update:
        tail = clean[update.end():]
        set_text = re.split(r"\bWHERE\b|\bRETURNING\b", tail, maxsplit=1, flags=re.I)[0]
        for assignment in _split_sql_csv(set_text):
            match = re.match(
                r"\s*[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?\s*=\s*(.+?)\s*$",
                assignment,
                re.I | re.S,
            )
            if not match:
                # Tuple/row-value and other unparsed assignments can conceal
                # status. Completion must fail closed independently.
                return True
            if match.group(1).lower() != "status":
                continue
            token = match.group(2)
            absolute_prefix = clean[: clean.find(assignment, update.end()) + match.start(2)]
            known, value = _bound_value(token, absolute_prefix, values)
            if not known or str(value).lower() == "done":
                return True
        return False

    insert = re.search(
        r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+(?:\w+\.)?"
        r"[\"`\[]?tasks[\"`\]]?\s*\(", clean, re.I,
    )
    if insert:
        table, assignments = _insert_values(clean, values)
        if table != "tasks" or not assignments:
            return True
        status = assignments.get("status")
        if status is None:
            return False
        known, value, _token = status
        if not known or str(value).lower() == "done":
            return True

    if re.search(
        r"\bREPLACE\s+(?:OR\s+\w+\s+)?INTO\s+(?:\w+\.)?"
        r"[\"`\[]?tasks[\"`\]]?\b", clean, re.I,
    ):
        return True

    # UPSERT/CTE variants that name the tasks table and assign status are
    # intentionally conservative if the simple parsers above did not prove a
    # non-done value.
    if (re.search(r"\b(?:UPDATE|INSERT|REPLACE)\b", clean, re.I)
            and re.search(r"\btasks\b", clean, re.I) and re.search(
        r"\bstatus\s*=\s*(?:['\"]done['\"]|CASE\b|\(|SELECT\b)", clean, re.I
    )):
        return True
    return False


def _guard_task_completion(
    sql: str,
    params: Iterable[Any],
    authorization: _CompletionAuthorization | None,
) -> None:
    if _is_task_done_write(sql, params) and not isinstance(authorization, _CompletionAuthorization):
        raise MCCompletionRefused(
            "tasks.status='done' refused at MC seam; use mc.complete_task() instead"
        )


class GuardedLocalCursor(sqlite3.Cursor):
    """Cursor form of the same guard; conn.cursor() cannot bypass the seam."""

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> sqlite3.Cursor:
        params = tuple(parameters)
        _guard_task_completion(sql, params, None)
        return super().execute(sql, params)

    def executemany(
        self, sql: str, seq_of_parameters: Iterable[Iterable[Any]]
    ) -> sqlite3.Cursor:
        rows = [tuple(row) for row in seq_of_parameters]
        for params in rows:
            _guard_task_completion(sql, params, None)
        return super().executemany(sql, rows)

    def executescript(self, sql_script: str) -> sqlite3.Cursor:
        for statement in sql_script.split(";"):
            if statement.strip():
                _guard_task_completion(statement, (), None)
        return super().executescript(sql_script)


class GuardedLocalConnection(sqlite3.Connection):
    """sqlite connection that keeps the completion gate in the only mode we have."""

    def execute(
        self,
        sql: str,
        parameters: Iterable[Any] = (),
        *,
        _completion_authorization: _CompletionAuthorization | None = None,
    ) -> sqlite3.Cursor:
        params = tuple(parameters)
        _guard_task_completion(sql, params, _completion_authorization)
        return sqlite3.Connection.cursor(self, factory=sqlite3.Cursor).execute(sql, params)

    def executemany(
        self,
        sql: str,
        seq_of_parameters: Iterable[Iterable[Any]],
    ) -> sqlite3.Cursor:
        rows = [tuple(row) for row in seq_of_parameters]
        for params in rows:
            _guard_task_completion(sql, params, None)
        return sqlite3.Connection.cursor(self, factory=sqlite3.Cursor).executemany(sql, rows)

    def executescript(self, sql_script: str) -> sqlite3.Cursor:
        for statement in sql_script.split(";"):
            if statement.strip():
                _guard_task_completion(statement, (), None)
        return sqlite3.Connection.cursor(self, factory=sqlite3.Cursor).executescript(sql_script)

    def cursor(self, _factory: Any = None) -> sqlite3.Cursor:
        # A caller-supplied stock Cursor factory would reopen the bypass this
        # connection exists to close, so the guarded factory is unconditional.
        return super().cursor(factory=GuardedLocalCursor)


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, factory=GuardedLocalConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Public name for _connect() — same GuardedLocalConnection, no bypass.

    Named after haroon-bbb-sync's connect() for familiarity; behavior is
    entirely this project's existing guard, not a new access path.
    """

    return _connect(db_path)


def query_rows(sql: str, params: tuple = (), db_path: Path = DB_PATH) -> list[dict]:
    """Read-only helper: only SELECT statements are accepted.

    Not a general execute() — INSERT/UPDATE/DELETE/etc. raise ValueError
    before the query ever reaches sqlite, so this cannot become a backdoor
    around GuardedLocalConnection's write guard.
    """

    if not sql.strip().upper().startswith("SELECT"):
        raise ValueError("query_rows() only accepts SELECT statements")
    with contextlib.closing(connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


_GUARDED_OPERATIONS = frozenset({"complete_task", "add_curated_edge", "mark_session_degraded"})


def write_gate(operation: str) -> bool:
    """Documentation lookup: does `operation` already have a dedicated guard?

    This does not add a new restriction — it records which existing
    functions (complete_task's completion guard, add_curated_edge's
    dual-approval gate, mark_session_degraded's degraded-marking path)
    are the sanctioned way to perform a sensitive operation, so callers
    can check before reaching for raw SQL.
    """

    return operation in _GUARDED_OPERATIONS


def readback_verify(table: str, row_id: int, db_path: Path = DB_PATH) -> dict:
    """Confirm (table, row_id) really persisted, via a fresh connection.

    A general "did what I just write actually land" check, independent of
    Stage R's snapshot mechanism. Optional for callers — complete_task() and
    similar critical writes may call this after commit(), but nothing
    requires it and existing callers are unaffected. Raises MCDegraded if
    the table is unknown or the row can't be found.
    """

    with contextlib.closing(connect(db_path)) as conn:
        known_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if table not in known_tables:
            raise MCDegraded(f"readback_verify: unknown table {table!r}")
        row = conn.execute(
            f'SELECT * FROM "{table}" WHERE id = ?', (row_id,)
        ).fetchone()
    if row is None:
        raise MCDegraded(f"readback_verify: no row in {table!r} with id={row_id}")
    return dict(row)


def init_db(db_path: Path = DB_PATH) -> None:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                status TEXT NOT NULL
            )"""
        )
        existing_session_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "client_session_uuid" not in existing_session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN client_session_uuid TEXT")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS roles (
                session_id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                declared_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS checkpoints (
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                current_task TEXT,
                last_decision TEXT,
                next_action TEXT,
                open_queue TEXT,
                risk TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sprints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )"""
        )
        existing_task_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "sprint_id" not in existing_task_columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN sprint_id INTEGER")
        if "parent_task_id" not in existing_task_columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS memory_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                keywords TEXT NOT NULL
            )"""
        )
        existing_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(memory_index)").fetchall()
        }
        if "embedding" not in existing_columns:
            conn.execute("ALTER TABLE memory_index ADD COLUMN embedding BLOB")
        if "confidence" not in existing_columns:
            conn.execute("ALTER TABLE memory_index ADD COLUMN confidence REAL DEFAULT 0.5")
        if "doc_type" not in existing_columns:
            conn.execute("ALTER TABLE memory_index ADD COLUMN doc_type TEXT DEFAULT 'lesson'")
        if "project" not in existing_columns:
            conn.execute("ALTER TABLE memory_index ADD COLUMN project TEXT DEFAULT 'global'")
        if "evidence_level" not in existing_columns:
            conn.execute(
                "ALTER TABLE memory_index ADD COLUMN evidence_level TEXT DEFAULT 'unknown'"
            )
        if "currentness" not in existing_columns:
            conn.execute(
                "ALTER TABLE memory_index ADD COLUMN currentness TEXT DEFAULT 'current'"
            )
        if "content_hash" not in existing_columns:
            conn.execute("ALTER TABLE memory_index ADD COLUMN content_hash TEXT")
        if "created_at" not in existing_columns:
            conn.execute("ALTER TABLE memory_index ADD COLUMN created_at TEXT")
        if "updated_at" not in existing_columns:
            conn.execute("ALTER TABLE memory_index ADD COLUMN updated_at TEXT")
        if "status" not in existing_columns:
            conn.execute(
                "ALTER TABLE memory_index ADD COLUMN status TEXT DEFAULT 'active'"
            )
        if "replaced_by" not in existing_columns:
            conn.execute("ALTER TABLE memory_index ADD COLUMN replaced_by TEXT")
        if "version" not in existing_columns:
            conn.execute(
                "ALTER TABLE memory_index ADD COLUMN version INTEGER DEFAULT 1"
            )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS doc_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                title TEXT,
                content TEXT,
                embedding BLOB,
                project TEXT,
                UNIQUE(file_path)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS symbol_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                symbol_name TEXT NOT NULL,
                symbol_type TEXT NOT NULL,
                docstring TEXT,
                embedding BLOB,
                project TEXT,
                UNIQUE(file_path, symbol_name)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                activity TEXT NOT NULL,
                at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                evidence_id INTEGER NOT NULL,
                completed_by_role TEXT NOT NULL,
                completion_summary TEXT NOT NULL,
                completed_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                role TEXT,
                message TEXT NOT NULL,
                engine TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS decision_packets (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                source TEXT NOT NULL,
                decision TEXT NOT NULL,
                context_gist TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dispatch_runs (
                id TEXT PRIMARY KEY,
                decision_id TEXT,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                summary TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS agent_outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dispatch_run_id TEXT NOT NULL,
                role TEXT NOT NULL,
                output TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS events_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                session_id TEXT,
                source TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS capability_boundaries (
                capability TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                reason TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS graph_nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                title TEXT NOT NULL,
                source_ref TEXT,
                content_hash TEXT,
                metadata_json TEXT,
                updated_at TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS graph_edges (
                src TEXT NOT NULL,
                dst TEXT NOT NULL,
                type TEXT NOT NULL,
                evidence TEXT,
                weight REAL NOT NULL DEFAULT 1.0,
                source_ref TEXT,
                authority TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (src, dst, type)
            )"""
        )
        existing_graph_edge_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(graph_edges)").fetchall()
        }
        if "currentness" not in existing_graph_edge_columns:
            conn.execute("ALTER TABLE graph_edges ADD COLUMN currentness TEXT NOT NULL DEFAULT 'unknown'")
        if "confidence" not in existing_graph_edge_columns:
            conn.execute("ALTER TABLE graph_edges ADD COLUMN confidence TEXT NOT NULL DEFAULT 'unknown'")
        if "curated" not in existing_graph_edge_columns:
            conn.execute("ALTER TABLE graph_edges ADD COLUMN curated INTEGER NOT NULL DEFAULT 0")
        if "requires_dual_approval" not in existing_graph_edge_columns:
            conn.execute(
                "ALTER TABLE graph_edges ADD COLUMN requires_dual_approval INTEGER NOT NULL DEFAULT 0"
            )
        existing_graph_node_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(graph_nodes)").fetchall()
        }
        if "curated" not in existing_graph_node_columns:
            conn.execute("ALTER TABLE graph_nodes ADD COLUMN curated INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS answer_quality_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_id INTEGER,
                total_score INTEGER NOT NULL,
                groundedness INTEGER NOT NULL,
                actionability INTEGER NOT NULL,
                role_compliance INTEGER NOT NULL,
                evidence_coverage INTEGER NOT NULL,
                decision_discipline INTEGER NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS answer_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                answer_id TEXT NOT NULL,
                engine TEXT NOT NULL,
                role TEXT NOT NULL,
                decision_packet_id TEXT,
                quality_score_id INTEGER,
                evidence_id INTEGER,
                checkpoint_risk TEXT,
                next_action TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS lifecycle_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                hook_name TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS compact_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                phase TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                taken_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                doc_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        # Deprecated since Stage AC (superseded by graph_nodes/graph_edges
        # curated + requires_dual_approval); kept only so old rows aren't lost.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS context_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS context_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_node_id INTEGER NOT NULL,
                to_node_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS term_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_term TEXT NOT NULL,
                alias TEXT NOT NULL,
                UNIQUE(alias)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS health_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                component TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pending_merges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_files TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS health_events_created ON health_events(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS graph_edges_src ON graph_edges(src, type)")
        conn.execute("CREATE INDEX IF NOT EXISTS graph_edges_dst ON graph_edges(dst, type)")
        conn.execute("CREATE INDEX IF NOT EXISTS graph_nodes_type ON graph_nodes(node_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS answer_quality_session ON answer_quality_scores(session_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS answer_receipts_session ON answer_receipts(session_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS lifecycle_runs_session ON lifecycle_runs(session_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS decisions_session ON decisions(session_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS compact_snapshots_session ON compact_snapshots(session_id, phase, taken_at)")
        for capability, reason in DEFAULT_CAPABILITY_BOUNDARIES.items():
            conn.execute(
                """INSERT OR IGNORE INTO capability_boundaries
                   (capability, enabled, reason, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (capability, 0, reason, _now()),
            )
        conn.commit()


def ensure_session(
    session_id: str, client_session_uuid: str | None = None, db_path: Path = DB_PATH
) -> str:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO sessions
                   (session_id, started_at, status, client_session_uuid)
                   VALUES (?, ?, ?, ?)""",
                (session_id, _now(), "active", client_session_uuid),
            )
        elif client_session_uuid is not None:
            conn.execute(
                "UPDATE sessions SET client_session_uuid = ? WHERE session_id = ?",
                (client_session_uuid, session_id),
            )
        conn.commit()
    return session_id


def find_session_by_client_uuid(
    client_session_uuid: str, db_path: Path = DB_PATH
) -> str | None:
    """The most recent session_id recorded under this client_session_uuid, or
    None if no session was ever tagged with it (Stage AH)."""

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """SELECT session_id FROM sessions WHERE client_session_uuid = ?
               ORDER BY started_at DESC LIMIT 1""",
            (client_session_uuid,),
        ).fetchone()
    return row["session_id"] if row else None


ROLE_PERMISSIONS = {
    "brozzer": {"code": False, "data": False},
    "advisor": {"code": False, "data": True},
    "architect": {"code": False, "data": True},
    "builder": {"code": True, "data": True},
}


def set_role(session_id: str, role: str, db_path: Path = DB_PATH) -> None:
    """Declare a session's role. Raises ValueError for a role outside ROLE_PERMISSIONS."""

    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"unknown role: {role}")
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO roles (session_id, role, declared_at) VALUES (?, ?, ?)",
            (session_id, role, _now()),
        )
        conn.commit()


def get_role(session_id: str, db_path: Path = DB_PATH) -> str:
    """The session's declared role, or 'brozzer' if it never declared one."""

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT role FROM roles WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row["role"] if row else "brozzer"


def role_exists(session_id: str, db_path: Path = DB_PATH) -> bool:
    """Whether this session_id has an explicit row in `roles` (vs. never declared)."""

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM roles WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row is not None


def get_most_recent_role(db_path: Path = DB_PATH) -> str | None:
    """The role with the latest declared_at across ALL sessions, or None if
    `roles` is empty. Used by Stage AF to carry a role across /clear, which
    hands session_start a fresh session_id with no roles row of its own."""

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT role FROM roles ORDER BY declared_at DESC LIMIT 1"
        ).fetchone()
    return row["role"] if row else None


def can_write_code(session_id: str, db_path: Path = DB_PATH) -> bool:
    return ROLE_PERMISSIONS[get_role(session_id, db_path=db_path)]["code"]


def can_write_data(session_id: str, db_path: Path = DB_PATH) -> bool:
    return ROLE_PERMISSIONS[get_role(session_id, db_path=db_path)]["data"]


def write_checkpoint(
    session_id: str,
    *,
    current_task: str | None = None,
    last_decision: str | None = None,
    next_action: str | None = None,
    open_queue: str | None = None,
    risk: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """INSERT INTO checkpoints
               (session_id, created_at, current_task, last_decision, next_action, open_queue, risk)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, _now(), current_task, last_decision, next_action, open_queue, risk),
        )
        conn.commit()


def latest_checkpoint(session_id: str, db_path: Path = DB_PATH) -> dict | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """SELECT * FROM checkpoints WHERE session_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM checkpoints ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
    return dict(row) if row else None


def add_task(
    title: str,
    status: str = "todo",
    *,
    sprint_id: int | None = None,
    parent_task_id: int | None = None,
    session_id: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Create a task. status='done' is refused here — use complete_task().

    sprint_id is optional and defaults to None (backward compatible with
    tasks created before Stage L's projects/sprints hierarchy existed).
    parent_task_id is optional (Stage AP) — set it to make this task a
    subtask; complete_task() refuses to close a parent while any of its
    subtasks are still open. If session_id is given, records a 'created' row
    in task_activity so tasks_touched_by_session() can later find it.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO tasks
                   (title, status, created_at, updated_at, sprint_id, parent_task_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, status, _now(), _now(), sprint_id, parent_task_id),
        )
        task_id = cur.lastrowid
        if session_id is not None:
            conn.execute(
                "INSERT INTO task_activity (task_id, session_id, activity, at) VALUES (?, ?, ?, ?)",
                (task_id, session_id, "created", _now()),
            )
        conn.commit()
        return task_id


def start_task(task_id: int, session_id: str | None = None, db_path: Path = DB_PATH) -> dict:
    """Move a task from 'todo' to 'in_progress' (Stage AY).

    The other half of the todo -> in_progress -> done loop that Stage AX's
    "confirm task N with test: ..." chat command could complete but not
    start. Raises ValueError if the task isn't currently 'todo' (including
    already 'in_progress' or 'done') so a task can't be started twice or
    restarted after completion. If session_id is given, records a 'started'
    row in task_activity.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown task_id: {task_id}")
        if row["status"] != "todo":
            raise ValueError(
                f"cannot start: task status is {row['status']!r}, expected 'todo'"
            )
        conn.execute(
            "UPDATE tasks SET status = 'in_progress', updated_at = ? WHERE id = ?",
            (_now(), task_id),
        )
        if session_id is not None:
            conn.execute(
                "INSERT INTO task_activity (task_id, session_id, activity, at) VALUES (?, ?, ?, ?)",
                (task_id, session_id, "started", _now()),
            )
        conn.commit()
    return {"ok": True, "task_id": task_id, "status": "in_progress"}


def request_review(task_id: int, session_id: str, db_path: Path = DB_PATH) -> None:
    """Move a task from 'in_progress' to 'ready_for_review' (Stage AP).

    An intermediate step, not completion — complete_task() still requires
    its own evidence/summary/subtask checks afterward. Raises ValueError if
    the task isn't currently 'in_progress' (the only state review can be
    requested from).
    """

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown task_id: {task_id}")
        if row["status"] != "in_progress":
            raise ValueError(
                f"cannot request review: task status is {row['status']!r}, expected 'in_progress'"
            )
        conn.execute(
            "UPDATE tasks SET status = 'ready_for_review', updated_at = ? WHERE id = ?",
            (_now(), task_id),
        )
        conn.execute(
            "INSERT INTO task_activity (task_id, session_id, activity, at) VALUES (?, ?, ?, ?)",
            (task_id, session_id, "review_requested", _now()),
        )
        conn.commit()


def list_open_tasks(db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status != 'done' ORDER BY created_at"
        ).fetchall()
    return [dict(row) for row in rows]


def list_tasks(db_path: Path = DB_PATH) -> list[dict]:
    """All tasks regardless of status — open and done alike."""

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
    return [dict(row) for row in rows]


def add_project(name: str, status: str = "active", db_path: Path = DB_PATH) -> int:
    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, status, created_at) VALUES (?, ?, ?)",
            (name, status, _now()),
        )
        conn.commit()
        return cur.lastrowid


def list_projects(db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
    return [dict(row) for row in rows]


def resolve_project(cwd: str, db_path: Path = DB_PATH) -> dict:
    """Match cwd's last folder name against projects.name.

    Stage AG: no fuzzy matching, no default project - an unrecognized cwd
    gets an explicit {"id": None, "name": "unmapped", "status": "unmapped"}
    rather than silently attaching to some existing project (Haroon's
    "say unmapped out loud" principle).
    """

    folder_name = Path(cwd).name
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE name = ?", (folder_name,)
        ).fetchone()
    if row is None:
        return {"id": None, "name": "unmapped", "status": "unmapped"}
    return dict(row)


def add_sprint(
    project_id: int, name: str, status: str = "active", db_path: Path = DB_PATH
) -> int:
    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO sprints (project_id, name, status, created_at) VALUES (?, ?, ?, ?)",
            (project_id, name, status, _now()),
        )
        conn.commit()
        return cur.lastrowid


def list_sprints(project_id: int | None = None, db_path: Path = DB_PATH) -> list[dict]:
    """All sprints, or only project_id's if given."""

    with contextlib.closing(_connect(db_path)) as conn:
        if project_id is None:
            rows = conn.execute("SELECT * FROM sprints ORDER BY created_at").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sprints WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
    return [dict(row) for row in rows]


def project_status(project_id: int, db_path: Path = DB_PATH) -> dict:
    """Summarize one project by combining list_projects(), list_sprints(), and
    list_open_tasks()/list_tasks(): its name, sprint count, and open/done task
    counts across all its sprints. Raises ValueError for an unknown project_id.
    """

    project = next(
        (p for p in list_projects(db_path=db_path) if p["id"] == project_id), None
    )
    if project is None:
        raise ValueError(f"unknown project_id: {project_id}")
    sprint_ids = {sprint["id"] for sprint in list_sprints(project_id, db_path=db_path)}
    open_task_count = sum(
        1 for task in list_open_tasks(db_path=db_path) if task["sprint_id"] in sprint_ids
    )
    done_task_count = sum(
        1 for task in list_tasks(db_path=db_path)
        if task["status"] == "done" and task["sprint_id"] in sprint_ids
    )
    return {
        "project_id": project_id,
        "name": project["name"],
        "sprint_count": len(sprint_ids),
        "open_task_count": open_task_count,
        "done_task_count": done_task_count,
        "has_northstar": get_document(project_id, "northstar", db_path=db_path) is not None,
    }


def add_document(
    project_id: int, doc_type: str, title: str, content: str, db_path: Path = DB_PATH
) -> int:
    """Attach a document (e.g. northstar/plan/note) to a project.

    Raises ValueError if project_id doesn't exist.
    """

    if not any(p["id"] == project_id for p in list_projects(db_path=db_path)):
        raise ValueError(f"unknown project_id: {project_id}")
    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO documents
               (project_id, doc_type, title, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, doc_type, title, content, _now(), _now()),
        )
        conn.commit()
        return cur.lastrowid


def get_document(project_id: int, doc_type: str, db_path: Path = DB_PATH) -> dict | None:
    """The most recently updated document of doc_type for a project, or None."""

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """SELECT * FROM documents WHERE project_id = ? AND doc_type = ?
               ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (project_id, doc_type),
        ).fetchone()
    return dict(row) if row else None


def update_document(document_id: int, content: str, db_path: Path = DB_PATH) -> None:
    """Update an existing document's content in place (not a new row)."""

    with contextlib.closing(_connect(db_path)) as conn:
        updated = conn.execute(
            "UPDATE documents SET content = ?, updated_at = ? WHERE id = ?",
            (content, _now(), document_id),
        )
        conn.commit()
        if updated.rowcount == 0:
            raise ValueError(f"unknown document_id: {document_id}")


def list_documents(project_id: int, db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_sessions(db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY started_at").fetchall()
    return [dict(row) for row in rows]


def list_task_activity(db_path: Path = DB_PATH) -> list[dict]:
    """Every session-task touch — the raw edges behind tasks_touched_by_session()."""

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM task_activity ORDER BY at").fetchall()
    return [dict(row) for row in rows]


def record_turn(
    session_id: str,
    speaker: str,
    message: str,
    *,
    role: str | None = None,
    engine: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Append one user/assistant turn to MC's active-turn trace."""

    ensure_session(session_id, db_path=db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO turns (session_id, speaker, role, message, engine, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, speaker, role, message, engine, _now()),
        )
        conn.commit()
        return cur.lastrowid


def list_turns(
    session_id: str | None = None, *, limit: int = 20, db_path: Path = DB_PATH
) -> list[dict]:
    """Recent turns, optionally for one session, oldest-first within the limit."""

    with contextlib.closing(_connect(db_path)) as conn:
        if session_id is None:
            rows = conn.execute(
                "SELECT * FROM turns ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM turns WHERE session_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
    return [dict(row) for row in reversed(rows)]


def record_event(
    event_type: str,
    *,
    session_id: str | None = None,
    source: str = "system",
    details: dict | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Append one operational event; details are JSON so callers keep structure."""

    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO events_log
               (event_type, session_id, source, details_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                event_type,
                session_id,
                source,
                json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def list_events(limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM events_log ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def log_health_event(
    component: str,
    event: str,
    detail: str | None = None,
    session_id: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """Record one failure-visibility row (Stage AU, Haroon's team doc item 8).

    Called from EXISTING fail-soft points (write_checkpoint callers,
    ask_ollama, _get_embedding_model, index_memory_files) that already
    swallow their own errors — this only makes those swallowed errors
    visible via get_health_state(), it does not change whether they're
    swallowed. component examples: "checkpoint", "memory_index",
    "embedding_model", "ollama", "db".

    Fail-soft itself, on purpose: logging must never be the thing that
    crashes a caller that was already handling its own failure gracefully.
    Any error here (e.g. a locked/corrupt db) is swallowed silently.
    """

    try:
        with contextlib.closing(_connect(db_path)) as conn:
            conn.execute(
                """INSERT INTO health_events (session_id, component, event, detail, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, component, event, detail, _now()),
            )
            conn.commit()
    except Exception:
        pass


_HEALTH_BLOCKED_THRESHOLD = 3


def get_health_state(session_id: str | None = None, db_path: Path = DB_PATH) -> str:
    """Roll up the last hour of health_events into one of three states.

    A simple threshold system covering 3 of Haroon's 6 states — "recovery
    required"/"validation failed"/"sync pending" don't apply here (no
    server, no sync, see decisions.md 2026-07-29). Every logged component
    (checkpoint, memory_index, embedding_model, ollama, db) counts the same:

    - no events in the last hour -> "healthy"
    - 1 to _HEALTH_BLOCKED_THRESHOLD events -> "degraded" (something failed
      and is worth a look — even a single checkpoint/db write failure)
    - more than _HEALTH_BLOCKED_THRESHOLD events -> "blocked" (failing
      repeatedly, not just once)

    session_id scopes to that session's events when given; None (the
    default) looks at events across all sessions, matching recent_decisions()'s
    project-wide default.
    """

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with contextlib.closing(_connect(db_path)) as conn:
        if session_id is None:
            count = conn.execute(
                "SELECT COUNT(*) FROM health_events WHERE created_at >= ?", (cutoff,)
            ).fetchone()[0]
        else:
            count = conn.execute(
                "SELECT COUNT(*) FROM health_events WHERE created_at >= ? AND session_id = ?",
                (cutoff, session_id),
            ).fetchone()[0]

    if count == 0:
        return "healthy"
    if count > _HEALTH_BLOCKED_THRESHOLD:
        return "blocked"
    return "degraded"


def list_health_events(
    limit: int = 20, session_id: str | None = None, db_path: Path = DB_PATH
) -> list[dict]:
    """Most recent health_events rows (Stage AV) — detail behind get_health_state()'s rollup."""

    with contextlib.closing(_connect(db_path)) as conn:
        if session_id is None:
            rows = conn.execute(
                "SELECT * FROM health_events ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM health_events WHERE session_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
    return [dict(row) for row in reversed(rows)]


def create_decision_packet(
    session_id: str,
    decision: str,
    *,
    source: str = "chat",
    context_gist: str | None = None,
    status: str = "new",
    db_path: Path = DB_PATH,
) -> str:
    """Local equivalent of Haroon's durable decision packet, before HOSNI dispatch."""

    ensure_session(session_id, db_path=db_path)
    packet_id = uuid.uuid4().hex
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """INSERT INTO decision_packets
               (id, session_id, source, decision, context_gist, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (packet_id, session_id, source, decision, context_gist, status, _now()),
        )
        conn.commit()
    record_event(
        "decision_packet_created",
        session_id=session_id,
        source=source,
        details={"decision_id": packet_id, "status": status},
        db_path=db_path,
    )
    return packet_id


def list_decision_packets(limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM decision_packets ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def create_dispatch_run(
    session_id: str,
    *,
    decision_id: str | None = None,
    status: str = "queued",
    summary: str | None = None,
    db_path: Path = DB_PATH,
) -> str:
    """Track a local Brozzer -> Architect/Builder dispatch simulation."""

    run_id = uuid.uuid4().hex
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """INSERT INTO dispatch_runs
               (id, decision_id, session_id, status, started_at, completed_at, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, decision_id, session_id, status, _now(), None, summary),
        )
        conn.commit()
    record_event(
        "dispatch_run_created",
        session_id=session_id,
        source="mc",
        details={"dispatch_run_id": run_id, "decision_id": decision_id, "status": status},
        db_path=db_path,
    )
    return run_id


def complete_dispatch_run(
    run_id: str,
    *,
    status: str = "completed",
    summary: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """Close a local dispatch run so the Advisor/Architect/Builder path has proof."""

    with contextlib.closing(_connect(db_path)) as conn:
        updated = conn.execute(
            """UPDATE dispatch_runs
               SET status = ?, completed_at = ?, summary = COALESCE(?, summary)
               WHERE id = ?""",
            (status, _now(), summary, run_id),
        )
        conn.commit()
        if updated.rowcount == 0:
            raise ValueError(f"unknown dispatch run: {run_id}")


def list_dispatch_runs(limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM dispatch_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def record_agent_output(
    dispatch_run_id: str,
    role: str,
    output: str,
    *,
    db_path: Path = DB_PATH,
) -> int:
    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO agent_outputs (dispatch_run_id, role, output, created_at)
               VALUES (?, ?, ?, ?)""",
            (dispatch_run_id, role, output, _now()),
        )
        conn.commit()
        return cur.lastrowid


def list_agent_outputs(
    dispatch_run_id: str | None = None, *, limit: int = 20, db_path: Path = DB_PATH
) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        if dispatch_run_id is None:
            rows = conn.execute(
                "SELECT * FROM agent_outputs ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM agent_outputs WHERE dispatch_run_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (dispatch_run_id, limit),
            ).fetchall()
    return [dict(row) for row in reversed(rows)]


def record_evidence(
    kind: str,
    label: str,
    status: str,
    *,
    details: dict | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Store a receipt/proof row such as tests, demo checks, or health checks."""

    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO evidence (kind, label, status, details_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                kind,
                label,
                status,
                json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def list_evidence(limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM evidence ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def assess_answer_quality(
    message: str,
    answer: str,
    *,
    context: dict | None = None,
    engine: str | None = None,
    decision_packet_id: str | None = None,
) -> dict:
    """Deterministic answer rubric: BBB-Lite's local proof, not an LLM judge."""

    context = context or {}
    message_text = str(message or "")
    answer_text = str(answer or "")
    lower_answer = answer_text.lower()
    lower_message = message_text.lower()
    context_has_memory = bool(
        context.get("keyword_memory")
        or context.get("semantic_memory")
        or context.get("graph_memory")
        or context.get("checkpoint")
        or context.get("open_tasks")
    )
    answer_sentences = re.split(r"[\n.;!?]+", lower_answer)
    unsupported_claim = any(
        any(term in sentence for term in UNSUPPORTED_CLAIM_TERMS)
        and any(action in sentence for action in UNSUPPORTED_ACTION_TERMS)
        for sentence in answer_sentences
    )
    mentions_context = any(
        term in lower_answer
        for term in (
            "mission control",
            "mc",
            "memory",
            "graph",
            "checkpoint",
            "role",
            "kanit",
            "kanıt",
            "context",
        )
    )
    has_next_action = bool(
        re.search(r"\b(next|sonraki|adim|adım|plan|risk|1\.|2\.|3\.)\b", lower_answer)
    )
    names_evidence = any(
        term in lower_answer
        for term in ("evidence", "kanit", "kanıt", "test", "checkpoint", "memory", "graph")
    )
    decision_requested = "decision" in lower_message or "karar" in lower_message

    groundedness = 25 if mentions_context else (18 if context_has_memory else 12)
    actionability = 20 if has_next_action else 8
    role_compliance = 0 if unsupported_claim else 20
    evidence_coverage = 20 if names_evidence else (12 if context_has_memory else 6)
    decision_discipline = 15 if decision_packet_id or not decision_requested else 5
    total = groundedness + actionability + role_compliance + evidence_coverage + decision_discipline
    status = "passed" if total >= 70 and not unsupported_claim else "needs_review"
    return {
        "total_score": total,
        "groundedness": groundedness,
        "actionability": actionability,
        "role_compliance": role_compliance,
        "evidence_coverage": evidence_coverage,
        "decision_discipline": decision_discipline,
        "status": status,
        "unsupported_claim": unsupported_claim,
        "engine": engine,
        "reason": (
            "answer is grounded enough for local demo"
            if status == "passed"
            else "answer needs review before being treated as BBB-quality output"
        ),
    }


def record_answer_quality(
    session_id: str,
    score: dict,
    *,
    turn_id: int | None = None,
    db_path: Path = DB_PATH,
) -> int:
    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO answer_quality_scores
               (session_id, turn_id, total_score, groundedness, actionability,
                role_compliance, evidence_coverage, decision_discipline, status,
                details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                turn_id,
                int(score["total_score"]),
                int(score["groundedness"]),
                int(score["actionability"]),
                int(score["role_compliance"]),
                int(score["evidence_coverage"]),
                int(score["decision_discipline"]),
                str(score["status"]),
                json.dumps(score, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def list_answer_quality_scores(
    session_id: str | None = None, *, limit: int = 20, db_path: Path = DB_PATH
) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        if session_id is None:
            rows = conn.execute(
                "SELECT * FROM answer_quality_scores ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM answer_quality_scores WHERE session_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
    return [dict(row) for row in reversed(rows)]


def record_answer_receipt(
    session_id: str,
    answer_id: str,
    *,
    engine: str,
    role: str,
    decision_packet_id: str | None = None,
    quality_score_id: int | None = None,
    evidence_id: int | None = None,
    checkpoint_risk: str | None = None,
    next_action: str | None = None,
    details: dict | None = None,
    db_path: Path = DB_PATH,
) -> int:
    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO answer_receipts
               (session_id, answer_id, engine, role, decision_packet_id,
                quality_score_id, evidence_id, checkpoint_risk, next_action,
                details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                answer_id,
                engine,
                role,
                decision_packet_id,
                quality_score_id,
                evidence_id,
                checkpoint_risk,
                next_action,
                json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def list_answer_receipts(
    session_id: str | None = None, *, limit: int = 20, db_path: Path = DB_PATH
) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        if session_id is None:
            rows = conn.execute(
                "SELECT * FROM answer_receipts ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM answer_receipts WHERE session_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
    return [dict(row) for row in reversed(rows)]


def record_lifecycle_run(
    hook_name: str,
    *,
    session_id: str | None = None,
    source: str = "hook",
    status: str = "ok",
    details: dict | None = None,
    db_path: Path = DB_PATH,
) -> int:
    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO lifecycle_runs
               (session_id, hook_name, source, status, details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                hook_name,
                source,
                status,
                json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def list_lifecycle_runs(
    session_id: str | None = None, *, limit: int = 20, db_path: Path = DB_PATH
) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        if session_id is None:
            rows = conn.execute(
                "SELECT * FROM lifecycle_runs ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM lifecycle_runs WHERE session_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
    return [dict(row) for row in reversed(rows)]


def _append_decision_md(text: str, path: Path = DECISIONS_MD_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n- {text}\n")


def add_decision(
    text: str,
    session_id: str | None = None,
    db_path: Path = DB_PATH,
    md_path: Path = DECISIONS_MD_PATH,
) -> int:
    """Record a decision both as a queryable row and as a decisions.md bullet.

    The table gives stable IDs to query/diff against (see compact_snapshot());
    decisions.md stays the human-readable log. Neither write is best-effort —
    if either fails, the caller sees the exception. md_path is parameterized
    like db_path so tests can redirect it instead of touching the real file.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO decisions (session_id, text, created_at) VALUES (?, ?, ?)",
            (session_id, text, _now()),
        )
        conn.commit()
        decision_id = cur.lastrowid
    _append_decision_md(text, md_path)
    return decision_id


def list_decisions(session_id: str | None = None, *, limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        if session_id is None:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM decisions WHERE session_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
    return [dict(row) for row in reversed(rows)]


def recent_decisions(limit: int = 2, db_path: Path = DB_PATH) -> list[str]:
    """The most recent N decision texts, newest first, across all sessions.

    Project-wide like list_decisions(session_id=None) — decisions carry a
    session_id column but aren't scoped to it (see decisions.md, 2026-07-24).
    Used by session_start.py's recovery package (Stage AA).
    """

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT text FROM decisions ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row["text"] for row in rows]


def recent_decision_ids(limit: int = 2, db_path: Path = DB_PATH) -> list[int]:
    """The most recent N decision row ids, newest first, across all sessions.

    Same ordering/scope as recent_decisions(), but ids instead of text -
    for callers (e.g. clear_recovery_verified()) that want a stable,
    compact reference rather than the full decision text.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id FROM decisions ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row["id"] for row in rows]


_DECISION_CONFLICT_WORD_OVERLAP_THRESHOLD = 0.5


def detect_decision_conflicts(db_path: Path = DB_PATH) -> list[dict]:
    """Find pairs of decisions that may disagree (Stage AT, Haroon's team doc item 7).

    Detection only — nothing here resolves or deletes anything, it just
    surfaces candidate pairs for a human to look at. Two decisions are
    compared only if they're NOT known to come from the same session: a
    pair is skipped when both rows carry the same non-NULL session_id
    (evolving your own earlier call in one session isn't a "conflict"); if
    either session_id is NULL (the common case — most decisions here are
    logged without one, see decisions.md 2026-07-24), the pair IS compared,
    since "same session" can't be ruled out either way and staying silent
    would defeat the point of this check.

    "High similarity" is Jaccard overlap of _significant_words(text) sets
    (shared with search_memory's keyword layer) at or above
    _DECISION_CONFLICT_WORD_OVERLAP_THRESHOLD — a simple word-overlap
    heuristic, not semantic search.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM decisions").fetchall()]

    word_sets = [set(_significant_words(row["text"])) for row in rows]
    conflicts: list[dict] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if a["session_id"] is not None and a["session_id"] == b["session_id"]:
                continue
            words_a, words_b = word_sets[i], word_sets[j]
            union = words_a | words_b
            if not union:
                continue
            overlap = len(words_a & words_b) / len(union)
            if overlap >= _DECISION_CONFLICT_WORD_OVERLAP_THRESHOLD:
                conflicts.append(
                    {
                        "a": a,
                        "b": b,
                        "reason": (
                            f"word overlap {overlap:.2f} across different sessions "
                            f"(session_id {a['session_id']!r} vs {b['session_id']!r})"
                        ),
                    }
                )
    return conflicts


def upsert_capability_boundary(
    capability: str,
    enabled: bool,
    reason: str,
    *,
    db_path: Path = DB_PATH,
) -> None:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """INSERT INTO capability_boundaries
               (capability, enabled, reason, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(capability) DO UPDATE SET
                   enabled = excluded.enabled,
                   reason = excluded.reason,
                   updated_at = excluded.updated_at""",
            (capability, int(enabled), reason, _now()),
        )
        conn.commit()


def list_capability_boundaries(db_path: Path = DB_PATH) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM capability_boundaries ORDER BY capability"
        ).fetchall()
    return [
        {**dict(row), "enabled": bool(row["enabled"])}
        for row in rows
    ]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug or "unknown"


def _graph_node_id(node_type: str, ref: str) -> str:
    return f"{node_type}:{_slug(ref)}"


def _authority_class_for_edge(edge_type: str, authority: str | None = None) -> str:
    if authority:
        return authority
    if edge_type in GRAPH_EDGE_TYPES:
        return "trusted-typed"
    if edge_type == "links-to":
        return "low-trust-navigation"
    if edge_type == "mentions":
        return "auto-keyword"
    return "unclassified"


def _upsert_graph_node(
    conn: sqlite3.Connection,
    node_id: str,
    node_type: str,
    title: str,
    *,
    source_ref: str | None = None,
    content_hash: str | None = None,
    metadata: dict | None = None,
) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO graph_nodes
           (node_id, node_type, title, source_ref, content_hash, metadata_json, updated_at, indexed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(node_id) DO UPDATE SET
               node_type = excluded.node_type,
               title = excluded.title,
               source_ref = excluded.source_ref,
               content_hash = excluded.content_hash,
               metadata_json = excluded.metadata_json,
               updated_at = excluded.updated_at""",
        (
            node_id,
            node_type,
            title,
            source_ref,
            content_hash,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )


def _upsert_graph_edge(
    conn: sqlite3.Connection,
    src: str,
    dst: str,
    edge_type: str,
    *,
    evidence: str | None = None,
    weight: float = 1.0,
    source_ref: str | None = None,
    authority: str | None = None,
    currentness: str = "unknown",
    confidence: str = "unknown",
    curated: bool = False,
    requires_dual_approval: bool = False,
) -> None:
    src, dst, edge_type = src.strip(), dst.strip(), edge_type.strip()
    if not src or not dst or not edge_type:
        raise ValueError("graph edge needs non-empty src, dst, and type")
    authority_class = _authority_class_for_edge(edge_type, authority)
    conn.execute(
        """INSERT INTO graph_edges
           (src, dst, type, evidence, weight, source_ref, authority, updated_at,
            currentness, confidence, curated, requires_dual_approval)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(src, dst, type) DO UPDATE SET
               evidence = excluded.evidence,
               weight = excluded.weight,
               source_ref = excluded.source_ref,
               authority = excluded.authority,
               updated_at = excluded.updated_at,
               currentness = excluded.currentness,
               confidence = excluded.confidence,
               curated = excluded.curated,
               requires_dual_approval = excluded.requires_dual_approval""",
        (
            src,
            dst,
            edge_type,
            evidence,
            float(weight),
            source_ref,
            authority_class,
            _now(),
            currentness,
            confidence,
            int(curated),
            int(requires_dual_approval),
        ),
    )


def _wiki_links(text: str) -> list[str]:
    links = []
    for raw in re.findall(r"\[\[([^\]]+)\]\]", text):
        label = raw.split("|", 1)[0].strip()
        if label:
            links.append(label)
    return list(dict.fromkeys(links))


def _upsert_memory_graph(
    conn: sqlite3.Connection,
    source_file: str,
    title: str,
    body: str,
    keywords: str,
) -> None:
    doc_id = _graph_node_id("doc", source_file)
    content_hash = _sha256_text(f"{title}\n{body}")
    _upsert_graph_node(
        conn,
        doc_id,
        "doc",
        title,
        source_ref=source_file,
        content_hash=content_hash,
        metadata={"source": "memory"},
    )
    conn.execute(
        "DELETE FROM graph_edges WHERE source_ref = ? AND authority IN ('auto-keyword', 'low-trust-navigation')",
        (source_file,),
    )
    for keyword in [part.strip() for part in keywords.split(",") if part.strip()]:
        concept_id = _graph_node_id("concept", keyword)
        _upsert_graph_node(
            conn,
            concept_id,
            "concept",
            keyword,
            source_ref=f"keyword:{keyword}",
            metadata={"source": "memory-keyword"},
        )
        _upsert_graph_edge(
            conn,
            doc_id,
            concept_id,
            "mentions",
            evidence=f"keyword extracted from {source_file}",
            weight=0.4,
            source_ref=source_file,
            authority="auto-keyword",
            currentness="unknown",
            confidence="inferred",
            curated=False,
        )
    for label in _wiki_links(body):
        target_id = _graph_node_id("concept", label)
        _upsert_graph_node(
            conn,
            target_id,
            "concept",
            label,
            source_ref=f"wikilink:{label}",
            metadata={"source": "memory-wikilink"},
        )
        _upsert_graph_edge(
            conn,
            doc_id,
            target_id,
            "links-to",
            evidence=f"wikilink in {source_file}",
            weight=0.6,
            source_ref=source_file,
            authority="low-trust-navigation",
            currentness="unknown",
            confidence="inferred",
            curated=False,
        )


def load_graph_edges(
    edge_path: Path | None = None, *, db_path: Path = DB_PATH
) -> dict:
    """Load explicit graph/edges.jsonl edges, mirroring Haroon's synced edge source."""

    init_db(db_path)
    path = edge_path or (Path(__file__).resolve().parent / "graph" / "edges.jsonl")
    if not path.exists():
        return {"loaded": 0, "skipped": 0, "unknown_types": [], "source_missing": True}
    loaded = skipped = 0
    unknown: set[str] = set()
    with contextlib.closing(_connect(db_path)) as conn:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                edge = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            src = str(edge.get("src") or "").strip()
            dst = str(edge.get("dst") or "").strip()
            edge_type = str(edge.get("type") or "").strip()
            if not src or not dst or not edge_type:
                skipped += 1
                continue
            if edge_type not in GRAPH_EDGE_TYPES:
                unknown.add(edge_type)
            _upsert_graph_node(
                conn,
                src,
                "component",
                src,
                source_ref=edge.get("source_ref") or str(path.name),
                metadata={"source": "explicit-edge"},
            )
            _upsert_graph_node(
                conn,
                dst,
                "component",
                dst,
                source_ref=edge.get("source_ref") or str(path.name),
                metadata={"source": "explicit-edge"},
            )
            _upsert_graph_edge(
                conn,
                src,
                dst,
                edge_type,
                evidence=edge.get("evidence"),
                weight=float(edge.get("weight", 1.0)),
                source_ref=edge.get("source_ref") or str(path.name),
                authority="trusted-typed" if edge_type in GRAPH_EDGE_TYPES else None,
                currentness=str(edge.get("currentness") or "current"),
                confidence=str(edge.get("confidence") or "verified"),
                curated=True,
            )
            loaded += 1
        conn.commit()
    return {"loaded": loaded, "skipped": skipped, "unknown_types": sorted(unknown)}


def list_graph_nodes(
    node_type: str | None = None, *, limit: int = 100, db_path: Path = DB_PATH
) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        if node_type is None:
            rows = conn.execute(
                "SELECT * FROM graph_nodes ORDER BY node_type, title LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM graph_nodes WHERE node_type = ? ORDER BY title LIMIT ?",
                (node_type, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def list_graph_edges(
    edge_type: str | None = None, *, limit: int = 100, db_path: Path = DB_PATH
) -> list[dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        if edge_type is None:
            rows = conn.execute(
                "SELECT * FROM graph_edges ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM graph_edges WHERE type = ? ORDER BY updated_at DESC LIMIT ?",
                (edge_type, limit),
            ).fetchall()
    return [dict(row) for row in reversed(rows)]


def curate_graph_edge(
    src: str,
    dst: str,
    edge_type: str,
    *,
    currentness: str = "current",
    confidence: str = "verified",
    reason: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    with contextlib.closing(_connect(db_path)) as conn:
        updated = conn.execute(
            """UPDATE graph_edges
               SET currentness = ?, confidence = ?, curated = 1, updated_at = ?
               WHERE src = ? AND dst = ? AND type = ?""",
            (currentness, confidence, _now(), src, dst, edge_type),
        )
        conn.commit()
        if updated.rowcount == 0:
            raise ValueError(f"unknown graph edge: {src} -> {dst} ({edge_type})")
    record_event(
        "graph_edge_curated",
        source="mc",
        details={
            "src": src,
            "dst": dst,
            "type": edge_type,
            "currentness": currentness,
            "confidence": confidence,
            "reason": reason,
        },
        db_path=db_path,
    )


def graph_stats(db_path: Path = DB_PATH) -> dict:
    with contextlib.closing(_connect(db_path)) as conn:
        node_count = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        curated_edges = conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE curated = 1"
        ).fetchone()[0]
        by_type = {
            row["type"]: row["count"]
            for row in conn.execute(
                "SELECT type, COUNT(*) AS count FROM graph_edges GROUP BY type ORDER BY count DESC"
            ).fetchall()
        }
        by_authority = {
            row["authority"]: row["count"]
            for row in conn.execute(
                "SELECT authority, COUNT(*) AS count FROM graph_edges GROUP BY authority ORDER BY count DESC"
            ).fetchall()
        }
        by_currentness = {
            row["currentness"]: row["count"]
            for row in conn.execute(
                "SELECT currentness, COUNT(*) AS count FROM graph_edges GROUP BY currentness ORDER BY count DESC"
            ).fetchall()
        }
        by_confidence = {
            row["confidence"]: row["count"]
            for row in conn.execute(
                "SELECT confidence, COUNT(*) AS count FROM graph_edges GROUP BY confidence ORDER BY count DESC"
            ).fetchall()
        }
    return {
        "nodes": node_count,
        "edges": edge_count,
        "curated_edges": curated_edges,
        "by_type": by_type,
        "by_authority": by_authority,
        "by_currentness": by_currentness,
        "by_confidence": by_confidence,
    }


def traverse_graph(
    start: str,
    *,
    edge_types: Iterable[str] | None = None,
    direction: str = "out",
    hops: int = GRAPH_MAX_HOPS,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Breadth-first typed-edge traversal, capped like Haroon's graph v2."""

    hops = max(1, min(int(hops), GRAPH_MAX_HOPS))
    selected_types = list(edge_types or [])
    result: list[dict] = []
    frontier: list[tuple[str, int]] = [(start, 0)]
    seen: set[tuple[str, str, str, int]] = set()
    with contextlib.closing(_connect(db_path)) as conn:
        while frontier:
            node, depth = frontier.pop(0)
            if depth >= hops:
                continue
            col, want = ("src", "dst") if direction == "out" else ("dst", "src")
            sql = (
                f"SELECT {want} AS node, type, evidence, weight, source_ref, authority, "
                f"updated_at, currentness, confidence, curated "
                f"FROM graph_edges WHERE {col} = ?"
            )
            params: list = [node]
            if selected_types:
                sql += " AND type IN (%s)" % ",".join("?" * len(selected_types))
                params.extend(selected_types)
            for row in conn.execute(sql, params).fetchall():
                if row["node"] == start:
                    continue
                key = (row["node"], node, row["type"], depth + 1)
                if key in seen:
                    continue
                seen.add(key)
                item = dict(row)
                item["hop"] = depth + 1
                item["via"] = node
                result.append(item)
                frontier.append((row["node"], depth + 1))
    result.sort(key=lambda row: (-(row["weight"] or 0.0), row["hop"], row["node"]))
    return result


def graph_depends_on(node: str, *, hops: int = GRAPH_MAX_HOPS, db_path: Path = DB_PATH) -> list[dict]:
    return traverse_graph(node, edge_types=["depends-on"], direction="out", hops=hops, db_path=db_path)


def graph_impact(node: str, *, hops: int = GRAPH_MAX_HOPS, db_path: Path = DB_PATH) -> list[dict]:
    return traverse_graph(node, edge_types=["depends-on"], direction="in", hops=hops, db_path=db_path)


def graph_memory_search(
    query: str,
    *,
    limit: int = 3,
    hops: int = 1,
    project: str | None = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Recall memory hits plus their graph neighborhood.

    project is an optional filter (Stage AM), passed through to
    search_memory()/semantic_search() unchanged - None searches everything.
    """

    hits = search_memory(query, limit=limit, project=project, db_path=db_path)
    if len(hits) < limit:
        with contextlib.suppress(Exception):
            for hit in semantic_search(query, limit=limit, project=project, db_path=db_path):
                if not any(existing["source_file"] == hit["source_file"] for existing in hits):
                    hits.append(hit)
                if len(hits) >= limit:
                    break
    enriched = []
    for hit in hits[:limit]:
        node_id = _graph_node_id("doc", hit["source_file"])
        item = dict(hit)
        item["node_id"] = node_id
        item["related"] = traverse_graph(node_id, hops=hops, db_path=db_path)
        enriched.append(item)
    return enriched


def pick_next_action(db_path: Path = DB_PATH) -> str | None:
    """"Next: <title>" for the oldest open task, or None if there isn't one."""

    open_tasks = list_open_tasks(db_path)
    if not open_tasks:
        return None
    return f"Next: {open_tasks[0]['title']}"


def session_started_at(session_id: str, db_path: Path = DB_PATH) -> str | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT started_at FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row["started_at"] if row else None


def has_completed_task_since(session_id: str, db_path: Path = DB_PATH) -> bool:
    """True if some task reached status='done' at or after this session started.

    tasks carries no session_id column, so this is a time-window proxy: any
    completion done via complete_task() whose updated_at is on/after this
    session's started_at counts as evidence the session actually completed one.
    """

    started_at = session_started_at(session_id, db_path)
    if started_at is None:
        return False
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM tasks WHERE status = 'done' AND updated_at >= ? LIMIT 1",
            (started_at,),
        ).fetchone()
    return row is not None


def sweep_stale_sessions(hours: int = 24, db_path: Path = DB_PATH) -> int:
    """Mark 'active' sessions older than `hours` as 'stale'; return how many changed."""

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with contextlib.closing(_connect(db_path)) as conn:
        updated = conn.execute(
            "UPDATE sessions SET status = 'stale' WHERE status = 'active' AND started_at < ?",
            (cutoff,),
        )
        conn.commit()
        return updated.rowcount


def check_safepoint_stale(hours: int = 2, db_path: Path = DB_PATH) -> list[str]:
    """Flag active sessions with no session-scoped checkpoint, or none recent enough.

    Queries checkpoints directly rather than through latest_checkpoint(): that
    helper falls back to the most recent checkpoint from ANY session when the
    given session has none of its own (a resume-UX convenience), which would
    hide a session that never checkpointed as long as some other session did
    recently. This guard needs the per-session truth, not that fallback.
    """

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    findings: list[str] = []
    with contextlib.closing(_connect(db_path)) as conn:
        active_sessions = conn.execute(
            "SELECT session_id FROM sessions WHERE status = 'active'"
        ).fetchall()
        for row in active_sessions:
            session_id = row["session_id"]
            checkpoint = conn.execute(
                "SELECT created_at FROM checkpoints WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if checkpoint is None or checkpoint["created_at"] < cutoff:
                findings.append(
                    f"session {session_id}: no recent checkpoint (possible safepoint-stale)"
                )
    return findings


def check_floor(db_path: Path = DB_PATH) -> list[str]:
    """A few deterministic checks over the DB; return findings (empty list if none).

    Mirrors haroon-bbb-sync's bbb-hourly-tripwire.py floor checks, trimmed to
    what this project's schema has: stale sessions, memory_index size,
    long-open tasks, and the safepoint-stale guard (see decisions.md,
    2026-07-24, for why the other three of Haroon's four checks were left out).
    """

    findings: list[str] = []
    with contextlib.closing(_connect(db_path)) as conn:
        stale_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status = 'stale'"
        ).fetchone()[0]
        if stale_count > 3:
            findings.append(f"{stale_count} sessions marked stale")

        memory_index_count = conn.execute("SELECT COUNT(*) FROM memory_index").fetchone()[0]
        if memory_index_count > 500:
            findings.append(f"memory_index has {memory_index_count} rows, consider review")

        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        old_open_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'todo' AND created_at < ?",
            (week_ago,),
        ).fetchone()[0]
        if old_open_count > 3:
            findings.append(f"{old_open_count} tasks open for 7+ days")

    findings.extend(check_safepoint_stale(db_path=db_path))
    return findings


def mark_session_completed(session_id: str, db_path: Path = DB_PATH) -> None:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            "UPDATE sessions SET status = 'completed' WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()


def mark_session_degraded(session_id: str, db_path: Path = DB_PATH) -> None:
    """Flag a session whose compact recovery could not be verified intact.

    Deliberately the opposite of this project's usual fail-soft default: a
    session that ends up here must stay visibly flagged, not silently
    continue as if nothing happened (see decisions.md, 2026-07-27).
    """

    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            "UPDATE sessions SET status = 'degraded' WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()


def tasks_touched_by_session(session_id: str, db_path: Path = DB_PATH) -> list[dict]:
    """Distinct tasks this session touched (via add_task/complete_task's session_id).

    Each row also carries completed_by_session: whether this same session is the
    one that recorded the 'completed' activity for that task — the precise,
    per-task replacement for the old has_completed_task_since() time-window proxy.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT t.id AS task_id, t.title AS title, t.status AS status,
                      MAX(CASE WHEN a.activity = 'completed' THEN 1 ELSE 0 END)
                          AS completed_by_session
               FROM task_activity a
               JOIN tasks t ON t.id = a.task_id
               WHERE a.session_id = ?
               GROUP BY t.id, t.title, t.status
               ORDER BY t.id""",
            (session_id,),
        ).fetchall()
    return [
        {**dict(row), "completed_by_session": bool(row["completed_by_session"])}
        for row in rows
    ]


def _rules_denied_count(session_id: str, log_path: Path = PRETOOLUSE_LOG_PATH) -> int:
    """Count this session's "no code-write permission" denials in pretooluse.log.

    Fail-soft on purpose: the log is a plain-text line format, not a structured
    record, so a missing file, unreadable file, or a session_id that never shows
    up just means zero denials found, not an error (see decisions.md, 2026-07-29,
    Stage Y).
    """

    if not log_path.exists():
        return 0
    pattern = re.compile(
        r"denied session=" + re.escape(session_id) + r":.*no code-write permission"
    )
    try:
        count = 0
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                if pattern.search(line):
                    count += 1
        return count
    except OSError:
        return 0


def session_score(
    session_id: str, db_path: Path = DB_PATH, log_path: Path = PRETOOLUSE_LOG_PATH
) -> dict:
    """3-axis session score: exec/context/rules, a small counterpart to Haroon's split.

    - exec_score: the original single-axis metric (task completion rate * 100,
      100 if nothing was touched).
    - context_score: crude proxy for context-budget tracking, which this project
      doesn't have — 100 if the session wrote at least one checkpoint, else 50.
    - rules_score: 100 minus 20 per PreToolUse write attempt denied for lacking
      code-write permission (see _rules_denied_count), read from
      hooks/pretooluse.log; fails soft to 100 if the log can't be read.

    None of these are Haroon's real context-budget/rule-tracking infrastructure —
    just the closest proxy our schema and hooks actually support (see
    decisions.md, 2026-07-29, Stage Y).
    """

    touched = tasks_touched_by_session(session_id, db_path=db_path)
    tasks_touched = len(touched)
    tasks_completed = sum(1 for task in touched if task["completed_by_session"])
    completion_rate = tasks_completed / tasks_touched if tasks_touched else 1.0
    exec_score = round(completion_rate * 100)

    with contextlib.closing(_connect(db_path)) as conn:
        checkpoints_written = conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE session_id = ?", (session_id,)
        ).fetchone()[0]

    context_score = 100 if checkpoints_written >= 1 else 50

    denied_attempts = _rules_denied_count(session_id, log_path=log_path)
    rules_score = max(0, 100 - denied_attempts * 20)

    overall_score = round((exec_score + context_score + rules_score) / 3)

    return {
        "session_id": session_id,
        "tasks_touched": tasks_touched,
        "tasks_completed": tasks_completed,
        "checkpoints_written": checkpoints_written,
        "completion_rate": completion_rate,
        "score": exec_score,
        "exec_score": exec_score,
        "context_score": context_score,
        "rules_score": rules_score,
        "denied_write_attempts": denied_attempts,
        "overall_score": overall_score,
    }


def _safe_default(fn, default):
    """Run fn(), fall back to default on any error - a "best effort" summary
    field must never turn into a crash (Stage AL, see decisions.md, 2026-07-29)."""

    try:
        return fn()
    except Exception:
        return default


def compact_snapshot(session_id: str, db_path: Path = DB_PATH) -> dict:
    """A small, stable-keyed count of state a /compact should not lose.

    tasks/decisions are project-wide totals, not session-scoped — tasks and
    decisions carry no session-ownership column, so "did the count drop"
    is the only comparison the schema supports (mirrors session_score()'s
    single-axis compromise, see decisions.md 2026-07-24). role_declared and
    checklist_items ARE session-scoped, since roles/checkpoints key on
    session_id directly.

    Stage AL added open_task_ids/latest_checkpoint_id/latest_dispatch_run_id/
    evidence_count/receipt_count/has_active_document/graph_node_count/
    graph_edge_count - all read from tables that already existed
    (dispatch_runs, evidence, answer_receipts, documents, graph_nodes/edges),
    no new table. compare_snapshots() only reads the original four keys, so
    these additions cannot introduce a new degraded-recovery finding; they
    are read-only extras for a fuller "what did this session touch" summary.
    Each is individually fail-soft: missing/unreadable data becomes
    0/None/False, never a raised error.
    """

    open_tasks = [task["id"] for task in list_open_tasks(db_path=db_path)]

    with contextlib.closing(_connect(db_path)) as conn:
        decision_count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        role_row = conn.execute(
            "SELECT 1 FROM roles WHERE session_id = ?", (session_id,)
        ).fetchone()

    checkpoint = latest_checkpoint(session_id, db_path=db_path)
    checklist_items = 0
    if checkpoint and checkpoint.get("open_queue"):
        try:
            parsed = json.loads(checkpoint["open_queue"])
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            checklist_items = len(parsed)

    def _latest_checkpoint_id():
        with contextlib.closing(_connect(db_path)) as conn:
            row = conn.execute(
                """SELECT rowid FROM checkpoints WHERE session_id = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
        return row["rowid"] if row else None

    def _latest_dispatch_run_id():
        runs = list_dispatch_runs(limit=1, db_path=db_path)
        return runs[-1]["id"] if runs else None

    def _evidence_count():
        with contextlib.closing(_connect(db_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]

    def _receipt_count():
        with contextlib.closing(_connect(db_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM answer_receipts").fetchone()[0]

    def _has_active_document():
        # None = "unknown", distinct from False = "no active document".
        # projects has no session_id column today, so there is no real path
        # from session_id to project_id - checked dynamically (not hardcoded)
        # so this starts working the day that column exists, with no further
        # change here (see decisions.md, 2026-08-01).
        with contextlib.closing(_connect(db_path)) as conn:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()
            }
            if "session_id" not in columns:
                return None
            row = conn.execute(
                "SELECT id FROM projects WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return get_document(row["id"], "northstar", db_path=db_path) is not None

    def _graph_counts():
        stats = graph_stats(db_path=db_path)
        return stats["nodes"], stats["edges"]

    graph_node_count, graph_edge_count = _safe_default(_graph_counts, (0, 0))

    return {
        "tasks": open_tasks,
        "decisions": decision_count,
        "role_declared": role_row is not None,
        "checklist_items": checklist_items,
        "open_task_ids": open_tasks,
        "latest_checkpoint_id": _safe_default(_latest_checkpoint_id, None),
        "latest_dispatch_run_id": _safe_default(_latest_dispatch_run_id, None),
        "evidence_count": _safe_default(_evidence_count, 0),
        "receipt_count": _safe_default(_receipt_count, 0),
        "has_active_document": _safe_default(_has_active_document, None),
        "graph_node_count": graph_node_count,
        "graph_edge_count": graph_edge_count,
    }


def clear_recovery_verified(session_id: str, db_path: Path = DB_PATH) -> dict:
    """A compact_snapshot()-style summary for /clear specifically.

    verified=True means Stage AF/AH's role-inheritance mechanism (or an
    already-declared role) left this session_id with an actual row in
    `roles` - not just get_role()'s bare "brozzer" fallback with nothing
    behind it (see decisions.md, 2026-07-29).
    """

    return {
        "role": get_role(session_id, db_path=db_path),
        "latest_checkpoint": latest_checkpoint(session_id, db_path=db_path),
        "open_task_ids": [task["id"] for task in list_open_tasks(db_path=db_path)],
        "recent_decisions": recent_decisions(limit=2, db_path=db_path),
        "verified": role_exists(session_id, db_path=db_path),
    }


def save_snapshot(session_id: str, phase: str, db_path: Path = DB_PATH) -> int:
    """Persist compact_snapshot(session_id) as JSON, tagged 'pre' or 'post'."""

    if phase not in ("pre", "post"):
        raise ValueError(f"phase must be 'pre' or 'post', got: {phase!r}")
    snapshot = compact_snapshot(session_id, db_path=db_path)
    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO compact_snapshots (session_id, phase, snapshot_json, taken_at)
               VALUES (?, ?, ?, ?)""",
            (session_id, phase, json.dumps(snapshot, ensure_ascii=False, sort_keys=True), _now()),
        )
        conn.commit()
        return cur.lastrowid


def compare_snapshots(session_id: str, db_path: Path = DB_PATH) -> list[str]:
    """Diff the latest 'pre' and 'post' compact snapshots for missing state.

    NOT fail-soft, on purpose: a missing snapshot row is itself reported as a
    finding rather than treated as "nothing to compare" (see decisions.md,
    2026-07-27) — the whole point of this function is that Compact must not
    be treated as a successful recovery when persistence can't be verified.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        pre_row = conn.execute(
            """SELECT snapshot_json FROM compact_snapshots
               WHERE session_id = ? AND phase = 'pre'
               ORDER BY taken_at DESC, id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        post_row = conn.execute(
            """SELECT snapshot_json FROM compact_snapshots
               WHERE session_id = ? AND phase = 'post'
               ORDER BY taken_at DESC, id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()

    if pre_row is None or post_row is None:
        missing_phase = "pre" if pre_row is None else "post"
        return [f"no '{missing_phase}' compact snapshot found for session {session_id}"]

    pre = json.loads(pre_row["snapshot_json"])
    post = json.loads(post_row["snapshot_json"])

    findings: list[str] = []

    pre_tasks, post_tasks = set(pre["tasks"]), set(post["tasks"])
    missing_tasks = sorted(pre_tasks - post_tasks)
    if missing_tasks:
        findings.append(
            f"tasks: {len(pre_tasks)} -> {len(post_tasks)}, "
            f"{len(missing_tasks)} missing (ids: {missing_tasks})"
        )

    if post["decisions"] < pre["decisions"]:
        findings.append(f"decisions: {pre['decisions']} -> {post['decisions']}")

    if post["checklist_items"] < pre["checklist_items"]:
        findings.append(
            f"checklist_items: {pre['checklist_items']} -> {post['checklist_items']}"
        )

    if pre["role_declared"] and not post["role_declared"]:
        findings.append("role_declared: True -> False")

    return findings


def recovery_package_size(session_id: str, db_path: Path = DB_PATH) -> int:
    """Character count of the recovery package session_start.py prints on a normal start.

    A simple proxy measurement (not a byte-exact echo of session_start.py's own
    output) meant to back the "bounded package" claim from Stage AA: it
    re-derives the same facts session_start.py prints — role, checkpoint,
    risk, degraded warning, recent decisions, related memory — via the same
    mc functions, and mirrors their format strings. If session_start.py's
    formatting changes, keep this in sync (see decisions.md, 2026-07-29).
    """

    lines: list[str] = []

    role = get_role(session_id, db_path=db_path)
    lines.append(
        f"[bbb-lite] role: {role} "
        f"(code={can_write_code(session_id, db_path=db_path)}, "
        f"data={can_write_data(session_id, db_path=db_path)})"
    )

    checkpoint = latest_checkpoint(session_id, db_path=db_path)
    last_task = (
        checkpoint["current_task"] if checkpoint and checkpoint["current_task"] else "none"
    )
    open_tasks = list_open_tasks(db_path=db_path)
    lines.append(
        f"[bbb-lite] session {session_id} (startup) | "
        f"last task: {last_task} | {len(open_tasks)} open task(s)"
    )

    if checkpoint and checkpoint["next_action"]:
        lines.append(f"[bbb-lite] {checkpoint['next_action']}")

    if checkpoint and checkpoint["risk"]:
        lines.append(f"[bbb-lite] risk: {checkpoint['risk']}")

    if checkpoint:
        owner_status = next(
            (
                row["status"]
                for row in list_sessions(db_path=db_path)
                if row["session_id"] == checkpoint["session_id"]
            ),
            None,
        )
        if owner_status == "degraded":
            lines.append(
                "[bbb-lite] WARNING: last session's recovery was degraded - "
                "some info may be missing"
            )

    for text in recent_decisions(limit=2, db_path=db_path):
        lines.append(f"[bbb-lite] recent decision: {text}")

    if checkpoint and checkpoint["current_task"]:
        try:
            hits = semantic_search(checkpoint["current_task"], limit=2, db_path=db_path)
        except Exception:
            hits = []
        for hit in hits:
            lines.append(
                f"[bbb-lite] related memory: {hit['title']} "
                f"({hit['source_file']}, confidence={hit['confidence']})"
            )

    return sum(len(line) for line in lines)


_COMPLETABLE_STATUSES = {"in_progress", "ready_for_review"}


def complete_task(
    task_id: int,
    *,
    session_id: str,
    evidence_id: int,
    completion_summary: str,
    require_test_evidence: bool = True,
    db_path: Path = DB_PATH,
) -> dict:
    """The one sanctioned path to tasks.status='done'; raw SQL is refused by the guard.

    Stage AE: "no done without evidence" — session_id, evidence_id, and
    completion_summary are all required (no defaults). evidence_id must name
    an existing evidence row whose status isn't 'fail', and completion_summary
    can't be blank. completed_by_role is not a separate parameter — it's
    derived from session_id via get_role(), since that's already known.

    Stage AP tightens this further (Codex's Task Completion item):
    - require_test_evidence (default True) refuses completion unless the
      evidence row's `kind` contains "test" (case-insensitive) — completion
      needs the evidence to actually be a test run, not just any passing
      record. Callers whose evidence genuinely isn't a test run (e.g.
      bin/dispatch.py's agentic dispatch receipt) must pass
      require_test_evidence=False explicitly, not fake a "test" label.
    - the task must currently be 'in_progress' or 'ready_for_review' —
      'todo'/'pending' or 'blocked' tasks must progress first; this is
      checked before the subtask check below.
    - a task with any not-yet-'done' subtask (parent_task_id pointing at
      it) cannot be completed until every subtask is done.

    Records a 'completed' row in task_activity and a full receipt (evidence,
    role, summary) in task_completions.
    """

    if not completion_summary.strip():
        raise ValueError("completion_summary cannot be empty")

    evidence_rows = query_rows(
        "SELECT * FROM evidence WHERE id = ?", (evidence_id,), db_path=db_path
    )
    if not evidence_rows:
        raise ValueError(f"evidence not found: {evidence_id}")
    if evidence_rows[0]["status"] == "fail":
        raise ValueError("cannot complete a task with failing evidence")
    if require_test_evidence and "test" not in evidence_rows[0]["kind"].lower():
        raise ValueError("completion evidence must include test execution")

    completed_by_role = get_role(session_id, db_path=db_path)

    authorization = _CompletionAuthorization(task_id=str(task_id))
    now = _now()
    with contextlib.closing(_connect(db_path)) as conn:
        task = conn.execute(
            "SELECT id, status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task is None:
            raise MCCompletionRefused(f"completion refused: task not found: {task_id}")
        if task["status"] == "done":
            raise MCCompletionRefused(f"completion refused: task already done: {task_id}")
        if task["status"] not in _COMPLETABLE_STATUSES:
            raise ValueError(
                f"cannot complete: task status {task['status']!r} is not ready "
                "(must be 'in_progress' or 'ready_for_review')"
            )
        open_subtasks = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE parent_task_id = ? AND status != 'done'",
            (task_id,),
        ).fetchone()[0]
        if open_subtasks:
            raise ValueError(f"cannot complete: {open_subtasks} subtask(s) still open")
        updated = conn.execute(
            "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ? AND status != 'done'",
            (now, task_id),
            _completion_authorization=authorization,
        )
        if updated.rowcount != 1:
            raise MCCompletionRefused(
                f"completion refused: guarded update changed {updated.rowcount} rows"
            )
        conn.execute(
            "INSERT INTO task_activity (task_id, session_id, activity, at) VALUES (?, ?, ?, ?)",
            (task_id, session_id, "completed", now),
        )
        conn.execute(
            """INSERT INTO task_completions
               (task_id, session_id, evidence_id, completed_by_role, completion_summary, completed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, session_id, evidence_id, completed_by_role, completion_summary, now),
        )
        conn.commit()
    return {
        "ok": True,
        "task_id": task_id,
        "status": "done",
        "evidence_id": evidence_id,
        "completed_by_role": completed_by_role,
    }


_STOPWORDS = {
    "this", "that", "with", "from", "have", "has", "was", "were", "are",
    "not", "but", "you", "your", "our", "its", "the", "and", "for", "via",
    "into", "does", "per", "any", "all", "one", "only", "when", "than",
    "then", "will", "can", "could", "would", "each", "such", "here",
    "there", "what", "which", "who", "how", "why", "over", "under",
}


def _significant_words(text: str) -> list[str]:
    """Lowercase words with stopwords/short words dropped — shared by indexing and search."""

    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_']*", text.lower())
    return [w for w in words if len(w) > 3 and w not in _STOPWORDS]


def _extract_keywords(text: str, *, limit: int = 20) -> str:
    """Very small keyword picker: significant words, deduped in order, capped at limit."""

    seen: list[str] = []
    for word in _significant_words(text):
        if word in seen:
            continue
        seen.append(word)
        if len(seen) >= limit:
            break
    return ", ".join(seen)


def normalize_term(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, and a naive per-word plural strip.

    The plural handling is "drop a trailing 's' on words longer than 3
    letters that don't already end in 'ss'" — a crude heuristic, not a real
    linguistic stemmer (it happily mangles words like "bus" or "status" if
    they were long enough to qualify). Good enough for catching "SMAs" vs
    "sma", not for anything more subtle. See decisions.md, Stage AR.
    """

    text = re.sub(r"[^\w\s]", " ", text.lower())
    words = []
    for word in text.split():
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def add_alias(canonical_term: str, alias: str, *, db_path: Path = DB_PATH) -> int:
    """Register alias -> canonical_term for the log_lesson() alias-match check (Stage AR).

    alias is stored normalize_term()-ed so lookups at match time are a plain
    equality check. Re-adding the same alias (after normalization) with a
    different canonical_term overwrites it rather than erroring, since alias
    is UNIQUE.
    """

    normalized_alias = normalize_term(alias)
    with contextlib.closing(_connect(db_path)) as conn:
        cursor = conn.execute(
            """INSERT INTO term_aliases (canonical_term, alias) VALUES (?, ?)
               ON CONFLICT(alias) DO UPDATE SET canonical_term = excluded.canonical_term
               RETURNING id""",
            (canonical_term, normalized_alias),
        )
        alias_id = cursor.fetchone()[0]
        conn.commit()
        return alias_id


def _match_alias(title: str, *, db_path: Path = DB_PATH) -> str | None:
    """Return the canonical_term for the first word in `title` that matches a known alias.

    Word-level only, per the Stage AR spec ("one of the words in the new
    title matches an alias") — not a substring or phrase match.
    """

    words = set(normalize_term(title).split())
    if not words:
        return None
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute("SELECT canonical_term, alias FROM term_aliases").fetchall()
    for row in rows:
        if row["alias"] in words:
            return row["canonical_term"]
    return None


_CONFIDENCE_LEVELS = {"high": 0.9, "medium": 0.5, "low": 0.2}
_DEFAULT_CONFIDENCE = 0.5

# Stage AM: frontmatter fields separate from the numeric `confidence` above.
# evidence_level is Haroon's epistemic category (how was this known, not how
# confident are we), not a replacement for confidence (see decisions.md,
# 2026-07-29).
_VALID_DOC_TYPES = {"feedback", "project", "reference", "lesson"}
_DEFAULT_DOC_TYPE = "lesson"
_DEFAULT_PROJECT = "global"
_VALID_EVIDENCE_LEVELS = {"verified", "remembered", "inferred", "unknown"}
_DEFAULT_EVIDENCE_LEVEL = "unknown"
_VALID_CURRENTNESS = {"current", "historical", "superseded", "unknown"}
_DEFAULT_CURRENTNESS = "current"


def _parse_confidence_value(raw: str) -> float:
    """Map a frontmatter confidence label (high/medium/low) or bare number to a float.

    Falls back to _DEFAULT_CONFIDENCE for anything unrecognized, so a typo'd or
    missing confidence never breaks indexing.
    """

    raw = raw.strip().lower()
    if raw in _CONFIDENCE_LEVELS:
        return _CONFIDENCE_LEVELS[raw]
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_CONFIDENCE


def _parse_memory_file(path: Path) -> dict:
    """Return this memory/*.md file's frontmatter-derived fields as a dict.

    Keys: title, body, confidence, doc_type, project, evidence_level,
    currentness. Each falls back to a sane default when frontmatter is
    absent or doesn't declare it, so a plain lesson file with no
    frontmatter (or pre-Stage-AM frontmatter with only title/confidence)
    indexes exactly as it did before Stage AM.
    """

    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        return {
            "title": path.stem,
            "body": text.strip(),
            "confidence": _DEFAULT_CONFIDENCE,
            "doc_type": _DEFAULT_DOC_TYPE,
            "project": _DEFAULT_PROJECT,
            "evidence_level": _DEFAULT_EVIDENCE_LEVEL,
            "currentness": _DEFAULT_CURRENTNESS,
        }
    frontmatter, body = match.group(1), match.group(2)

    def _field(name: str, valid: set[str] | None, default: str) -> str:
        field_match = re.search(rf"^{name}:\s*(.+)$", frontmatter, re.M)
        if not field_match:
            return default
        value = field_match.group(1).strip().lower()
        if valid is not None and value not in valid:
            return default
        return value

    title_match = re.search(r"^title:\s*(.+)$", frontmatter, re.M)
    title = title_match.group(1).strip() if title_match else path.stem
    confidence_match = re.search(r"^confidence:\s*(.+)$", frontmatter, re.M)
    confidence = (
        _parse_confidence_value(confidence_match.group(1))
        if confidence_match
        else _DEFAULT_CONFIDENCE
    )
    return {
        "title": title,
        "body": body.strip(),
        "confidence": confidence,
        "doc_type": _field("type", _VALID_DOC_TYPES, _DEFAULT_DOC_TYPE),
        "project": _field("project", None, _DEFAULT_PROJECT),
        "evidence_level": _field("evidence_level", _VALID_EVIDENCE_LEVELS, _DEFAULT_EVIDENCE_LEVEL),
        "currentness": _field("currentness", _VALID_CURRENTNESS, _DEFAULT_CURRENTNESS),
    }


def _get_embedding_model() -> Any:
    """Load the sentence-transformers model once and cache it for the process lifetime.

    Not offline-first: the very first call on a machine downloads the model from the
    Hugging Face Hub and raises if there's no network. Once Hugging Face's local cache
    (~/.cache/huggingface) has the model, later calls load from disk and need no network.

    Stage AU: logs a health_events row (component="embedding_model") the moment
    it goes unavailable — still raises right after, exactly as before, so
    _embed_or_none() (the actual fail-soft point for callers) is unchanged.
    Logs to the default db_path: whether the embedding model is loadable is a
    process-global fact (the _embedding_unavailable flag itself is global, not
    per-caller), not something scoped to whichever db_path the caller happens
    to be using.
    """

    global _embedding_model, _embedding_unavailable
    if _embedding_unavailable:
        raise RuntimeError("embedding model unavailable in this process")
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        try:
            _embedding_model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
        except Exception as exc:
            _embedding_unavailable = True
            log_health_event("embedding_model", "load_failed", detail=str(exc))
            raise
    return _embedding_model


def _embed(text: str) -> bytes:
    """Encode text into a vector using the cached model, returned as raw float32 bytes."""

    vector = _get_embedding_model().encode(text, convert_to_numpy=True)
    return np.asarray(vector, dtype=np.float32).tobytes()


def _embed_or_none(text: str) -> bytes | None:
    """Best-effort embedding for indexing; keyword memory must survive offline."""

    global _embedding_unavailable
    if _embedding_unavailable:
        return None
    try:
        return _embed(text)
    except Exception:
        _embedding_unavailable = True
        return None


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def index_memory_files(db_path: Path = DB_PATH) -> int:
    """Upsert every memory/*.md file into memory_index. Re-running re-indexes, never duplicates.

    Stage AS: also tracks content_hash/created_at/updated_at/version. If a
    row's content_hash is unchanged from what's already stored, updated_at
    and version are left untouched (nothing actually changed); otherwise
    updated_at moves to now and version increments. created_at is set once,
    on the row's first insert, and never overwritten afterward.

    Stage AU: a single unreadable/malformed file no longer aborts the whole
    sweep — it's skipped (logged as a health_events row, component=
    "memory_index") and indexing continues with the rest.
    """

    memory_dir = Path(__file__).resolve().parent / "memory"
    count = 0
    now = _now()
    # Skips are logged AFTER the connection below commits/closes, not from
    # inside the loop — log_health_event() opens its own connection, and
    # doing that while `conn` still holds an uncommitted write transaction
    # open would deadlock/"database is locked" against itself.
    skipped: list[tuple[str, str]] = []
    with contextlib.closing(_connect(db_path)) as conn:
        for path in sorted(memory_dir.glob("*.md")):
            try:
                parsed = _parse_memory_file(path)
                title, body, confidence = parsed["title"], parsed["body"], parsed["confidence"]
                keywords = _extract_keywords(f"{title} {body}")
                embedding = _embed_or_none(f"{title}\n{body}")
                content_hash = _sha256_text(body)
                conn.execute(
                    """INSERT INTO memory_index
                           (source_file, title, content, keywords, embedding, confidence,
                            doc_type, project, evidence_level, currentness,
                            content_hash, created_at, updated_at, status, replaced_by, version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, 1)
                       ON CONFLICT(source_file) DO UPDATE SET
                           title = excluded.title,
                           content = excluded.content,
                           keywords = excluded.keywords,
                           embedding = excluded.embedding,
                           confidence = excluded.confidence,
                           doc_type = excluded.doc_type,
                           project = excluded.project,
                           evidence_level = excluded.evidence_level,
                           currentness = excluded.currentness,
                           content_hash = excluded.content_hash,
                           created_at = COALESCE(memory_index.created_at, excluded.created_at),
                           updated_at = CASE
                               WHEN memory_index.content_hash IS excluded.content_hash
                               THEN COALESCE(memory_index.updated_at, excluded.updated_at)
                               ELSE excluded.updated_at
                           END,
                           version = CASE
                               WHEN memory_index.content_hash IS excluded.content_hash
                               THEN COALESCE(memory_index.version, 1)
                               ELSE COALESCE(memory_index.version, 1) + 1
                           END""",
                    (
                        path.name, title, body, keywords, embedding, confidence,
                        parsed["doc_type"], parsed["project"], parsed["evidence_level"],
                        parsed["currentness"], content_hash, now, now,
                    ),
                )
                _upsert_memory_graph(conn, path.name, title, body, keywords)
                count += 1
            except Exception as exc:
                skipped.append((path.name, str(exc)))
        conn.commit()
    for name, detail in skipped:
        log_health_event("memory_index", "file_skipped", detail=f"{name}: {detail}", db_path=db_path)
    return count


def mark_memory_replaced(
    old_source_file: str, new_source_file: str, db_path: Path = DB_PATH
) -> None:
    """Mark an old memory_index row as replaced by a newer one (Stage AS).

    Leaves the row in place instead of deleting it — status becomes
    'replaced' and replaced_by points at the successor file, so heal_memory()
    merges leave a trace of what replaced what. search_memory() and
    semantic_search() exclude 'replaced' rows by default.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """UPDATE memory_index SET status = 'replaced', replaced_by = ?, updated_at = ?
               WHERE source_file = ?""",
            (new_source_file, _now(), old_source_file),
        )
        conn.commit()


def search_memory(
    query: str,
    *,
    limit: int = 3,
    project: str | None = None,
    include_replaced: bool = False,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Keyword search over memory_index — never reads memory/*.md directly.

    Mirrors the "recall comes from the index, not the raw docs" split: this
    is the search layer, backed by whatever index_memory_files() last wrote.
    Not semantic search — weighted keyword-count ranking: a term counted in
    title is worth 3 points, in keywords 2 points, in content 1 point. The
    raw keyword score is then multiplied by the row's confidence, so a
    low-confidence lesson ranks below an equally-matching high-confidence one.

    project is an optional filter (Stage AM) — None (the default) searches
    everything, exactly as before that stage. include_replaced (Stage AS) —
    False (the default) hides rows a heal_memory() merge marked 'replaced'.
    """

    terms = list(dict.fromkeys(_significant_words(query)))
    if not terms:
        return []
    conditions = []
    params: list[Any] = []
    if project is not None:
        conditions.append("project = ?")
        params.append(project)
    if not include_replaced:
        conditions.append("status != 'replaced'")
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT source_file, title, content, keywords, confidence FROM memory_index{where_clause}",
            params,
        ).fetchall()
    scored: list[tuple[float, dict]] = []
    for row in rows:
        title = row["title"].lower()
        keywords = row["keywords"].lower()
        content = row["content"].lower()
        raw_score = sum(
            3 * title.count(term) + 2 * keywords.count(term) + content.count(term)
            for term in terms
        )
        if raw_score > 0:
            confidence = row["confidence"] if row["confidence"] is not None else _DEFAULT_CONFIDENCE
            scored.append((raw_score * confidence, dict(row)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored[:limit]]


def semantic_search(
    query: str,
    *,
    limit: int = 3,
    project: str | None = None,
    include_replaced: bool = False,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Embedding-based search over memory_index — finds meaning matches with no shared words.

    Complements search_memory() (keyword ranking); this ranks by cosine similarity
    between the query's embedding and each row's stored embedding, multiplied by
    the row's confidence — a low-confidence lesson ranks below an equally
    similar high-confidence one. `similarity` in the returned dict stays the raw
    cosine value; the confidence weighting only affects ordering.

    project is an optional filter (Stage AM) — None (the default) searches
    everything, exactly as before that stage. include_replaced (Stage AS) —
    False (the default) hides rows a heal_memory() merge marked 'replaced'.
    The returned dict includes `currentness` (Stage AT: detect_memory_conflicts()
    compares it across results to spot same-topic notes that disagree).
    """

    query_vector = np.frombuffer(_embed(query), dtype=np.float32)
    conditions = ["embedding IS NOT NULL"]
    params: list[Any] = []
    if project is not None:
        conditions.append("project = ?")
        params.append(project)
    if not include_replaced:
        conditions.append("status != 'replaced'")
    where_clause = " AND ".join(conditions)
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            f"""SELECT source_file, title, content, keywords, embedding, confidence, currentness
               FROM memory_index WHERE {where_clause}""",
            params,
        ).fetchall()
    scored: list[tuple[float, dict]] = []
    for row in rows:
        row_vector = np.frombuffer(row["embedding"], dtype=np.float32)
        similarity = _cosine_similarity(query_vector, row_vector)
        confidence = row["confidence"] if row["confidence"] is not None else _DEFAULT_CONFIDENCE
        item = {key: row[key] for key in row.keys() if key != "embedding"}
        item["similarity"] = similarity
        scored.append((similarity * confidence, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored[:limit]]


_MEMORY_CONFLICT_SIMILARITY_THRESHOLD = 0.5


def detect_memory_conflicts(query: str, *, db_path: Path = DB_PATH) -> list[dict]:
    """Find same-topic memory notes that disagree on currentness (Stage AT, item 7).

    Runs semantic_search(query, limit=5) — which already excludes
    status='replaced' rows (Stage AS; those are already-resolved merges, not
    open conflicts) — then pairs up results that both clear
    _MEMORY_CONFLICT_SIMILARITY_THRESHOLD similarity to the query AND whose
    `currentness` field disagrees (e.g. one 'current', one 'superseded').
    Detection only: nothing here rewrites currentness or merges anything.
    """

    results = semantic_search(query, limit=5, db_path=db_path)
    candidates = [r for r in results if r["similarity"] > _MEMORY_CONFLICT_SIMILARITY_THRESHOLD]
    conflicts: list[dict] = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            if a["currentness"] != b["currentness"]:
                conflicts.append(
                    {
                        "a": a,
                        "b": b,
                        "reason": (
                            f"both match '{query}' (similarity {a['similarity']:.2f}/"
                            f"{b['similarity']:.2f}) but currentness differs: "
                            f"{a['currentness']!r} vs {b['currentness']!r}"
                        ),
                    }
                )
    return conflicts


_EXCLUDED_INDEX_DIR_NAMES = {"__pycache__", "node_modules"}


def _is_excluded_index_path(path: Path) -> bool:
    """Skip hidden dirs (.git, .vs, .claude, ...) and dependency/cache dirs."""

    return any(
        part.startswith(".") or part in _EXCLUDED_INDEX_DIR_NAMES for part in path.parts
    )


def _extract_python_symbols(tree: ast.AST) -> list[dict]:
    """Every function/class def in a parsed Python module, dotted-qualified by
    enclosing scope (e.g. "ClassName.method_name") so two classes in the same
    file can each have a method of the same name without colliding on
    symbol_index's UNIQUE(file_path, symbol_name)."""

    symbols: list[dict] = []
    scope_stack: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def _record(self, node: ast.AST, symbol_type: str) -> None:
            qualified_name = ".".join(scope_stack + [node.name])
            symbols.append(
                {
                    "name": qualified_name,
                    "type": symbol_type,
                    "docstring": ast.get_docstring(node),
                }
            )
            scope_stack.append(node.name)
            self.generic_visit(node)
            scope_stack.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._record(node, "class")

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._record(node, "function")

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._record(node, "async function")

    _Visitor().visit(tree)
    return symbols


def index_symbols(
    root_dir: str | Path | None = None,
    extensions: tuple[str, ...] = (".py",),
    *,
    db_path: Path = DB_PATH,
) -> int:
    """Parse source files under root_dir and upsert their symbols into symbol_index.

    Stage AN (1/3): only ".py" (via the ast module) actually does anything -
    other languages (.js/.ts/.sql/.sh) are a deliberate next step, not
    started here (see decisions.md, 2026-07-29). extensions is accepted so
    that future shape doesn't require an API change; any extension other
    than ".py" is silently skipped for now.

    project is set to root_dir's own folder name for every symbol indexed in
    this run - the same "last folder name" convention resolve_project() uses
    (Stage AG), since there is no other session/file -> project mapping in
    the schema.
    """

    root = Path(root_dir) if root_dir is not None else Path(__file__).resolve().parent
    project = root.name
    count = 0
    with contextlib.closing(_connect(db_path)) as conn:
        for ext in extensions:
            if ext != ".py":
                continue
            for path in sorted(root.rglob(f"*{ext}")):
                if _is_excluded_index_path(path.relative_to(root)):
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except (SyntaxError, UnicodeDecodeError, OSError):
                    continue
                rel_path = str(path.relative_to(root))
                for symbol in _extract_python_symbols(tree):
                    _upsert_symbol_row(conn, rel_path, symbol, project)
                    count += 1
        conn.commit()
    return count


def _upsert_symbol_row(conn: sqlite3.Connection, file_path: str, symbol: dict, project: str) -> None:
    embedding = _embed_or_none(f"{symbol['name']}\n{symbol['docstring'] or ''}")
    conn.execute(
        """INSERT INTO symbol_index
               (file_path, symbol_name, symbol_type, docstring, embedding, project)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(file_path, symbol_name) DO UPDATE SET
               symbol_type = excluded.symbol_type,
               docstring = excluded.docstring,
               embedding = excluded.embedding,
               project = excluded.project""",
        (file_path, symbol["name"], symbol["type"], symbol["docstring"], embedding, project),
    )


def upsert_symbol_file(
    file_path: str | Path, *, project: str | None = None, db_path: Path = DB_PATH
) -> int:
    """Re-index just one .py file's symbols — a lighter, single-file version
    of index_symbols() for CLI/editor-integration use (Stage AN 3/3).

    project defaults to the file's parent directory name (same convention
    index_symbols() uses for a whole root_dir) if not given.
    """

    path = Path(file_path)
    if project is None:
        project = path.resolve().parent.name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel_path = str(path)
    count = 0
    with contextlib.closing(_connect(db_path)) as conn:
        for symbol in _extract_python_symbols(tree):
            _upsert_symbol_row(conn, rel_path, symbol, project)
            count += 1
        conn.commit()
    return count


def _parse_doc_file(path: Path) -> tuple[str, str]:
    """Return (title, content) for a plain (frontmatter-free) doc: title is
    the first "# heading" line if present, else the filename stem."""

    text = path.read_text(encoding="utf-8").strip()
    first_line = text.splitlines()[0] if text else ""
    heading_match = re.match(r"^#\s+(.+)$", first_line)
    title = heading_match.group(1).strip() if heading_match else path.stem
    return title, text


def _upsert_doc_row(conn: sqlite3.Connection, file_path: str, title: str, content: str, project: str) -> None:
    embedding = _embed_or_none(f"{title}\n{content}")
    conn.execute(
        """INSERT INTO doc_index (file_path, title, content, embedding, project)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(file_path) DO UPDATE SET
               title = excluded.title,
               content = excluded.content,
               embedding = excluded.embedding,
               project = excluded.project""",
        (file_path, title, content, embedding, project),
    )


def upsert_doc_file(
    file_path: str | Path, *, project: str | None = None, db_path: Path = DB_PATH
) -> int:
    """Re-index just one .md file into doc_index — a lighter, single-file
    version of index_docs() for CLI/editor-integration use (Stage AN 3/3).

    project defaults to the file's parent directory name (same convention
    index_docs() uses for a whole root_dir) if not given. Does not exempt
    memory/ the way index_docs() does — a direct, explicit path always wins;
    the memory/ skip is index_docs()'s "don't duplicate a bulk scan" rule,
    not a blanket ban.
    """

    path = Path(file_path)
    if project is None:
        project = path.resolve().parent.name
    title, content = _parse_doc_file(path)
    with contextlib.closing(_connect(db_path)) as conn:
        _upsert_doc_row(conn, str(path), title, content, project)
        conn.commit()
    return 1


def index_docs(
    root_dir: str | Path | None = None,
    extensions: tuple[str, ...] = (".md",),
    *,
    db_path: Path = DB_PATH,
) -> int:
    """Index project docs (e.g. docs/*.md) into doc_index.

    Deliberately skips memory/ - memory/*.md is already covered by
    memory_index/index_memory_files() (Stage C onward); duplicating it here
    would just be two indexes of the same files (see decisions.md,
    2026-07-29). Stage AN (1/3): only ".md" does anything for now, same
    placeholder shape as index_symbols().
    """

    root = Path(root_dir) if root_dir is not None else Path(__file__).resolve().parent
    memory_dir = (root / "memory").resolve()
    project = root.name
    count = 0
    with contextlib.closing(_connect(db_path)) as conn:
        for ext in extensions:
            if ext != ".md":
                continue
            for path in sorted(root.rglob(f"*{ext}")):
                rel_path = path.relative_to(root)
                if _is_excluded_index_path(rel_path):
                    continue
                if path.resolve().is_relative_to(memory_dir):
                    continue
                try:
                    title, content = _parse_doc_file(path)
                except (UnicodeDecodeError, OSError):
                    continue
                _upsert_doc_row(conn, str(rel_path), title, content, project)
                count += 1
        conn.commit()
    return count


def search_symbols(
    query: str, *, limit: int = 3, project: str | None = None, db_path: Path = DB_PATH
) -> list[dict]:
    """Embedding-based search over symbol_index — same cosine-similarity ranking
    as semantic_search(), applied to indexed function/class symbols instead of
    memory/*.md notes (Stage AN 1/3). No confidence weighting: symbol_index
    has no confidence column, unlike memory_index.

    project is an optional filter (Stage AN 2/3, same shape as Stage AM's
    memory search filter) — None searches everything.
    """

    query_vector = np.frombuffer(_embed(query), dtype=np.float32)
    with contextlib.closing(_connect(db_path)) as conn:
        if project is None:
            rows = conn.execute(
                """SELECT file_path, symbol_name, symbol_type, docstring, embedding, project
                   FROM symbol_index WHERE embedding IS NOT NULL"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT file_path, symbol_name, symbol_type, docstring, embedding, project
                   FROM symbol_index WHERE embedding IS NOT NULL AND project = ?""",
                (project,),
            ).fetchall()
    scored: list[tuple[float, dict]] = []
    for row in rows:
        row_vector = np.frombuffer(row["embedding"], dtype=np.float32)
        similarity = _cosine_similarity(query_vector, row_vector)
        item = {key: row[key] for key in row.keys() if key != "embedding"}
        item["similarity"] = similarity
        scored.append((similarity, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored[:limit]]


def search_docs(
    query: str, *, limit: int = 3, project: str | None = None, db_path: Path = DB_PATH
) -> list[dict]:
    """Embedding-based search over doc_index — same cosine-similarity ranking
    as semantic_search(), applied to indexed docs/*.md files instead of
    memory/*.md notes (Stage AN 1/3).

    project is an optional filter (Stage AN 2/3, same shape as Stage AM's
    memory search filter) — None searches everything.
    """

    query_vector = np.frombuffer(_embed(query), dtype=np.float32)
    with contextlib.closing(_connect(db_path)) as conn:
        if project is None:
            rows = conn.execute(
                """SELECT file_path, title, content, embedding, project
                   FROM doc_index WHERE embedding IS NOT NULL"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT file_path, title, content, embedding, project
                   FROM doc_index WHERE embedding IS NOT NULL AND project = ?""",
                (project,),
            ).fetchall()
    scored: list[tuple[float, dict]] = []
    for row in rows:
        row_vector = np.frombuffer(row["embedding"], dtype=np.float32)
        similarity = _cosine_similarity(query_vector, row_vector)
        item = {key: row[key] for key in row.keys() if key != "embedding"}
        item["similarity"] = similarity
        scored.append((similarity, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored[:limit]]


def cyg_similar(
    query: str, *, project: str | None = None, limit: int = 5, db_path: Path = DB_PATH
) -> list[dict]:
    """Merge search_symbols()/search_docs() hits, ranked by similarity.

    The practical form of Haroon's "reuse before build" Golden Rule (Stage AN
    2/3): what indexed code or doc already looks like `query`. Each hit
    carries source_table ("symbol_index" or "doc_index") so a caller (or
    cyg_exists()) knows where a match came from.
    """

    symbol_hits = search_symbols(query, limit=limit, project=project, db_path=db_path)
    doc_hits = search_docs(query, limit=limit, project=project, db_path=db_path)
    combined = []
    for hit in symbol_hits:
        item = dict(hit)
        item["source_table"] = "symbol_index"
        combined.append(item)
    for hit in doc_hits:
        item = dict(hit)
        item["source_table"] = "doc_index"
        combined.append(item)
    combined.sort(key=lambda item: item["similarity"], reverse=True)
    return combined[:limit]


def cyg_exists(
    query: str, *, project: str | None = None, threshold: float = 0.6, db_path: Path = DB_PATH
) -> dict:
    """Does something like `query` already exist in symbol_index/doc_index?

    The practical, checkable form of Haroon's "reuse before build" Golden
    Rule (Stage AN 2/3) — checks real indexed symbols/docs instead of just
    trusting the model to remember to search first. `score` is always the
    best similarity found (None only if nothing is indexed at all); `match`
    is populated only once that score clears `threshold` — below threshold,
    the score is still reported so a caller can see "close but not close
    enough", not just a bare False.
    """

    hits = cyg_similar(query, project=project, limit=1, db_path=db_path)
    if not hits:
        return {"exists": False, "match": None, "score": None}
    best = hits[0]
    score = best["similarity"]
    exists = score >= threshold
    return {"exists": exists, "match": best if exists else None, "score": score}


def ask_ollama(prompt: str, model: str = "gemma:2b", *, db_path: Path = DB_PATH) -> str | None:
    """Fail-soft call to a local ollama model; None (never raises) if it's unavailable.

    Mirrors the MC-unreachable-never-breaks-the-session principle: if ollama isn't
    installed, isn't running, or just times out, callers fall back to their own
    default behavior instead of crashing. Stage AU: every None return now also
    logs a health_events row (component="ollama") — same fail-soft outcome,
    just visible now via get_health_state().
    """

    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_health_event("ollama", "unavailable", detail=str(exc), db_path=db_path)
        return None
    if result.returncode != 0:
        log_health_event(
            "ollama", "nonzero_exit", detail=f"returncode={result.returncode}", db_path=db_path
        )
        return None
    output = result.stdout.strip()
    if not output:
        log_health_event("ollama", "blank_output", db_path=db_path)
        return None
    return output


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _normalize_for_match(text: str) -> str:
    """Lowercase, strip ANSI escapes, and collapse '-'/whitespace runs to a single space.

    Real ollama output can carry terminal control codes (cursor moves, line
    clears) and phrase an option with a space where it was given with a
    hyphen (or vice versa); this puts both sides of a match on equal footing.
    """

    text = _ANSI_ESCAPE_RE.sub("", text)
    text = re.sub(r"[-\s]+", " ", text.strip().lower())
    return text


def classify(prompt: str, options: Iterable[str], *, model: str = "gemma:2b") -> str | None:
    """Ask ask_ollama to pick one of `options` for `prompt`; fail-soft to None.

    The model is asked to answer with just the chosen option. Both the answer
    and each option are run through _normalize_for_match() before the
    substring check, so minor extra wording, stray ANSI codes, punctuation,
    or hyphen/space differences from the model don't break the match. Returns
    the option in its original casing, or None if ollama is unavailable or
    its answer doesn't match any option.
    """

    options = list(options)
    if not options:
        return None
    choices = ", ".join(options)
    full_prompt = (
        f"{prompt}\nChoose exactly one option from: {choices}\n"
        "Answer with only the option, nothing else."
    )
    answer = ask_ollama(full_prompt, model=model)
    if answer is None:
        return None
    normalized = _normalize_for_match(answer)
    for option in options:
        if _normalize_for_match(option) in normalized:
            return option
    return None


def log_lesson(
    title: str, content: str, *, confidence: str = "medium", db_path: Path = DB_PATH
) -> Path | None:
    """Write one lesson file to memory/ (one fact per file) — unless it's caught as a
    duplicate first. Filesystem + memory_index (+ read-only term_aliases) only, never any
    other DB table.

    Three cheap checks run before ever asking ollama (Stage AR):
      1. Exact match: normalize_term(title) equals an existing lesson's title exactly
         -> skipped outright, ollama is never called.
      2. Alias match: a word in title matches a known alias -> canonical_term is appended
         to title for context; this is informational only, it never blocks the write, and
         it also skips straight past the semantic check below (no ollama call either).
      3. Only when neither 1 nor 2 fired does the original ask_ollama()-based semantic
         duplicate check (nearest hit via search_memory()) run, unchanged.

    Returns the new file's Path, or None if it was skipped as a duplicate.
    """

    memory_dir = Path(__file__).resolve().parent / "memory"
    memory_dir.mkdir(exist_ok=True)

    index_memory_files(db_path)

    normalized_new_title = normalize_term(title)
    with contextlib.closing(_connect(db_path)) as conn:
        existing_titles = conn.execute(
            "SELECT source_file, title FROM memory_index"
        ).fetchall()
    for row in existing_titles:
        if normalize_term(row["title"]) == normalized_new_title:
            print(
                f"[mc.log_lesson] exact duplicate, skipped: "
                f"{title!r} == {row['source_file']}"
            )
            return None

    canonical_term = _match_alias(title, db_path=db_path)
    if canonical_term is not None:
        title = f"{title} ({canonical_term})"
    else:
        candidates = search_memory(f"{title} {content}", limit=1, db_path=db_path)
        if candidates:
            existing = candidates[0]
            verdict = ask_ollama(
                f"Existing note: '{existing['title']}: {existing['content']}'\n"
                f"New note: '{title}: {content}'\n"
                "Are these about the same fact? Answer only yes or no."
            )
            if verdict and "yes" in verdict.strip().lower():
                print(
                    f"[mc.log_lesson] muhtemelen tekrar, eklenmedi: "
                    f"{title!r} ~ {existing['source_file']}"
                )
                return None

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "lesson"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = memory_dir / f"lesson-{date}-{slug}.md"
    suffix = 2
    while path.exists():
        path = memory_dir / f"lesson-{date}-{slug}-{suffix}.md"
        suffix += 1
    path.write_text(
        f"---\ntitle: {title}\ndate: {date}\nconfidence: {confidence}\n---\n\n{content.strip()}\n",
        encoding="utf-8",
    )
    index_memory_files(db_path)
    return path


def find_similar_lesson_groups(
    threshold: float = 0.85, db_path: Path = DB_PATH
) -> list[list[dict]]:
    """Group lesson files whose embeddings are pairwise similar at/above `threshold`.

    MEMORY.md and decisions.md are structural index files, not lessons, so
    they're excluded from consideration. Similarity is transitive here: if A~B
    and B~C both clear the threshold, A/B/C come back as one group of three,
    not two overlapping pairs (Union-Find over the pairwise-similarity graph).
    Groups of size 1 (nothing similar enough) are dropped from the result.
    Stage AS: rows already marked status='replaced' by a prior heal_memory()
    merge are excluded too — their files are gone from memory/, and
    reconsidering them would re-merge stale, already-archived content.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT source_file, title, content, keywords, embedding FROM memory_index
               WHERE embedding IS NOT NULL AND source_file NOT IN ('MEMORY.md', 'decisions.md')
                     AND status != 'replaced'
               ORDER BY source_file"""
        ).fetchall()

    items = [{key: row[key] for key in row.keys() if key != "embedding"} for row in rows]
    vectors = [np.frombuffer(row["embedding"], dtype=np.float32) for row in rows]

    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _cosine_similarity(vectors[i], vectors[j]) >= threshold:
                union(i, j)

    groups: dict[int, list[dict]] = {}
    for i, item in enumerate(items):
        groups.setdefault(find(i), []).append(item)

    return [group for group in groups.values() if len(group) > 1]


def heal_memory(db_path: Path = DB_PATH) -> dict:
    """Find lesson files ask_ollama judges to be about the same fact and
    queue them as pending merge proposals — never merges anything itself.

    Fable's BUS review (see decisions.md, 2026-08-05) found the previous
    version applied the merge — rewriting, archiving, and marking rows
    'replaced' — the moment Ollama said "yes", with no human check in
    between. That's the exact pattern that cost BBB real data before. Now a
    "yes" verdict only inserts a pending_merges row; the actual file
    rewrite/archive/mark_memory_replaced work happens exclusively inside
    approve_merge(), which must be called by hand for a specific proposal.
    Each group is independent and fail-soft — an unavailable ollama or a
    "no" verdict just leaves that group untouched, and a group already
    queued (a 'pending' row for the same set of files) is skipped without
    asking ollama again, so re-running this daily doesn't spam duplicate
    proposals.
    """

    groups = find_similar_lesson_groups(db_path=db_path)
    proposed = 0

    with contextlib.closing(_connect(db_path)) as conn:
        already_pending = {
            row["source_files"]
            for row in conn.execute(
                "SELECT source_files FROM pending_merges WHERE status = 'pending'"
            ).fetchall()
        }

    for group in groups:
        source_files = sorted(item["source_file"] for item in group)
        key = json.dumps(source_files)
        if key in already_pending:
            continue

        notes_text = "\n".join(f"- {item['title']}: {item['content']}" for item in group)
        verdict = ask_ollama(
            f"these notes:\n{notes_text}\n"
            "are they about the same underlying fact? Answer only yes or no."
        )
        if not verdict or "yes" not in verdict.strip().lower():
            continue

        title = group[0]["title"]
        content = "\n\n".join(item["content"].strip() for item in group)

        with contextlib.closing(_connect(db_path)) as conn:
            conn.execute(
                """INSERT INTO pending_merges (source_files, title, content, status, created_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (key, title, content, _now()),
            )
            conn.commit()
        proposed += 1

    return {"groups_found": len(groups), "merged": proposed}


def approve_merge(merge_id: int, db_path: Path = DB_PATH) -> dict:
    """Apply one pending_merges proposal: this is the only function that
    actually writes a healed file, archives the old ones, and marks the old
    memory_index rows 'replaced'.

    heal_memory() only proposes — nothing changes on disk or in
    memory_index for a group until approve_merge() is called by hand for
    its specific merge_id. Mirrors haroon-bbb-sync's memory/_archived-healed/
    pattern: merged-away notes are moved there, never deleted (see
    decisions.md, 2026-07-27). Raises ValueError if merge_id doesn't exist
    or isn't currently 'pending' (already approved, or unknown id).
    """

    memory_dir = Path(__file__).resolve().parent / "memory"
    archived_dir = memory_dir / "_archived-healed"

    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM pending_merges WHERE id = ?", (merge_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no pending_merges row with id {merge_id}")
        if row["status"] != "pending":
            raise ValueError(
                f"pending_merges row {merge_id} is not pending (status={row['status']!r})"
            )
        source_files = json.loads(row["source_files"])
        title = row["title"]
        content = row["content"]

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "lesson"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = memory_dir / f"lesson-{date}-{slug}-healed.md"
    suffix = 2
    while path.exists():
        path = memory_dir / f"lesson-{date}-{slug}-healed-{suffix}.md"
        suffix += 1
    path.write_text(
        f"---\ntitle: {title}\ndate: {date}\nconfidence: medium\n---\n\n{content}\n",
        encoding="utf-8",
    )

    for source_file in source_files:
        mark_memory_replaced(source_file, path.name, db_path=db_path)

    archived_dir.mkdir(exist_ok=True)
    for source_file in source_files:
        old_path = memory_dir / source_file
        if old_path.exists():
            old_path.rename(archived_dir / source_file)

    index_memory_files(db_path)

    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            "UPDATE pending_merges SET status = 'approved', resolved_at = ? WHERE id = ?",
            (_now(), merge_id),
        )
        conn.commit()

    return {"merged": True, "healed_file": path.name}


def retire_low_confidence_lessons(threshold: float = 0.3, db_path: Path = DB_PATH) -> int:
    """Archive lessons whose confidence is below `threshold` (never delete).

    Stage X: makes confidence a lever, not a dead metric — a lesson that
    keeps scoring low in search eventually gets moved out of memory_index
    entirely. Mirrors heal_memory()'s memory/_archived-healed/ pattern: files
    are moved there, not deleted. MEMORY.md and decisions.md are structural
    index files, not lessons, so they're exempt regardless of confidence.
    """

    memory_dir = Path(__file__).resolve().parent / "memory"
    archived_dir = memory_dir / "_archived-healed"

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT source_file, confidence FROM memory_index
               WHERE source_file NOT IN ('MEMORY.md', 'decisions.md')"""
        ).fetchall()
        retired = [
            row["source_file"]
            for row in rows
            if (row["confidence"] if row["confidence"] is not None else _DEFAULT_CONFIDENCE)
            < threshold
        ]
        for source_file in retired:
            conn.execute(
                "DELETE FROM memory_index WHERE source_file = ?", (source_file,)
            )
        conn.commit()

    if retired:
        archived_dir.mkdir(exist_ok=True)
        for source_file in retired:
            old_path = memory_dir / source_file
            if old_path.exists():
                old_path.rename(archived_dir / source_file)

    return len(retired)


def propose_node(node_type: str, label: str, db_path: Path = DB_PATH) -> str:
    """Add a context-map node into graph_nodes with curated=0.

    A proposed node cannot be linked by add_curated_edge() until
    approve_node() curates it — see decisions.md, Stage AC (formerly
    Stage Z's own context_nodes/context_edges, merged into graph_nodes/
    graph_edges to remove the duplicate schema).
    """

    node_id = _graph_node_id(node_type, label)
    with contextlib.closing(_connect(db_path)) as conn:
        _upsert_graph_node(conn, node_id, node_type, label, source_ref="context-map")
        conn.commit()
    return node_id


def approve_node(node_id: str, db_path: Path = DB_PATH) -> None:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            "UPDATE graph_nodes SET curated = 1, updated_at = ? WHERE node_id = ?",
            (_now(), node_id),
        )
        conn.commit()


def add_curated_edge(
    from_node_id: str, to_node_id: str, relation: str, db_path: Path = DB_PATH
) -> None:
    """Link two curated nodes in graph_edges. Raises ValueError unless both
    nodes are curated=1 in graph_nodes.

    This is the project's most important rule carried over from Stage Z: the
    context map only grows by human-curated approval, never automatically
    (see decisions.md, Stage AC). requires_dual_approval=1 marks the edge as
    having passed this gate, distinguishing it from edges advisory-curated
    after the fact via curate_graph_edge().
    """

    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT node_id, curated FROM graph_nodes WHERE node_id IN (?, ?)",
            (from_node_id, to_node_id),
        ).fetchall()
        curated = {row["node_id"]: bool(row["curated"]) for row in rows}
        if not curated.get(from_node_id) or not curated.get(to_node_id):
            raise ValueError(
                "add_curated_edge requires both nodes to be curated=1 in graph_nodes; "
                "approve_node() them first"
            )
        _upsert_graph_edge(
            conn,
            from_node_id,
            to_node_id,
            relation,
            authority="human-curated",
            currentness="current",
            confidence="verified",
            curated=True,
            requires_dual_approval=True,
        )
        conn.commit()


def list_context_map(only_approved: bool = True, db_path: Path = DB_PATH) -> dict:
    """{"nodes": [...], "edges": [...]} — approved-only by default.

    Backed by graph_nodes/graph_edges since Stage AC (context_nodes/
    context_edges are deprecated, no longer written to). An edge only
    appears here if it was created through add_curated_edge()'s dual-
    approval gate (requires_dual_approval=1), so edges advisory-curated by
    curate_graph_edge() or auto-derived by the memory graph never leak in.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        if only_approved:
            nodes = conn.execute(
                "SELECT * FROM graph_nodes WHERE curated = 1 ORDER BY node_id"
            ).fetchall()
        else:
            nodes = conn.execute("SELECT * FROM graph_nodes ORDER BY node_id").fetchall()
        edges = conn.execute(
            "SELECT * FROM graph_edges WHERE curated = 1 AND requires_dual_approval = 1 "
            "ORDER BY updated_at"
        ).fetchall()
    return {
        "nodes": [dict(row) for row in nodes],
        "edges": [dict(row) for row in edges],
    }


def db_health_check(db_path: Path = DB_PATH) -> dict:
    """Local-only stand-in for a "server unavailable" check (Stage AQ).

    There is no server to ping, so this just checks whether the sqlite
    file itself is reachable (exists and opens for a read).
    """

    reachable = False
    if db_path.exists():
        try:
            with contextlib.closing(_connect(db_path)) as conn:
                conn.execute("SELECT 1")
            reachable = True
        except sqlite3.Error:
            reachable = False
    return {
        "reachable": reachable,
        "mode": "local-only",
        "path": str(db_path),
    }


if __name__ == "__main__":
    init_db()
