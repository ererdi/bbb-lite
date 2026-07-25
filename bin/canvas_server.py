"""Stage H: local JSON API feeding the tldraw canvas — standard library only.

Mirrors haroon-bbb-sync's canvas idea but with a local http.server instead of
HOSNI: one endpoint, one purpose, no auth (localhost-only, single local user).
Fail-soft like daily_check.py/dispatch.py — if mc.db can't be read, respond
500 rather than let the process die, since a canvas refresh is best-effort.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

HOST = "127.0.0.1"
PORT = 8080


def build_canvas_data(db_path: Path = mc.DB_PATH) -> dict:
    """Sessions, tasks (open + done), and session-task links for the canvas."""

    return {
        "sessions": mc.list_sessions(db_path=db_path),
        "tasks": mc.list_tasks(db_path=db_path),
        "links": mc.list_task_activity(db_path=db_path),
    }


class CanvasRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
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
        if self.path != "/api/canvas-data":
            self._send_json(404, {"error": "not found"})
            return
        try:
            data = build_canvas_data()
        except Exception as exc:  # fail-soft: db unreachable, keep the server up
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, data)

    def log_message(self, format: str, *args) -> None:  # quieter default logging
        sys.stderr.write(f"[canvas-server] {format % args}\n")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), CanvasRequestHandler)
    print(f"[canvas-server] listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
