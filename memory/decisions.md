# Decisions

- **2026-07-21 — Ported the guard engine, not the whole system.** `mc.py`'s
  `GuardedLocalConnection`/`GuardedLocalCursor` and SQL-shape parser come from
  haroon-bbb-sync's `lib/bbb_mc_access.py` (rebuild branch), but the remote HTTP bridge, the
  write-gate nonce/marker audit trail, board-hygiene guards, and human-approval gating were all
  dropped — they assume tables (`projects`, `task_activity`, `events_log`,
  `bbb_completion_authority`, ...) that don't exist in bbb-lite's schema. Only the
  tasks.status='done' guard was kept, adapted to bbb-lite's actual `tasks` table.

- **2026-07-21 — Local mode is the only mode.** No remote/bridge config, no machine-resolution
  logic. `mc.py` always connects straight to the local `mc.db` file, per CLAUDE.md's "SQLite
  only, a single local file, no server" rule.

- **2026-07-21 — `complete_task()` is the sole sanctioned path to done.** Raw SQL (including via
  `add_task(status="done")` or `conn.cursor()`) can never mark a task done directly; see
  `memory/lesson-guard-blocks-raw-done-writes.md`. This mirrors the source system's completion
  gate but without its evidence/approval-token machinery, which doesn't apply here.

- **2026-07-24 — gemma4:12b'ye yükseltme bilerek atlandı.** Sebep: CPU'da hız/kaynak
  dezavantajı + gemma:2b'nin zaten kendi testlerimizde güvenilmezliği kayıtlı (bkz.
  `memory/lesson-2026-07-24-gemma-2b-s-duplicate-check-in-log-lesson-is-unreliable.md`) —
  büyük model küçük bir farkı örtmez, mimari kararımız donanım ve zaman kısıtına göre
  bilinçli seçildi.

- **2026-07-24 — Headless rol simülasyonu için Claude Agent SDK değil, Claude Code
  CLI'nin print-mode'unu (`claude -p`) kullanacağız.** Sebep: SDK ayrı bir API key +
  pay-per-token faturalandırma gerektiriyor, CLI ise mevcut Claude Pro aboneliğinin (OAuth,
  sabit ücret) içinde kalıyor — token/maliyet kontrolü için bilinçli tercih.

- **2026-07-24 — "Servis/API yüzeyi yok" gap'i not edildi, ayrı iş açılmadı:** planlanan
  Streamlit dashboard (sıradaki büyük madde) kendi process/port'una sahip bir servis
  olacağı için bu gap'i de karşılayacak.

- **2026-07-24 — "Small brain sadece duplicate-checker" gap'i not edildi:** rol
  simülasyonu (Stage F) sırasında `ask_ollama()` çoklu-seçenekli bir sınıflandırıcıya
  genişletilirken kapatılacak.
