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
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

DB_PATH = Path(__file__).resolve().parent / "mc.db"
_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
_embedding_model: Any = None


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
    session_id: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Create a task. status='done' is refused here — use complete_task().

    If session_id is given, records a 'created' row in task_activity so
    tasks_touched_by_session() can later find it.
    """

    with contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, status, _now(), _now()),
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

    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    return _embedding_model


def _embed(text: str) -> bytes:
    """Encode text into a vector using the cached model, returned as raw float32 bytes."""

    vector = _get_embedding_model().encode(text, convert_to_numpy=True)
    return np.asarray(vector, dtype=np.float32).tobytes()


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
            embedding = _embed(f"{title}\n{body}")
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
