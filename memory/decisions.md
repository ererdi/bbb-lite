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

- **2026-07-24 - "Graph memory derinliği" bulgusu (Codex): bu büyük ölçüde ZATEN
  ÇÖZÜLDÜ (Stage E, semantic_search() - gerçek sentence-transformers embedding'i,
  anlam bazlı arama). Kalan tek fark: Haroon bge-m3 (büyük, özel model) kullanıyor,
  biz all-MiniLM-L6-v2 (küçük, hafif) - bu, Stage'de zaten bilinçli bir karar olarak
  not edilmişti (donanım/hız), model boyutu farkı dışında Graph memory mekanizması
  artık eşdeğer.**

- **2026-07-24 - "Hooks .disabled, demo gerçek Claude lifecycle'a bağlı değil"
  bulgusu (Codex): bu YANLIŞ/eksik bir tespit. Hook'lar bilerek .disabled
  tutuluyor (geliştirme sırasında kendi checkpoint/session verisinin karışmaması
  için, bkz. erken karar) - ama önce GERÇEK, canlı olarak test edildi: /clear
  sonrası checkpoint hatırlama, gerçek ollama ile judge verdict, gerçek claude -p
  ile dispatch, gerçek role_declare ile brozzer->builder geçişi (Stage N). Yani
  "çalışmıyor" değil, "test edilip kanıtlanmış, sonra geliştirme kolaylığı için
  kapatılmış" - .disabled uzantısını kaldırmak tek satırlık bir işlem, herhangi
  bir an aktif edilebilir.**

- **2026-07-24 - "MC schema genişliği" bulgusu (Codex): bu bir kullanım eksikliği
  değil, ölçek farkı. Haroon'un ~180 tablosuna karşı bizde 7 tablo var (sessions,
  checkpoints, tasks, memory_index, task_activity, projects, sprints, roles).
  Kalan tablolar (docs, events_log, inbox, bbb_completion_authority, CRM benzeri
  yapılar) bilinçli olarak kapsam dışı - bunlar Haroon'un production sisteminin
  aylarca birikmiş, çok daha büyük ihtiyaçlarına ait, bu projenin öğrenme/demo
  amacına ek bir şey katmıyorlar.**

- **2026-07-24 - "Canvas, Haroon'daki context-map/voice-sync seviyesinde değil"
  bulgusu (Codex): doğru, iki sebepten - (a) voice-sync mümkün değil çünkü ses
  altyapısı bilerek kapsam dışı bırakıldı, (b) context-map derinliği (curated
  node/edge ilişkileri) Haroon'da elle onaylanan bir süreç - bizim Canvas'ımız
  (Stage H) sessions/tasks/task_activity'yi otomatik gösteriyor, bu haliyle
  amacına uygun ama Haroon'unki kadar zengin bir ilişki modeli yok.**

- **2026-07-24 - Demo Console eklendi (bin/demo_server.py, port 8090), Canvas'tan
  (port 8080+5173) ayrı tutuldu - Codex'in önerisi: Canvas ilişki-görselleştirme,
  Demo Console durum/kanıt gösterimi, farklı amaçlar. HOSNI yerine yerel SQLite
  okuyan aynı 'local eşdeğer' mantığı.**

- **2026-07-24 - Assistant Console eklendi (bin/assistant_server.py, port 8100):
  bu artık dashboard değil, BBB-Lite ile konuşulan gerçek web yüzeyi. Tarayıcı
  Claude hesabına doğrudan bağlanmaz; backend mevcut Claude Code CLI oturumunu
  (`claude -p`) kullanır, yoksa Ollama'ya fail-soft düşer. Her cevap önce rol,
  checkpoint, açık görevler, keyword memory ve semantic memory context'i toplar;
  cevap sonrası lifecycle checkpoint yazar.**

