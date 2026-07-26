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
_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
_embedding_model: Any = None
_embedding_unavailable = False
GRAPH_EDGE_TYPES = ("supersedes", "depends-on", "evidence-for", "contradicts", "derived-from")
GRAPH_MAX_HOPS = 2

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


def init_db(db_path: Path = DB_PATH) -> None:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                status TEXT NOT NULL
            )"""
        )
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
        conn.execute("CREATE INDEX IF NOT EXISTS graph_edges_src ON graph_edges(src, type)")
        conn.execute("CREATE INDEX IF NOT EXISTS graph_edges_dst ON graph_edges(dst, type)")
        conn.execute("CREATE INDEX IF NOT EXISTS graph_nodes_type ON graph_nodes(node_type)")
        for capability, reason in DEFAULT_CAPABILITY_BOUNDARIES.items():
            conn.execute(
                """INSERT OR IGNORE INTO capability_boundaries
                   (capability, enabled, reason, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (capability, 0, reason, _now()),
            )
        conn.commit()


def ensure_session(session_id: str, db_path: Path = DB_PATH) -> str:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO sessions (session_id, started_at, status) VALUES (?, ?, ?)",
                (session_id, _now(), "active"),
            )
            conn.commit()
    return session_id


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
    session_id: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Create a task. status='done' is refused here — use complete_task().

    sprint_id is optional and defaults to None (backward compatible with
    tasks created before Stage L's projects/sprints hierarchy existed).
    If session_id is given, records a 'created' row in task_activity so
    tasks_touched_by_session() can later find it.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, status, created_at, updated_at, sprint_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, status, _now(), _now(), sprint_id),
        )
        task_id = cur.lastrowid
        if session_id is not None:
            conn.execute(
                "INSERT INTO task_activity (task_id, session_id, activity, at) VALUES (?, ?, ?, ?)",
                (task_id, session_id, "created", _now()),
            )
        conn.commit()
        return task_id


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
    }


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
) -> None:
    src, dst, edge_type = src.strip(), dst.strip(), edge_type.strip()
    if not src or not dst or not edge_type:
        raise ValueError("graph edge needs non-empty src, dst, and type")
    authority_class = _authority_class_for_edge(edge_type, authority)
    conn.execute(
        """INSERT INTO graph_edges
           (src, dst, type, evidence, weight, source_ref, authority, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(src, dst, type) DO UPDATE SET
               evidence = excluded.evidence,
               weight = excluded.weight,
               source_ref = excluded.source_ref,
               authority = excluded.authority,
               updated_at = excluded.updated_at""",
        (src, dst, edge_type, evidence, float(weight), source_ref, authority_class, _now()),
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


def graph_stats(db_path: Path = DB_PATH) -> dict:
    with contextlib.closing(_connect(db_path)) as conn:
        node_count = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
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
    return {
        "nodes": node_count,
        "edges": edge_count,
        "by_type": by_type,
        "by_authority": by_authority,
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
                f"SELECT {want} AS node, type, evidence, weight, source_ref, authority, updated_at "
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


def graph_memory_search(query: str, *, limit: int = 3, hops: int = 1, db_path: Path = DB_PATH) -> list[dict]:
    """Recall memory hits plus their graph neighborhood."""

    hits = search_memory(query, limit=limit, db_path=db_path)
    if len(hits) < limit:
        with contextlib.suppress(Exception):
            for hit in semantic_search(query, limit=limit, db_path=db_path):
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


def session_score(session_id: str, db_path: Path = DB_PATH) -> dict:
    """Single-axis session score: task completion rate * 100, plus checkpoint count.

    Not Haroon's 3-axis context/exec/rules score — bbb-lite has no context-budget
    or rule-tracking infrastructure to feed those, so this is the one axis our
    schema actually supports (see decisions.md, 2026-07-24).
    """

    touched = tasks_touched_by_session(session_id, db_path=db_path)
    tasks_touched = len(touched)
    tasks_completed = sum(1 for task in touched if task["completed_by_session"])
    completion_rate = tasks_completed / tasks_touched if tasks_touched else 1.0

    with contextlib.closing(_connect(db_path)) as conn:
        checkpoints_written = conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE session_id = ?", (session_id,)
        ).fetchone()[0]

    return {
        "session_id": session_id,
        "tasks_touched": tasks_touched,
        "tasks_completed": tasks_completed,
        "checkpoints_written": checkpoints_written,
        "completion_rate": completion_rate,
        "score": round(completion_rate * 100),
    }


def complete_task(
    task_id: int, *, session_id: str | None = None, db_path: Path = DB_PATH
) -> dict:
    """The one sanctioned path to tasks.status='done'; raw SQL is refused by the guard.

    If session_id is given, records a 'completed' row in task_activity.
    """

    authorization = _CompletionAuthorization(task_id=str(task_id))
    now = _now()
    with contextlib.closing(_connect(db_path)) as conn:
        task = conn.execute("SELECT id, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise MCCompletionRefused(f"completion refused: task not found: {task_id}")
        if task["status"] == "done":
            raise MCCompletionRefused(f"completion refused: task already done: {task_id}")
        updated = conn.execute(
            "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ? AND status != 'done'",
            (now, task_id),
            _completion_authorization=authorization,
        )
        if updated.rowcount != 1:
            raise MCCompletionRefused(
                f"completion refused: guarded update changed {updated.rowcount} rows"
            )
        if session_id is not None:
            conn.execute(
                "INSERT INTO task_activity (task_id, session_id, activity, at) VALUES (?, ?, ?, ?)",
                (task_id, session_id, "completed", now),
            )
        conn.commit()
    return {"ok": True, "task_id": task_id, "status": "done"}


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


def _parse_memory_file(path: Path) -> tuple[str, str]:
    """Return (title, body) — title from frontmatter if present, else the filename."""

    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        return path.stem, text.strip()
    frontmatter, body = match.group(1), match.group(2)
    title_match = re.search(r"^title:\s*(.+)$", frontmatter, re.M)
    title = title_match.group(1).strip() if title_match else path.stem
    return title, body.strip()


def _get_embedding_model() -> Any:
    """Load the sentence-transformers model once and cache it for the process lifetime.

    Not offline-first: the very first call on a machine downloads the model from the
    Hugging Face Hub and raises if there's no network. Once Hugging Face's local cache
    (~/.cache/huggingface) has the model, later calls load from disk and need no network.
    """

    global _embedding_model, _embedding_unavailable
    if _embedding_unavailable:
        raise RuntimeError("embedding model unavailable in this process")
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        try:
            _embedding_model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
        except Exception:
            _embedding_unavailable = True
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
    """Upsert every memory/*.md file into memory_index. Re-running re-indexes, never duplicates."""

    memory_dir = Path(__file__).resolve().parent / "memory"
    count = 0
    with contextlib.closing(_connect(db_path)) as conn:
        for path in sorted(memory_dir.glob("*.md")):
            title, body = _parse_memory_file(path)
            keywords = _extract_keywords(f"{title} {body}")
            embedding = _embed_or_none(f"{title}\n{body}")
            conn.execute(
                """INSERT INTO memory_index (source_file, title, content, keywords, embedding)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source_file) DO UPDATE SET
                       title = excluded.title,
                       content = excluded.content,
                       keywords = excluded.keywords,
                       embedding = excluded.embedding""",
                (path.name, title, body, keywords, embedding),
            )
            _upsert_memory_graph(conn, path.name, title, body, keywords)
            count += 1
        conn.commit()
    return count


def search_memory(query: str, *, limit: int = 3, db_path: Path = DB_PATH) -> list[dict]:
    """Keyword search over memory_index — never reads memory/*.md directly.

    Mirrors the "recall comes from the index, not the raw docs" split: this
    is the search layer, backed by whatever index_memory_files() last wrote.
    Not semantic search — weighted keyword-count ranking: a term counted in
    title is worth 3 points, in keywords 2 points, in content 1 point.
    """

    terms = list(dict.fromkeys(_significant_words(query)))
    if not terms:
        return []
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT source_file, title, content, keywords FROM memory_index"
        ).fetchall()
    scored: list[tuple[int, dict]] = []
    for row in rows:
        title = row["title"].lower()
        keywords = row["keywords"].lower()
        content = row["content"].lower()
        score = sum(
            3 * title.count(term) + 2 * keywords.count(term) + content.count(term)
            for term in terms
        )
        if score > 0:
            scored.append((score, dict(row)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored[:limit]]


def semantic_search(query: str, *, limit: int = 3, db_path: Path = DB_PATH) -> list[dict]:
    """Embedding-based search over memory_index — finds meaning matches with no shared words.

    Complements search_memory() (keyword ranking); this ranks by cosine similarity
    between the query's embedding and each row's stored embedding.
    """

    query_vector = np.frombuffer(_embed(query), dtype=np.float32)
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT source_file, title, content, keywords, embedding FROM memory_index "
            "WHERE embedding IS NOT NULL"
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


def ask_ollama(prompt: str, model: str = "gemma:2b") -> str | None:
    """Fail-soft call to a local ollama model; None (never raises) if it's unavailable.

    Mirrors the MC-unreachable-never-breaks-the-session principle: if ollama isn't
    installed, isn't running, or just times out, callers fall back to their own
    default behavior instead of crashing.
    """

    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


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
    """Write one lesson file to memory/ (one fact per file) — unless ask_ollama() judges it
    a near-duplicate of the closest existing lesson found via search_memory(). Filesystem +
    memory_index only, never any other DB table.

    Returns the new file's Path, or None if it was skipped as a likely duplicate.
    """

    memory_dir = Path(__file__).resolve().parent / "memory"
    memory_dir.mkdir(exist_ok=True)

    index_memory_files(db_path)
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


if __name__ == "__main__":
    init_db()
