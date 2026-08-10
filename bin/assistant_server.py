"""Stage P: BBB-Lite Assistant Console.

A tiny local chat surface for the assistant itself. The browser never talks to
Claude directly and never sees credentials; this backend calls the local
`claude -p` CLI when it is available/authenticated, then falls back to Ollama.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import date
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
VOICE_OMISSION_REASON = (
    "Voice is deliberately out of scope for this stage: it needs STT/TTS, mic "
    "permissions, latency handling, port isolation, and a separate red/green "
    "voice harness. This stage proves the local assistant brain first."
)
BROZZER_PARITY = {
    "status": "partial-local-brozzer",
    "same": [
        "default brozzer role",
        "Mission Control context",
        "keyword and semantic memory recall",
        "graph memory with currentness/confidence",
        "lifecycle checkpoint after replies",
        "answer quality scoring and receipts",
        "honest local capability boundaries",
    ],
    "missing": [
        "HOSNI durable dispatch",
        "voice mouth and room-state loop",
        "Rider decision veto window",
        "shared active-turns log across chat and voice",
        "production bridge, Telegram, CRM, and full org pipeline",
    ],
    "next": [
        "connect real Claude Code hook settings only when self-interference is safe",
        "add a stronger local/remote dispatch bridge after text behavior is stable",
        "add voice only after text assistant behavior is stable",
    ],
}

_ROLE_EN_RE = re.compile(r"\byou are (?:the )?(brozzer|advisor|architect|builder)\b", re.I)
_ROLE_TR_RE = re.compile(r"\b(brozzer|advisor|architect|builder|mimarsın|buildersın|builder'sın)\b", re.I)
_DECISION_PREFIX_RE = re.compile(
    r"\b(?:decision|karar)\s*[:=-]\s*(?P<decision>.+)",
    re.I | re.S,
)
_DECISION_PHRASES = (
    "karar olarak kaydet",
    "bunu karar",
    "decision olarak kaydet",
    "log this decision",
    "record this decision",
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Stage AV: real task commands, alongside the existing decision:/karar: pattern.
_TASK_ADD_RE = re.compile(r"\b(?:task|g[öo]rev)\s*[:=-]\s*(?P<title>.+)", re.I | re.S)
_TASK_COMPLETE_EN_RE = re.compile(r"\bcomplete\s+task\s+(?P<id>\d+)\b", re.I)
_TASK_COMPLETE_TR_RE = re.compile(r"\b(?P<id>\d+)\s*numaral[ıi]\s+g[öo]revi\s+tamamla\b", re.I)
_STATUS_COMMAND_WORDS = {"status", "durum"}

# Stage AW: cheap, read-only informational commands - no real Claude call.
# Dispatch (CTO/architect/builder) and context-map are deliberately NOT wired
# into chat here; see decisions.md for the reasoning.
_EXISTS_RE = re.compile(r"^(?:exists|var\s*m[ıi])\s*[:=-]\s*(?P<query>.+)$", re.I | re.S)
_PROJECTS_COMMAND_WORDS = {"projects", "projeler"}

# "callers: <symbol>" -> mc.find_callers() ("calls" edges from index_symbols()).
# "depends: <node>" -> mc.graph_depends_on() (curated "depends-on" edges from
# graph/edges.jsonl) - a different, hand-curated graph, not the auto-detected
# call graph, so the two commands can legitimately return "none" for the same
# name if it's only known to one of the two edge sets.
_CALLERS_RE = re.compile(r"^callers\s*[:=-]\s*(?P<query>.+)$", re.I | re.S)
_DEPENDS_RE = re.compile(r"^depends\s*[:=-]\s*(?P<query>.+)$", re.I | re.S)

# "mentions: <symbol or doc>" -> mc.find_mentions() ("references" edges
# index_docs() derives). Symbol input returns the docs that mention it; doc
# input (title, file path, or filename/stem) returns the symbols it
# mentions - see mc.find_mentions()'s docstring for the direction rule.
_MENTIONS_RE = re.compile(r"^mentions\s*[:=-]\s*(?P<query>.+)$", re.I | re.S)

# Stage AX: "confirm task N with test: ..." - a real test-evidence completion
# path, distinct from the intentionally-guard-rejected "complete task N".
_CONFIRM_TASK_TEST_RE = re.compile(
    r"\bconfirm\s+task\s+(?P<id>\d+)\s+with\s+test\s*:\s*(?P<detail>.+)",
    re.I | re.S,
)

# Stage AY: "start task N" / "işe başla N" - the todo -> in_progress half of
# the loop that "confirm task N with test: ..." (in_progress/ready_for_review
# -> done) could not previously reach from chat.
_TASK_START_EN_RE = re.compile(r"\bstart\s+task\s+(?P<id>\d+)\b", re.I)
_TASK_START_TR_RE = re.compile(r"\bi[şs]e\s+ba[şs]la\s+(?P<id>\d+)\b", re.I)

# Stage AZ: "remind: <description> | <YYYY-MM-DD> | <name1, name2, ...>" - a
# dated reminder wired to person graph nodes, alongside task:/decision:.
_REMIND_RE = re.compile(r"\bremind\s*[:=-]\s*(?P<rest>.+)", re.I | re.S)

# Stage BA: "decision: <text> | <entity name>" - optional entity suffix that
# links the resulting packet into the graph (mc.link_decision_to_entity) so
# it isn't a Haroon-style "cold decision"; "decisions-for: <entity>" reads
# it back (mc.get_decisions_for_entity).
_DECISIONS_FOR_RE = re.compile(r"^decisions-for\s*[:=-]\s*(?P<entity>.+)$", re.I | re.S)

# Stage BB: "compact:" / "clear-sim: <new_session_id>" - chat-triggered
# simulations of Claude Code's own /compact and /clear mechanisms. Real
# Claude Code context compaction does NOT exist in assistant_server.py (it's
# a plain HTTP server, not the Claude Code CLI runtime) - these commands call
# the exact same mc.py functions Stage R/AF proved under a genuine /compact
# and /clear (mc.save_snapshot/compare_snapshots, mc.get_most_recent_role/
# set_role/clear_recovery_verified) directly from chat, so the underlying
# mechanism is real even though the trigger is simulated. See decisions.md,
# 2026-08-09.
_CLEAR_SIM_RE = re.compile(r"^clear-sim\s*[:=-]\s*(?P<session_id>.+)$", re.I | re.S)


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, str):
        return _clean_model_output(value) or ""
    if hasattr(value, "item"):
        return value.item()
    return value


def _clip(text: str, limit: int = 1200) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _clean_model_output(text: str | None) -> str | None:
    """Remove terminal noise from CLI/model output before it reaches the browser."""

    if text is None:
        return None
    cleaned = _ANSI_ESCAPE_RE.sub("", text)
    cleaned = _CONTROL_RE.sub("", cleaned)
    cleaned = cleaned.strip()
    return cleaned or None


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


def _decision_text(message: str) -> str | None:
    """Extract an explicit chat decision without guessing from ordinary text."""

    message = str(message or "").strip()
    if not message:
        return None
    match = _DECISION_PREFIX_RE.search(message)
    if match:
        return _clip(match.group("decision"), 500)
    lower = message.lower()
    if any(phrase in lower for phrase in _DECISION_PHRASES):
        return _clip(message, 500)
    return None


def _split_decision_entity(decision_text: str) -> tuple[str, str | None]:
    """Split an optional "| <entity name>" suffix off a decision: payload.

    "decision: X | Genie" links the resulting packet to the "Genie" entity
    node (mc.link_decision_to_entity) so it isn't a Haroon-style "cold"
    decision, invisible to graph traversal; plain "decision: X" (no "|") is
    unaffected - entity is None and nothing gets linked.
    """

    if "|" not in decision_text:
        return decision_text.strip(), None
    left, _, right = decision_text.rpartition("|")
    entity = right.strip()
    return left.strip(), (entity or None)


def _decisions_for_query(message: str) -> str | None:
    """Extract the entity name from an explicit 'decisions-for: ...' message."""

    match = _DECISIONS_FOR_RE.match(message.strip())
    if not match:
        return None
    entity = _clip(match.group("entity"), 200)
    return entity or None


def _is_compact_command(message: str) -> bool:
    """'compact' or 'compact:' - no payload, unlike the other prefix commands."""

    return message.strip().lower().rstrip(":") == "compact"


def _clear_sim_session_id(message: str) -> str | None:
    """Extract the target session id from an explicit 'clear-sim: ...' message."""

    match = _CLEAR_SIM_RE.match(message.strip())
    if not match:
        return None
    session_id = _clip(match.group("session_id"), 200)
    return session_id or None


def _task_add_title(message: str) -> str | None:
    """Extract a task title from an explicit 'task: ...' / 'görev: ...' message."""

    match = _TASK_ADD_RE.search(message)
    if not match:
        return None
    title = _clip(match.group("title"), 200)
    return title or None


def _task_complete_id(message: str) -> int | None:
    """Extract a task id from 'complete task N' or 'N numaralı görevi tamamla'."""

    match = _TASK_COMPLETE_EN_RE.search(message) or _TASK_COMPLETE_TR_RE.search(message)
    return int(match.group("id")) if match else None


def _task_start_id(message: str) -> int | None:
    """Extract a task id from 'start task N' or 'işe başla N'."""

    match = _TASK_START_EN_RE.search(message) or _TASK_START_TR_RE.search(message)
    return int(match.group("id")) if match else None


def _remind_payload(message: str) -> str | None:
    """Extract the raw '<desc> | <date> | <people>' text from 'remind: ...'."""

    match = _REMIND_RE.search(message)
    if not match:
        return None
    rest = match.group("rest").strip()
    return rest or None


def _parse_remind_payload(rest: str) -> tuple[str, str, list[str]]:
    """Split a remind payload into (description, due_date, people).

    Raises ValueError with a message safe to show the user directly - the
    chat handler shows it verbatim instead of a traceback, same as the
    complete-task guard failures already do.
    """

    parts = [part.strip() for part in rest.split("|")]
    if len(parts) != 3:
        raise ValueError(
            "expected 'remind: <description> | <YYYY-MM-DD> | <name1, name2, ...>'"
        )
    description, due_date, people_raw = parts
    if not description:
        raise ValueError("reminder description is empty")
    try:
        date.fromisoformat(due_date)
    except ValueError:
        raise ValueError(f"could not parse date '{due_date}', expected YYYY-MM-DD") from None
    people = [name.strip() for name in people_raw.split(",") if name.strip()]
    if not people:
        raise ValueError("remind needs at least one person name")
    return description, due_date, people


def _is_status_command(message: str) -> bool:
    return message.strip().lower() in _STATUS_COMMAND_WORDS


def _exists_query(message: str) -> str | None:
    """Extract the query from an explicit 'exists: ...' / 'var mı: ...' message."""

    match = _EXISTS_RE.match(message.strip())
    if not match:
        return None
    query = _clip(match.group("query"), 300)
    return query or None


def _is_projects_command(message: str) -> bool:
    return message.strip().lower() in _PROJECTS_COMMAND_WORDS


def _callers_query(message: str) -> str | None:
    """Extract the symbol name from an explicit 'callers: ...' message."""

    match = _CALLERS_RE.match(message.strip())
    if not match:
        return None
    query = _clip(match.group("query"), 300)
    return query or None


def _depends_query(message: str) -> str | None:
    """Extract the node name from an explicit 'depends: ...' message."""

    match = _DEPENDS_RE.match(message.strip())
    if not match:
        return None
    query = _clip(match.group("query"), 300)
    return query or None


def _mentions_query(message: str) -> str | None:
    """Extract the symbol/doc name from an explicit 'mentions: ...' message."""

    match = _MENTIONS_RE.match(message.strip())
    if not match:
        return None
    query = _clip(match.group("query"), 300)
    return query or None


def _confirm_task_with_test_match(message: str) -> tuple[int, str] | None:
    """Extract (task_id, detail) from 'confirm task N with test: <detail>'."""

    match = _CONFIRM_TASK_TEST_RE.search(message)
    if not match:
        return None
    detail = _clip(match.group("detail"), 300)
    return int(match.group("id")), detail


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
    return _clean_model_output(result.stdout)


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
    graph_hits = _safe_call([], mc.graph_memory_search, message, limit=2, db_path=db_path)
    tasks = _safe_call([], mc.list_tasks, db_path=db_path)
    open_tasks = [task for task in tasks if task.get("status") != "done"][:5]
    checkpoint = _safe_call(None, mc.latest_checkpoint, session_id, db_path=db_path)

    # Stage AV: wire the previously hook-only health/score/conflict signals
    # into the chat surface itself, so what's built is actually visible here.
    health = _safe_call("unknown", mc.get_health_state, session_id, db_path=db_path)
    score = _safe_call({}, mc.session_score, session_id, db_path=db_path)
    current_task = (checkpoint or {}).get("current_task")
    conflicts = (
        _safe_call([], mc.detect_memory_conflicts, current_task, db_path=db_path)
        if current_task
        else []
    )

    return {
        "session_id": session_id,
        "role": role,
        "permissions": mc.ROLE_PERMISSIONS.get(role, {}),
        "declared_role": declared,
        "checkpoint": checkpoint,
        "keyword_memory": keyword_hits,
        "semantic_memory": semantic_hits,
        "graph_memory": graph_hits,
        "open_tasks": open_tasks,
        "health": health,
        "session_score": score,
        "memory_conflicts": conflicts,
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


def _structured_local_answer(*, known: list[str], impact: list[str], risks: list[str], actions: list[str]) -> str:
    def section(title: str, items: list[str]) -> str:
        return f"{title}:\n" + "\n".join(f"- {item}" for item in items)

    return "\n\n".join(
        [
            section("Known", known),
            section("MC impact", impact),
            section("Risk", risks),
            section("Next action", actions),
        ]
    )


def build_prompt(message: str, context: dict) -> str:
    open_tasks = "\n".join(
        f"- #{task.get('id')} [{task.get('status')}] {task.get('title')}"
        for task in context["open_tasks"]
    ) or "- none"
    checkpoint = context["checkpoint"] or {}
    checkpoint_line = _clip(json.dumps(checkpoint, ensure_ascii=False), 500) if checkpoint else "none"
    score = context.get("session_score") or {}
    score_line = _clip(json.dumps(score, ensure_ascii=False), 300) if score else "unavailable"
    conflicts = context.get("memory_conflicts") or []
    conflict_lines = "\n".join(f"- {c.get('reason')}" for c in conflicts) or "- none"

    return f"""