- **2026-07-26 - Stage Q MC observability expansion eklendi:** Haroon referansi
  disina cikmadan `turns`, `decision_packets`, `dispatch_runs`, `agent_outputs`,
  `events_log`, `evidence`, `capability_boundaries` tablolarinin lokal SQLite
  karsiliklari kuruldu. Bunlar Haroon'daki active-turn trace, bbb-dispatch
  packet, agent_runs ledger, receipt/proof ve explicit service-boundary
  fikirlerinin HOSNI'siz yerel esdegeridir. Voice yine bilincli kapsam disi:
  once text assistant + MC izleri kanitlanacak.

- **2026-07-26 - Stage R graph memory eklendi:** Stage E semantic_search sadece
  "benzer dokuman bulma" idi; artik `graph_nodes` + `graph_edges` ile gercek
  typed-edge graph katmani var. Haroon'daki `graph/edges.jsonl`,
  `graph_edges`, hop-capped traversal ve trusted edge vocabulary mantiginin
  lokal SQLite karsiligi kuruldu. HOSNI graph service yok; ayni fikir yerel
  `mc.db` icinde calisiyor.

- **2026-07-24 - Voice bilinçli olarak bu stage dışında bırakıldı; Brozzer parity
  planı eklendi (docs/brozzer-parity-plan.md). Gerekçe: voice ayrı bir ürün
  yüzeyi; STT/TTS, mic izinleri, latency, barge-in, port izolasyonu ve ayrı
  red/green harness ister. Bu stage text assistant brain'i kanıtlıyor. Brozzer
  şu an partial-local-brozzer: MC/memory/lifecycle var; HOSNI durable dispatch,
  voice mouth, shared active-turns log, Rider veto ve production bridge yok.**

- **2026-07-27 - dispatch.py, claude -p'den Claude Agent SDK'ya taşındı.** Not:
  Anthropic'in Haziran 2026'da duyurduğu ayrı "Agent SDK credit" sistemi 15
  Haziran'da uygulamaya konulmadan durduruldu - hem CLI hem SDK hâlâ normal
  abonelik kotasından düşüyor, bu yönden maliyet farkı hiç olmadı/olmayacak.
  SDK'ya geçiş sebebi: Haroon'un CLI'nin ileride kısıtlanabileceği uyarısı ve
  SDK'nın daha resmi/uzun-vadeli desteklenen yol olması. Bu, 2026-07-24
  tarihli "SDK ayrı API key + pay-per-token gerektiriyor" kararını geçersiz
  kılar.

- **2026-07-27 - Compact recovery validation eklendi (Stage R).** Bilerek
  fail-soft DEĞİL: eksik kayıt varsa session 'degraded' işaretlenir ve
  görünür şekilde bildirilir - Haroon'un ekip şablonundaki "prevent Compact
  from being treated as successful if persistence fails" gereksinimine
  uygun, projenin genel fail-soft felsefesinden bilinçli bir sapma.

- **2026-07-27 - Memory self-healing eklendi (Stage T), Haroon'un kendi
  _archived-healed/ deseninden ilham alındı - eski notlar silinmiyor,
  arşivleniyor.

- **2026-07-27 - Bilinen bir çelişki:** Haroon'un net kuralı "workstation'da
  asla kalıcı loop/cron çalışmaz, sadece HOSNI'de" - ama daily_check.py
  (check_floor, heal_memory dahil) Windows Task Scheduler ile WORKSTATION'da
  çalışıyor. Sebep: bizde HOSNI/server yok, tek kullanıcı/tek makine
  mimarisi. Risk (Haroon'un endişe ettiği "birden fazla kişi aynı anda
  çakışır" senaryosu) bizde gerçekleşmiyor çünkü tek kullanıcı var. Ama bu,
  mimarinin gerçek bir sınırlaması olarak açıkça not ediliyor - eğer proje
  çok kullanıcılı hale gelirse, bu loop'un merkezi bir sunucuya taşınması
  gerekir.
