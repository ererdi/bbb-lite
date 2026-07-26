"""Stage P: BBB-Lite Assistant Console.

A tiny local chat surface for the assistant itself. The browser never talks to
Claude directly and never sees credentials; this backend calls the local
`claude -p` CLI when it is available/authenticated, then falls back to Ollama.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

HOST = "127.0.0.1"
PORT = 8100
REPO_ROOT = Path(__file__).resolve().parent.parent
CONSOLE_HTML_PATH = Path(__file__).resolve().parent / "assistant_console.html"
DEFAULT_SESSION_ID = "web-assistant"
CLAUDE_TIMEOUT_SECONDS = 120

_ROLE_EN_RE = re.compile(r"\byou are (?:the )?(brozzer|advisor|architect|builder)\b", re.I)
_ROLE_TR_RE = re.compile(r"\b(brozzer|advisor|architect|builder|mimarsın|buildersın|builder'sın)\b", re.I)


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def _clip(text: str, limit: int = 1200) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _declared_role(message: str) -> str | None:
    match = _ROLE_EN_RE.search(message)
    if match:
        return match.group(1).lower()
    match = _ROLE_TR_RE.search(message)
    if not match:
        return None
    raw = match.group(1).lower()
    if raw.startswith("mimar"):
        return "architect"
    if raw.startswith("builder"):
        return "builder"
    return raw


def run_claude(prompt: str, *, timeout: int = CLAUDE_TIMEOUT_SECONDS) -> str | None:
    """Call the user's authenticated Claude Code CLI. Fail-soft to None."""

    claude_path = shutil.which("claude") or "claude"
    flattened = " ".join(prompt.split())
    try:
        result = subprocess.run(
            [claude_path, "-p", flattened],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _safe_call(default, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        return default


def collect_context(message: str, session_id: str, *, db_path: Path = mc.DB_PATH) -> dict:
    mc.init_db(db_path)
    mc.ensure_session(session_id, db_path=db_path)
    declared = _declared_role(message)
    if declared:
        mc.set_role(session_id, declared, db_path=db_path)

    role = mc.get_role(session_id, db_path=db_path)
    keyword_hits = _safe_call([], mc.search_memory, message, limit=3, db_path=db_path)
    semantic_hits = _safe_call([], mc.semantic_search, message, limit=3, db_path=db_path)
    tasks = _safe_call([], mc.list_tasks, db_path=db_path)
    open_tasks = [task for task in tasks if task.get("status") != "done"][:5]

    return {
        "session_id": session_id,
        "role": role,
        "permissions": mc.ROLE_PERMISSIONS.get(role, {}),
        "declared_role": declared,
        "checkpoint": _safe_call(None, mc.latest_checkpoint, session_id, db_path=db_path),
        "keyword_memory": keyword_hits,
        "semantic_memory": semantic_hits,
        "open_tasks": open_tasks,
    }


def _memory_lines(items: list[dict]) -> str:
    lines = []
    for item in items:
        title = item.get("title") or item.get("source_file") or "memory"
        content = _clip(item.get("content", ""), 280)
        similarity = item.get("similarity")
        suffix = f" similarity={similarity:.3f}" if isinstance(similarity, float) else ""
        lines.append(f"- {title}{suffix}: {content}")
    return "\n".join(lines) or "- none"


def build_prompt(message: str, context: dict) -> str:
    open_tasks = "\n".join(
        f"- #{task.get('id')} [{task.get('status')}] {task.get('title')}"
        for task in context["open_tasks"]
    ) or "- none"
    checkpoint = context["checkpoint"] or {}
    checkpoint_line = _clip(json.dumps(checkpoint, ensure_ascii=False), 500) if checkpoint else "none"

    return f"""
You are BBB-Lite, Erdi's local assistant prototype inspired by Haroon's BBB OS.
Answer in the user's language. Be concise, concrete, and honest.
You run locally: SQLite Mission Control, role permissions, lifecycle checkpoints,
keyword memory search, and semantic memory search. You do not have HOSNI, voice,
Telegram, CRM, or production bridge powers. If the provided context is insufficient,
say what is unknown instead of inventing.

Session: {context['session_id']}
Role: {context['role']}
Permissions: {context['permissions']}
Latest checkpoint: {checkpoint_line}

Open tasks:
{open_tasks}

Keyword memory:
{_memory_lines(context['keyword_memory'])}

Semantic memory:
{_memory_lines(context['semantic_memory'])}

User message:
{message}
""".strip()


def answer_message(message: str, session_id: str = DEFAULT_SESSION_ID, *, db_path: Path = mc.DB_PATH) -> dict:
    message = str(message or "").strip()
    if not message:
        return {"error": "message is required"}

    context = collect_context(message, session_id, db_path=db_path)
    prompt = build_prompt(message, context)
    answer = run_claude(prompt)
    engine = "claude-cli"
    if answer is None:
        answer = mc.ask_ollama(prompt)
        engine = "ollama"
    if answer is None:
        answer = (
            "Cevap motoruna ulaşamadım. Claude CLI oturum açmamış/limitli olabilir; "
            "Ollama da yanıt vermedi. Yine de Mission Control, role, memory ve "
            "checkpoint context'i toplandı."
        )
        engine = "unavailable"

    _safe_call(
        None,
        mc.write_checkpoint,
        session_id,
        current_task=_clip(message, 160),
        last_decision="assistant_console_reply",
        next_action=_clip(answer, 180),
        risk="low" if engine != "unavailable" else "model_unavailable",
        db_path=db_path,
    )

    return {
        "id": uuid.uuid4().hex,
        "answer": answer,
        "engine": engine,
        "context": _json_safe(context),
    }


def build_state(session_id: str = DEFAULT_SESSION_ID, *, db_path: Path = mc.DB_PATH) -> dict:
    mc.init_db(db_path)
    mc.ensure_session(session_id, db_path=db_path)
    role = mc.get_role(session_id, db_path=db_path)
    tasks = _safe_call([], mc.list_tasks, db_path=db_path)
    return {
        "session_id": session_id,
        "role": role,
        "permissions": mc.ROLE_PERMISSIONS.get(role, {}),
        "open_tasks": sum(1 for task in tasks if task.get("status") != "done"),
        "done_tasks": sum(1 for task in tasks if task.get("status") == "done"),
        "checkpoint": _safe_call(None, mc.latest_checkpoint, session_id, db_path=db_path),
        "claude_cli_available": shutil.which("claude") is not None,
    }


class AssistantRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(_json_safe(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/state"):
            self._send_json(200, build_state())
            return
        if self.path in ("/", "/assistant_console.html"):
            try:
                self._send_html(200, CONSOLE_HTML_PATH.read_bytes())
            except OSError as exc:
                self._send_json(500, {"error": str(exc)})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            session_id = str(payload.get("session_id") or DEFAULT_SESSION_ID)
            data = answer_message(str(payload.get("message") or ""), session_id)
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})
            return
        status = 400 if "error" in data else 200
        self._send_json(status, data)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"[assistant-server] {format % args}\n")


def main() -> None:
    mc.init_db()
    server = ThreadingHTTPServer((HOST, PORT), AssistantRequestHandler)
    print(f"[assistant-server] listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