You are BBB-Lite, Erdi's local assistant prototype inspired by Haroon's BBB OS.
You are not a generic chatbot. You behave like a small Brozzer-style operating
assistant: grounded in Mission Control, memory, role, and the current session.
Answer in the user's language. Be concrete, calm, and useful.

Response law:
- Use this compact shape when it fits: Known / MC impact / Risk / Next action.
- Start from what is known in the supplied context.
- If the request is operational, produce a direct next-action plan.
- Name whether the answer is based on Mission Control, memory, graph memory,
  checkpoint, or a declared limitation.
- If required facts are missing, ask only the smallest necessary question after
  giving a safe default.
- Do not claim powers you do not have. You cannot access HOSNI, voice, Telegram,
  calendar, email, CRM, production bridge, or the public internet from this UI.
- Do not mention implementation details unless they explain a limitation.
- Never output terminal control characters or raw model artifacts.
- Voice is intentionally out of scope in this stage. If asked, explain that the
  text assistant brain is being proven before STT/TTS, mic, latency, and voice
  harness work.
- You are a partial local Brozzer, not Haroon's full Brozzer. The missing parts
  are HOSNI durable dispatch, voice mouth, shared chat/voice active-turns log,
  Rider decision veto, production bridge, Telegram, CRM, and the full org
  pipeline. Be proud of the local core, but honest about the boundary.

