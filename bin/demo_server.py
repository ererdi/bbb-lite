"""Stage O: BBB-Lite Demo Console — standard library only, a SEPARATE server/port
from canvas_server.py (Codex's suggestion): canvas stays single-purpose
(session-task relationship visualization), this one is status/evidence —
Mission Control counts, session lifecycle, roles, memory search, daily health,
a Canvas link, and test evidence. Fail-soft per section like
canvas_server.py/daily_check.py: one section erroring must never sink the rest
of the response.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

HOST = "127.0.0.1"
PORT = 8090
REPO_ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = REPO_ROOT / "memory"
CONSOLE_HTML_PATH = Path(__file__).resolve().parent / "demo_console.html"
CANVAS_LINK = "http://localhost:5173"

_memory_example_cache: dict | None = None


def _latest_session_id(db_path: Path = mc.DB_PATH) -> str | None:
    """The most recently started session, per list_sessions()'s started_at ordering."""

    sessions = mc.list_sessions(db_path=db_path)
    return sessions[-1]["session_id"] if sessions else None


def _build_mission_control(db_path: Path = mc.DB_PATH) -> dict:
    tasks = mc.list_tasks(db_path=db_path)
    return {
        "project_count": len(mc.list_projects(db_path=db_path)),
        "sprint_count": len(mc.list_sprints(db_path=db_path)),
        "task_count_open": sum(1 for task in tasks if task["status"] != "done"),
        "task_count_done": sum(1 for task in tasks if task["status"] == "done"),
        "guard_note": "tasks.status='done' is refused outside complete_task() - guard-enforced.",
    }


def _build_lifecycle(db_path: Path = mc.DB_PATH) -> dict:
    session_id = _latest_session_id(db_path)
    if session_id is None:
        return {"session_id": None, "checkpoint": None, "score": None}
    return {
        "session_id": session_id,
        "checkpoint": mc.latest_checkpoint(session_id, db_path=db_path),
        "score": mc.session_score(session_id, db_path=db_path),
    }


def _build_roles(db_path: Path = mc.DB_PATH) -> dict:
    session_id = _latest_session_id(db_path)
    return {
        "role_permissions": mc.ROLE_PERMISSIONS,
        "latest_session_role": mc.get_role(session_id, db_path=db_path) if session_id else None,
    }


def _memory_search_example(db_path: Path = mc.DB_PATH) -> dict:
    """search_memory()/semantic_search() for a fixed "guard" query, cached for the
    process lifetime. semantic_search() loads an embedding model — too expensive
    to redo on every console refresh, so this computes it once and reuses it.
    """

    global _memory_example_cache
    if _memory_example_cache is None:
        keyword_hits = mc.search_memory("guard", limit=1, db_path=db_path)
        semantic_hits = mc.semantic_search("guard", limit=1, db_path=db_path)
        _memory_example_cache = {
            "query": "guard",
            "keyword_result": keyword_hits[0] if keyword_hits else None,
            "semantic_result": semantic_hits[0] if semantic_hits else None,
        }
    return _memory_example_cache


def _build_memory(db_path: Path = mc.DB_PATH) -> dict:
    return {
        "file_count": len(list(MEMORY_DIR.glob("*.md"))),
        "search_example": _memory_search_example(db_path),
    }


def _build_daily_health(db_path: Path = mc.DB_PATH) -> dict:
    return {"findings": mc.check_floor(db_path=db_path)}


_UNITTEST_TOTAL_RE = re.compile(r"Ran (\d+) tests?")
_UNITTEST_FAILED_RE = re.compile(r"FAILED \(([^)]*)\)")


def _parse_unittest_output(output: str) -> dict:
    """Passed/total from unittest's plain-text summary (stdout+stderr combined).

    unittest has no direct "passed" count, only total ran + failures/errors,
    so passed is derived as total - failures - errors.
    """

    total_match = _UNITTEST_TOTAL_RE.search(output)
    total = int(total_match.group(1)) if total_match else 0
    failures = errors = 0
    failed_match = _UNITTEST_FAILED_RE.search(output)
    if failed_match:
        for part in failed_match.group(1).split(","):
            count_match = re.match(r"\s*(failures|errors)=(\d+)", part)
            if count_match:
                kind, count = count_match.group(1), int(count_match.group(2))
                if kind == "failures":
                    failures = count
                else:
                    errors = count
    return {"passed": total - failures - errors, "total": total}


def _build_evidence() -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover"],
            cwd=REPO_ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return {"tests": "unavailable"}
    counts = _parse_unittest_output(result.stdout + result.stderr)
    if counts["total"] == 0:
        return {"tests": "unavailable"}
    return {"passed": counts["passed"], "total": counts["total"], "ok": result.returncode == 0}


def build_demo_data(db_path: Path = mc.DB_PATH) -> dict:
    """Assemble all 7 demo-console sections; each section fails soft on its own."""

    builders = {
        "mission_control": lambda: _build_mission_control(db_path),
        "lifecycle": lambda: _build_lifecycle(db_path),
        "roles": lambda: _build_roles(db_path),
        "memory": lambda: _build_memory(db_path),
        "daily_health": lambda: _build_daily_health(db_path),
        "canvas_link": lambda: CANVAS_LINK,
        "evidence": _build_evidence,
    }
    data: dict = {}
    for name, build in builders.items():
        try:
            data[name] = build()
        except Exception as exc:  # fail-soft: one section's error must not sink the rest
            data[name] = {"error": str(exc)}
    return data


class DemoRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
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

    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/api/demo-data":
            try:
                data = build_demo_data()
            except Exception as exc:  # fail-soft: db unreachable, keep the server up
                self._send_json(500, {"error": str(exc)})
                return
            self._send_json(200, data)
            return
        if self.path in ("/", "/demo_console.html"):
            try:
                body = CONSOLE_HTML_PATH.read_bytes()
            except OSError as exc:
                self._send_json(500, {"error": str(exc)})
                return
            self._send_html(200, body)
            return
        self._send_json(404, {"error": "not found"})

    def log_message(self, format: str, *args) -> None:  # quieter default logging
        sys.stderr.write(f"[demo-server] {format % args}\n")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), DemoRequestHandler)
    print(f"[demo-server] listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
