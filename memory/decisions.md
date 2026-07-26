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

- **2026-07-24 — Stage F: rol simülasyonunun Haroon'un gerçek sisteminden farkları.**
  Aşağıdaki tablo, `bin/dispatch.py`'nin haroon-bbb-sync'teki karşılığından nerelerde ve
  neden ayrıştığını kayıt altına alır — sunucu yok kuralı ve mevcut Pro aboneliği
  kısıtları etrafında bilinçli basitleştirmeler.

  | Konu | Haroon'un sisteminde | bbb-lite'da | Sebep |
  |---|---|---|---|
  | Dispatch | ssh ile ayrı bir bilgisayara (HOSNI) | subprocess ile aynı bilgisayarda alt-süreç | Sunucu/ikinci makine yok |
  | Headless çağrı | Claude Agent SDK | `claude -p` (CLI print-mode) | SDK ayrı API key + pay-per-token gerektiriyor; CLI mevcut Pro aboneliği içinde kalıyor |
  | Model | CTO=Sonnet 5, Builder=Opus 4.8 (ayrı güç) | tek model | Basitlik, maliyet kontrolü |
  | "Architect gerekli mi" kararı | kendi (Haroon'un) mantığı | Ollama tabanlı `classify()` fonksiyonu | Her küçük kararı gerçek Claude'a sormamak, token tasarrufu |
  | Worktree (izole çalışma alanı) | git worktree | git worktree | Gerçekten aynı — sunucu gerektirmeyen bir git özelliği |

- **2026-07-24 — Haroon'un hourly-tripwire'ının 4 kontrolünden 3'ü (frozen-DB guard,
  systemd health, changed-files/MC-writes) bilerek eklenmedi: ilk ikisi Linux/systemd ve
  HOSNI'ye özel servislere bakıyor (bizde yok), üçüncüsü çoklu-makine (Mini+HOSNI)
  senkronizasyonuna bakıyor (bizde tek makine var, senkronize edilecek ikinci taraf yok).
  4. kontrol (safepoint-stale guard) ise anlamlıydı ve check_safepoint_stale() olarak
  eklendi.

- **2026-07-24 — Curator-only guard, Haroon'un rol sistemine (curator role_for_uuid ile)
  değil, basit bir BBB_CURATOR ortam değişkenine dayanıyor** - rol sistemi kurmadığımız
  için (bilerek, kapsam dışı) en yakın karşılık bu.

- **2026-07-24 — Genel PreToolUse guard, Haroon'un role-guard.py'sindeki rol-bazlı izin
  kontrolü değil (rol sistemi yok), sabit bir kuralı (mc.db'ye Write/Edit aracıyla asla
  dokunulmaz) zorlayan + her kod değişikliğini loglayan bir versiyon.**

- **2026-07-24 - projects/sprints tabloları eklendi, Haroon'un MC şemasının küçük bir alt
  kümesi (aynı SQLite + guard mantığı, CRM/bütçe/otomatik-sprint-geçişi gibi ek katmanlar
  yok). phases, docs, events_log bilinçli olarak kapsam dışı - zincirleme etkiyi (Canvas,
  dispatch, guard'ların hepsinin yeniden yazılması) sınırlı tutmak için.

- **2026-07-24 - Skills sistemi 5 skill ile kuruldu (Haroon'un 36'sına karşı) - format
  aynı, kapsam mc.py'deki gerçek fonksiyonlarla sınırlı, boş/sahte skill yok.**

- **2026-07-24 - session_score() eklendi, Haroon'un context/exec/rules 3-eksenli
  puanlamasının değil, tek-eksenli (görev tamamlama oranı) basit bir karşılığı - bizde
  context-budget/rule-tracking altyapısı olmadığı için.**

- **2026-07-24 - Gerçek rol sistemi kuruldu: roles tablosu, session'a kalıcı rol atama,
  4 rol + izin matrisi, ve pretooluse_log.py üzerinden GERÇEK kod-yazma zorlaması.
  Haroon'unkinden farkı: UUID yerine session_id kullanılıyor, persona metinleri yok
  (sadece izin matrisi), architect/advisor'ın 'data=True, code=False' ayrımı
  basitleştirilmiş durumda tutuluyor.**