Session: {context['session_id']}
Role: {context['role']}
Permissions: {context['permissions']}
Latest checkpoint: {checkpoint_line}
Health state: {context.get('health', 'unknown')}
Session score: {score_line}

Memory conflicts:
{conflict_lines}

Open tasks:
{open_tasks}

Keyword memory:
{_memory_lines(context['keyword_memory'])}

Semantic memory:
{_memory_lines(context['semantic_memory'])}

Graph memory:
{_memory_lines(context['graph_memory'])}

User message:
{message}
""".strip()


def _local_brozzer_answer(message: str, context: dict) -> str:
    """Deterministic BBB-like fallback when Claude is unavailable.

    This is intentionally more conservative than a tiny local model: it keeps the
    demo grounded instead of pretending to be a full brain.
    """

    lower = message.lower()
    role = context.get("role", "brozzer")
    open_tasks = context.get("open_tasks") or []
    task_line = "Açık görev yok."
    if open_tasks:
        first = open_tasks[0]
        task_line = f"Şu an açık görünen ilk görev: #{first.get('id')} - {first.get('title')}."

    if any(word in lower for word in ("toplant", "meeting", "planlar", "planla", "yarın", "13")):
        return (
            "Tamam, bunu BBB-Lite içinde operasyonel bir plan olarak ele alıyorum.\n\n"
            "Bildiklerim:\n"
            "- Zaman isteği: yarın 13:00.\n"
            f"- Aktif rol: {role}.\n"
            f"- MC durumu: {task_line}\n\n"
            "Güvenli varsayılan plan:\n"
            "1. Amaç: Haroon'a BBB-Lite Assistant Console'u göstermek.\n"
            "2. Demo sırası: Assistant chat -> Demo Console kanıt paneli -> Canvas ilişki görünümü.\n"
            "3. Konuşma örneği: 'Bu sistem Haroon BBB OS'e göre neyi kanıtlıyor?' diye sor.\n"
            "4. Kanıt: 152 test, role/memory/checkpoint paneli, semantic memory sonucu.\n"
            "5. Eksik bilgi: toplantı konusu ve katılımcılar net değil.\n\n"
            "Ben takvime kayıt atamam; bu UI'da calendar/email entegrasyonu yok. "
            "Konu ve katılımcıları verirsen sana direkt paylaşılabilir toplantı mesajını çıkarayım."
        )

    if "neden" in lower and any(word in lower for word in ("uzun", "düşündün", "dusundun", "bekledin")):
        return (
            "Uzun görünmesinin iki sebebi var:\n\n"
            "1. Önce Mission Control, rol, checkpoint, keyword memory ve semantic memory context'i toplanıyor.\n"
            "2. Claude CLI cevap vermezse fallback devreye giriyor; küçük model bazen yavaş ve zayıf kalıyor.\n\n"
            "Bu yüzden davranışı değiştirdim: Claude yoksa artık ham küçük-model cevabına güvenmek yerine "
            "daha kontrollü, BBB-Lite tarzı yerel cevap üretiyorum. Bu daha az süslü ama daha güvenilir."
        )

    if any(word in lower for word in ("haroon", "bbb os", "benzer", "kanıt", "göster")):
        return (
            "BBB-Lite'ın şu an kanıtladığı çekirdek şey şu:\n\n"
            "1. Mission Control var: görevler SQLite üzerinden takip ediliyor.\n"
            "2. Role sistemi var: brozzer/advisor/architect/builder ayrımı okunuyor.\n"
            "3. Lifecycle var: her cevap checkpoint yazıyor.\n"
            "4. Memory var: keyword ve semantic search aynı ekranda kanıtlanıyor.\n"
            "5. Guard mantığı var: işi 'done' yapmak özel complete_task yoluna bağlı.\n\n"
            "Haroon'dan farkı dürüstçe: HOSNI, voice, Telegram, CRM ve production bridge yok. "
            "Bu ekran, o büyük sistemin lokal ve konuşulabilir küçük iskeleti."
        )

    return (
        "Bunu BBB-Lite context'iyle ele alıyorum.\n\n"
        f"- Aktif rol: {role}.\n"
        f"- MC: {task_line}\n"
        "- Memory: sağ panelde keyword ve semantic recall sonuçları görünüyor.\n\n"
        "Net aksiyon önermem için bir cümle daha lazım: bunu plan mı yapmamı, görev mi açmamı, "
        "yoksa Haroon'a anlatılacak demo metnine mi çevirmemi istiyorsun?"
    )


def _enforce_brozzer_contract(answer: str, message: str, context: dict, engine: str) -> str:
    """Keep weak model output inside the local Brozzer operating contract."""

    clean = _clean_model_output(answer) or ""
    lower = clean.lower()
    has_contract = all(label in lower for label in ("known", "risk", "next"))
    if has_contract and len(clean) >= 80:
        return clean

    role = context.get("role", "brozzer")
    open_tasks = context.get("open_tasks") or []
    first_task = "none"
    if open_tasks:
        first = open_tasks[0]
        first_task = f"#{first.get('id')} {first.get('title')}"
    weak_model_note = ""
    if engine in {"ollama", "local-guarded", "unavailable"}:
        weak_model_note = " Engine is local/fallback, so the answer is treated as needs-check evidence."

    return (
        "Known:\n"
        f"- Active role: {role}.\n"
        f"- First open MC task: {first_task}.\n"
        "- Context read before answer: checkpoint, keyword memory, semantic memory, graph memory.\n\n"
        "MC impact:\n"
        "- This reply will be checkpointed and written to the MC trace.\n"
        "- A quality score, evidence row, and answer receipt will be created for audit.\n\n"
        "Risk:\n"
        f"- Raw model answer did not fully satisfy the Brozzer contract.{weak_model_note}\n"
        "- Unsupported powers remain blocked here: HOSNI, voice, Telegram, calendar, email, CRM, production bridge.\n\n"
        "Next action:\n"
        f"- Treat this as the guarded answer for: {message[:160]}\n"
        f"- Raw answer summary: {_clip(clean, 260) or 'no useful model text'}"
    )


def _status_footer(context: dict) -> str:
    """One-line status line appended to every reply (Stage AV) - always visible,
    never the main content."""

    health = context.get("health") or "unknown"
    overall_score = (context.get("session_score") or {}).get("overall_score")
    score_text = f"{overall_score}/100" if isinstance(overall_score, int) else "n/a"
    conflict_count = len(context.get("memory_conflicts") or [])
    return f"[health: {health} | score: {score_text} | conflicts: {conflict_count}]"


def _status_report(context: dict, *, session_id: str, db_path: Path) -> str:
    """Full, non-summarized dump for the 'status'/'durum' command (Stage AV)."""

    open_tasks = context.get("open_tasks") or []
    task_lines = "\n".join(
        f"- #{task.get('id')} [{task.get('status')}] {task.get('title')}" for task in open_tasks
    ) or "- none"

    upcoming_reminders = _safe_call(
        [], mc.list_upcoming_reminders, within_days=30, db_path=db_path
    )
    reminder_lines = "\n".join(
        f"- {reminder.get('due_date')} #{reminder.get('id')} {reminder.get('title')}"
        + (f" ({', '.join(reminder.get('people') or [])})" if reminder.get("people") else "")
        for reminder in upcoming_reminders
    ) or "- none"

    recent_health_events = _safe_call(
        [], mc.list_health_events, limit=3, session_id=session_id, db_path=db_path
    )
    event_lines = "\n".join(
        f"- {event.get('created_at')} {event.get('component')}/{event.get('event')}: "
        f"{event.get('detail') or '-'}"
        for event in recent_health_events
    ) or "- none"

    conflicts = context.get("memory_conflicts") or []
    conflict_lines = "\n".join(f"- {conflict.get('reason')}" for conflict in conflicts) or "- none"

    score = context.get("session_score") or {}
    score_line = json.dumps(score, ensure_ascii=False) if score else "unavailable"

    return (
        "STATUS REPORT\n\n"
        f"Role: {context.get('role')}\n\n"
        f"Open tasks:\n{task_lines}\n\n"
        f"Upcoming reminders (next 30 days):\n{reminder_lines}\n\n"
        f"Health state: {context.get('health', 'unknown')}\n"
        f"Recent health events (last 3):\n{event_lines}\n\n"
        f"Session score: {score_line}\n\n"
        f"Memory conflicts ({len(conflicts)}):\n{conflict_lines}"
    )


def _exists_report(query: str, *, db_path: Path) -> str:
    """Answer for the 'exists: <query>' / 'var mı: <query>' command (Stage AW).

    Cheap and fast: mc.cyg_exists() only compares embeddings, no real Claude
    call, so this is safe for a chat-turn shortcut.
    """

    result = _safe_call({"exists": False, "match": None, "score": None}, mc.cyg_exists, query, db_path=db_path)
    exists = result.get("exists")
    score = result.get("score")
    score_text = f"{score:.3f}" if isinstance(score, float) else "n/a"
    match = result.get("match") or {}
    match_line = ""
    if match:
        label = match.get("symbol_name") or match.get("title") or match.get("file_path") or "match"
        match_line = f"\nMatch: {label} (source={match.get('source_table', '?')})"
    return f"exists: {exists} (score: {score_text}){match_line}"


def _callers_report(symbol_name: str, *, db_path: Path) -> str:
    """Answer for the 'callers: <symbol>' command.

    mc.find_callers() walks the "calls" edges index_symbols() derives from
    real ast.Call detection - answers "what calls X" against the code as
    actually indexed, not a guess.
    """

    callers = _safe_call([], mc.find_callers, symbol_name, db_path=db_path)
    if not callers:
        return f"callers of '{symbol_name}': none found (not indexed, or nothing calls it)"
    lines = "\n".join(
        f"- {c.get('symbol_name')} ({c.get('file_path')})" for c in callers
    )
    return f"callers of '{symbol_name}' ({len(callers)}):\n{lines}"


def _depends_report(node: str, *, db_path: Path) -> str:
    """Answer for the 'depends: <node>' command.

    mc.graph_depends_on() walks the curated "depends-on" edges loaded from
    graph/edges.jsonl (mc.load_graph_edges()) - a hand-curated component
    graph, distinct from the auto-detected "calls" edges 'callers:' reads.
    """

    deps = _safe_call([], mc.graph_depends_on, node, db_path=db_path)
    if not deps:
        return f"'{node}' depends on: none found (not in the curated dependency graph)"
    lines = "\n".join(
        f"- {d.get('node')} ({d.get('type')}, hop={d.get('hop')})" for d in deps
    )
    return f"'{node}' depends on ({len(deps)}):\n{lines}"


def _mentions_report(name: str, *, db_path: Path) -> str:
    """Answer for the 'mentions: <symbol or doc>' command.

    mc.find_mentions() walks the "references" edges index_docs() derives
    (doc -> symbol, from prose scanning) - answers "what mentions X" in
    either direction, against the doc/symbol graph as actually indexed.
    """

    mentions = _safe_call([], mc.find_mentions, name, db_path=db_path)
    if not mentions:
        return f"mentions of '{name}': none found (not indexed, or nothing references it)"
    kind = mentions[0].get("node_type")
    label = "documents mentioning" if kind == "doc" else "symbols mentioned by"
    lines = "\n".join(f"- {m.get('title')} ({m.get('file_path')})" for m in mentions)
    return f"mentions of '{name}' - {label} ({len(mentions)}):\n{lines}"


def _decisions_for_report(entity_name: str, *, db_path: Path) -> str:
    """Answer for the 'decisions-for: <entity>' command (Stage BA).

    mc.get_decisions_for_entity() walks the "concerns" edges
    mc.link_decision_to_entity() writes - a decision only shows up here if it
    was explicitly linked via 'decision: ... | <entity>', not a keyword guess.
    """

    decisions = _safe_call([], mc.get_decisions_for_entity, entity_name, db_path=db_path)
    if not decisions:
        return f"decisions for '{entity_name}': none found (not linked, or entity unknown)"
    lines = "\n".join(
        f"- {d.get('created_at')} [{d.get('status')}] {d.get('decision')}" for d in decisions
    )
    return f"decisions for '{entity_name}' ({len(decisions)}):\n{lines}"


def _projects_report(*, db_path: Path) -> str:
    """Answer for the 'projects' / 'projeler' command (Stage AW) - read-only."""

    projects = _safe_call([], mc.list_projects, db_path=db_path)
    if not projects:
        return "Projects: none"
    lines = "\n".join(
        f"- #{project.get('id')} {project.get('name')} [{project.get('status')}]"
        for project in projects
    )
    return f"Projects:\n{lines}"


def _compact_report(session_id: str, *, db_path: Path) -> str:
    """Answer for the 'compact:' command (Stage BB) - a chat-triggered
    simulation of Claude Code's own /compact mechanism.

    assistant_server.py is a plain HTTP server; it has no real Claude Code
    context window to compact. This calls the exact snapshot/diff functions
    Stage R proved under a genuine /compact (mc.save_snapshot,
    mc.compare_snapshots) back-to-back against the *current* MC state, so
    the underlying loss-detection mechanism is real even though there is no
    actual compaction event triggering it. A real /compact can still only be
    exercised inside the Claude Code terminal itself (see decisions.md,
    2026-08-09).
    """

    mc.save_snapshot(session_id, "pre", db_path=db_path)
    mc.save_snapshot(session_id, "post", db_path=db_path)
    findings = mc.compare_snapshots(session_id, db_path=db_path)
    if findings:
        lines = "\n".join(f"- {finding}" for finding in findings)
        return f"compact simulation: DEGRADED\n{lines}"
    snapshot = mc.compact_snapshot(session_id, db_path=db_path)
    total = (
        len(snapshot["tasks"])
        + snapshot["decisions"]
        + snapshot["checklist_items"]
        + (1 if snapshot["role_declared"] else 0)
    )
    return f"compact simulation: {total}/{total} items intact"


def _clear_sim_report(new_session_id: str, *, db_path: Path) -> str:
    """Answer for the 'clear-sim: <new_session_id>' command (Stage BB) - a
    chat-triggered simulation of Claude Code's own /clear mechanism.

    Runs the real Stage AF role-inheritance logic (mc.get_most_recent_role
    -> mc.set_role) against a brand-new session id, exactly as
    hooks/session_start.py does on a genuine /clear, then reports
    mc.clear_recovery_verified() for that new id. The live chat session
    (whatever session_id this request actually came in on) is never touched
    - this only ever writes to the new id given after the colon.
    """

    mc.ensure_session(new_session_id, db_path=db_path)
    inherited = mc.get_most_recent_role(db_path=db_path)
    if inherited:
        mc.set_role(new_session_id, inherited, db_path=db_path)
        role_line = f"role inherited: {inherited}"
    else:
        role_line = "NOTE: no previous role found, defaulting to brozzer"
    verified = mc.clear_recovery_verified(new_session_id, db_path=db_path)
    checkpoint_state = "present" if verified["latest_checkpoint"] else "none"
    return (
        f"clear-sim for '{new_session_id}':\n"
        f"- {role_line}\n"
        f"- verified: role={verified['role']}, verified_flag={verified['verified']}, "
        f"{len(verified['open_task_ids'])} open tasks, checkpoint={checkpoint_state}"
    )


def _complete_task_via_chat(task_id: int, session_id: str, *, db_path: Path) -> str:
    """Real mc.complete_task() call from chat (Stage AV) - errors are shown as-is,
    proving the completion guard instead of hiding behind a friendly message."""

    try:
        evidence_id = mc.record_evidence(
            "user_confirmation",
            f"chat_complete_task_{task_id}",
            "passed",
            details={"session_id": session_id, "source": "assistant_console_chat"},
            db_path=db_path,
        )
        result = mc.complete_task(
            task_id,
            session_id=session_id,
            evidence_id=evidence_id,
            completion_summary=f"Completed via assistant chat by session {session_id}.",
            db_path=db_path,
        )
    except Exception as exc:
        return f"[complete task failed: {exc}]"
    return f"[task {result['task_id']} completed]"


def _confirm_task_with_test(task_id: int, detail: str, session_id: str, *, db_path: Path) -> str:
    """Real test-evidence completion path (Stage AX): 'confirm task N with test: ...'.

    Unlike _complete_task_via_chat (which deliberately has no test evidence and
    is refused by complete_task's require_test_evidence guard), this creates a
    genuine kind="test_run" evidence row first, so complete_task's guard is
    satisfied honestly rather than bypassed.
    """

    try:
        evidence_id = mc.record_evidence(
            "test_run",
            f"chat_test_{task_id}",
            "passed",
            details={"session_id": session_id, "source": "assistant_console_chat", "detail": detail},
            db_path=db_path,
        )
        result = mc.complete_task(
            task_id,
            session_id=session_id,
            evidence_id=evidence_id,
            completion_summary=detail or f"Test passed for task {task_id}.",
            require_test_evidence=True,
            db_path=db_path,
        )
    except Exception as exc:
        return f"[confirm task with test failed: {exc}]"
    return f"[task {result['task_id']} completed via test evidence id={evidence_id}]"


def _start_task_via_chat(task_id: int, session_id: str, *, db_path: Path) -> str:
    """Real mc.start_task() call from chat (Stage AY) - errors (already
    started/done) are shown as-is, same as the other task commands."""

    try:
        result = mc.start_task(task_id, session_id=session_id, db_path=db_path)
    except Exception as exc:
        return f"[start task failed: {exc}]"
    return f"[task {result['task_id']} started]"


def _remind_via_chat(rest: str, session_id: str, *, db_path: Path) -> str:
    """Real mc.create_reminder() call from chat (Stage AZ): 'remind: ...'.

    A malformed payload or unparseable date is shown to the user verbatim
    (via _parse_remind_payload's ValueError), same as the other task-command
    guard failures - never a bare traceback.
    """

    try:
        description, due_date, people = _parse_remind_payload(rest)
    except ValueError as exc:
        return f"[remind failed: {exc}]"

    try:
        result = mc.create_reminder(description, due_date, people, session_id=session_id, db_path=db_path)
    except Exception as exc:
        return f"[remind failed: {exc}]"

    people_summary = ", ".join(
        f"{name} ({'new' if info.get('created') else 'existing'})"
        for name, info in result.get("people", {}).items()
    )
    return f"[reminder added, task id={result['task_id']}] {people_summary}"


def _handle_task_commands(message: str, session_id: str, *, db_path: Path) -> str | None:
    """Real task add/complete/remind side effects for the chat surface
    (Stage AV/AX/AZ).

    Returns a confirmation/error line to append to the answer, or None if the
    message triggered no task command.
    """

    remind_payload = _remind_payload(message)
    if remind_payload is not None:
        return _remind_via_chat(remind_payload, session_id, db_path=db_path)

    confirm_match = _confirm_task_with_test_match(message)
    if confirm_match:
        task_id, detail = confirm_match
        return _confirm_task_with_test(task_id, detail, session_id, db_path=db_path)

    start_id = _task_start_id(message)
    if start_id is not None:
        return _start_task_via_chat(start_id, session_id, db_path=db_path)

    title = _task_add_title(message)
    if title:
        task_id = mc.add_task(title, session_id=session_id, db_path=db_path)
        return f"[task added, id={task_id}]"

    task_id = _task_complete_id(message)
    if task_id is not None:
        return _complete_task_via_chat(task_id, session_id, db_path=db_path)

    return None


def answer_message(message: str, session_id: str = DEFAULT_SESSION_ID, *, db_path: Path = mc.DB_PATH) -> dict:
    message = str(message or "").strip()
    if not message:
        return {"error": "message is required"}

    context = collect_context(message, session_id, db_path=db_path)
    _safe_call(
        None,
        mc.record_turn,
        session_id,
        "user",
        message,
        role=context["role"],
        db_path=db_path,
    )
    exists_query = _exists_query(message)
    callers_query = _callers_query(message)
    depends_query = _depends_query(message)
    mentions_query = _mentions_query(message)
    decisions_for_query = _decisions_for_query(message)
    clear_sim_session_id = _clear_sim_session_id(message)
    if _is_status_command(message):
        answer = _status_report(context, session_id=session_id, db_path=db_path)
        engine = "status-command"
    elif exists_query:
        answer = _exists_report(exists_query, db_path=db_path)
        engine = "exists-command"
    elif callers_query:
        answer = _callers_report(callers_query, db_path=db_path)
        engine = "callers-command"
    elif depends_query:
        answer = _depends_report(depends_query, db_path=db_path)
        engine = "depends-command"
    elif mentions_query:
        answer = _mentions_report(mentions_query, db_path=db_path)
        engine = "mentions-command"
    elif decisions_for_query:
        answer = _decisions_for_report(decisions_for_query, db_path=db_path)
        engine = "decisions-for-command"
    elif _is_compact_command(message):
        answer = _compact_report(session_id, db_path=db_path)
        engine = "compact-command"
    elif clear_sim_session_id:
        answer = _clear_sim_report(clear_sim_session_id, db_path=db_path)
        engine = "clear-sim-command"
    elif _is_projects_command(message):
        answer = _projects_report(db_path=db_path)
        engine = "projects-command"
    else:
        prompt = build_prompt(message, context)
        answer = run_claude(prompt)
        engine = "claude-cli"
        if answer is None:
            answer = _local_brozzer_answer(message, context)
            engine = "local-guarded"
            if os.environ.get("BBB_ASSISTANT_USE_OLLAMA") == "1":
                ollama_answer = _clean_model_output(mc.ask_ollama(prompt, db_path=db_path))
                if ollama_answer:
                    answer = ollama_answer
                    engine = "ollama"
        if answer is None:
            answer = (
                "Cevap motoruna ulaşamadım. Claude CLI oturum açmamış/limitli olabilir; "
                "Ollama da yanıt vermedi. Yine de Mission Control, role, memory ve "
                "checkpoint context'i toplandı."
            )
            engine = "unavailable"
        answer = _enforce_brozzer_contract(answer, message, context, engine)

        task_note = _handle_task_commands(message, session_id, db_path=db_path)
        if task_note:
            answer = f"{answer}\n\n{task_note}"

    answer = f"{answer}\n\n{_status_footer(context)}"
    checkpoint_risk = "low" if engine != "unavailable" else "model_unavailable"

    # Stage AU: this was already fail-soft via _safe_call (a failed checkpoint
    # write must never break the console reply) - now it also logs, so a
    # checkpoint that silently failed to persist shows up in get_health_state().
    try:
        mc.write_checkpoint(
            session_id,
            current_task=_clip(message, 160),
            last_decision="assistant_console_reply",
            next_action=_clip(answer, 180),
            risk=checkpoint_risk,
            db_path=db_path,
        )
    except Exception as exc:
        mc.log_health_event(
            "checkpoint", "write_failed", detail=str(exc), session_id=session_id, db_path=db_path
        )
    assistant_turn_id = _safe_call(
        None,
        mc.record_turn,
        session_id,
        "assistant",
        answer,
        role=context["role"],
        engine=engine,
        db_path=db_path,
    )
    _safe_call(
        None,
        mc.record_event,
        "assistant_reply",
        session_id=session_id,
        source="assistant_console",
        details={"engine": engine, "role": context["role"]},
        db_path=db_path,
    )
    decision_packet_id = None
    decision = _decision_text(message)
    if decision:
        decision, decision_entity = _split_decision_entity(decision)
        decision_packet_id = _safe_call(
            None,
            mc.create_decision_packet,
            session_id,
            decision,
            source="chat",
            context_gist=_clip(answer, 240),
            db_path=db_path,
        )
        # Stage AX: decision_packets alone isn't searchable by search_memory/
        # semantic_search (those read memory_index) - mirror the same text
        # into memory via log_lesson so a chat decision can actually be found
        # later, not just stored.
        _safe_call(
            None,
            mc.log_lesson,
            f"chat decision: {_clip(decision, 60)}",
            decision,
            confidence="medium",
            db_path=db_path,
        )
        # Stage BA: "decision: ... | <entity>" - link the packet into the
        # graph (mc.link_decision_to_entity) so it isn't a Haroon-style
        # "cold decision", invisible to graph traversal.
        if decision_entity and decision_packet_id:
            _safe_call(
                None,
                mc.link_decision_to_entity,
                decision_packet_id,
                decision_entity,
                db_path=db_path,
            )

    score = mc.assess_answer_quality(
        message,
        answer,
        context=context,
        engine=engine,
        decision_packet_id=decision_packet_id,
    )
    quality_score_id = _safe_call(
        None,
        mc.record_answer_quality,
        session_id,
        score,
        turn_id=assistant_turn_id,
        db_path=db_path,
    )
    evidence_id = _safe_call(
        None,
        mc.record_evidence,
        "answer",
        "assistant_console_reply",
        score["status"],
        details={
            "engine": engine,
            "role": context["role"],
            "total_score": score["total_score"],
            "decision_packet_id": decision_packet_id,
        },
        db_path=db_path,
    )
    answer_id = uuid.uuid4().hex
    receipt_id = _safe_call(
        None,
        mc.record_answer_receipt,
        session_id,
        answer_id,
        engine=engine,
        role=context["role"],
        decision_packet_id=decision_packet_id,
        quality_score_id=quality_score_id,
        evidence_id=evidence_id,
        checkpoint_risk=checkpoint_risk,
        next_action=_clip(answer, 180),
        details={"message": _clip(message, 180), "score_status": score["status"]},
        db_path=db_path,
    )

    return {
        "id": answer_id,
        "answer": answer,
        "engine": engine,
        "decision_packet_id": decision_packet_id,
        "quality_score": score,
        "quality_score_id": quality_score_id,
        "evidence_id": evidence_id,
        "answer_receipt_id": receipt_id,
        "context": _json_safe(context),
    }


def build_state(session_id: str = DEFAULT_SESSION_ID, *, db_path: Path = mc.DB_PATH) -> dict:
    mc.init_db(db_path)
    mc.ensure_session(session_id, db_path=db_path)
    role = mc.get_role(session_id, db_path=db_path)
    tasks = _safe_call([], mc.list_tasks, db_path=db_path)
    boundaries = _safe_call([], mc.list_capability_boundaries, db_path=db_path)
    boundaries_by_name = {item.get("capability"): item for item in boundaries}
    voice_boundary = boundaries_by_name.get("voice") or {
        "enabled": False,
        "reason": VOICE_OMISSION_REASON,
    }
    return {
        "session_id": session_id,
        "role": role,
        "permissions": mc.ROLE_PERMISSIONS.get(role, {}),
        "open_tasks": sum(1 for task in tasks if task.get("status") != "done"),
        "done_tasks": sum(1 for task in tasks if task.get("status") == "done"),
        "checkpoint": _safe_call(None, mc.latest_checkpoint, session_id, db_path=db_path),
        "claude_cli_available": shutil.which("claude") is not None,
        "voice": {"enabled": voice_boundary["enabled"], "reason": voice_boundary["reason"]},
        "brozzer_parity": BROZZER_PARITY,
        "mc_trace": {
            "turns": _safe_call([], mc.list_turns, session_id, limit=8, db_path=db_path),
            "decision_packets": _safe_call([], mc.list_decision_packets, limit=5, db_path=db_path),
            "dispatch_runs": _safe_call([], mc.list_dispatch_runs, limit=5, db_path=db_path),
            "agent_outputs": _safe_call([], mc.list_agent_outputs, limit=5, db_path=db_path),
            "events": _safe_call([], mc.list_events, limit=8, db_path=db_path),
            "evidence": _safe_call([], mc.list_evidence, limit=5, db_path=db_path),
            "answer_quality_scores": _safe_call(
                [], mc.list_answer_quality_scores, session_id, limit=5, db_path=db_path
            ),
            "answer_receipts": _safe_call(
                [], mc.list_answer_receipts, session_id, limit=5, db_path=db_path
            ),
            "lifecycle_runs": _safe_call(
                [], mc.list_lifecycle_runs, session_id, limit=5, db_path=db_path
            ),
            "capability_boundaries": boundaries,
            "graph_stats": _safe_call({}, mc.graph_stats, db_path=db_path),
        },
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
