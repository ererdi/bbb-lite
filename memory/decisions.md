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

- **2026-07-27 - Compact recovery validation eklendi (Stage R2 - "Stage R"
  etiketi burada yanlış kullanılmıştı, 2026-07-26 tarihli graph memory
  girişiyle çakışıyordu; bu girdi ayrı bir stage olarak Stage R2 diye
  yeniden adlandırıldı, kod/tablo adları değişmedi).** Bilerek
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

- **2026-07-29 - dispatch.py'ye CTO katmanı eklendi (Stage U)** -
  Brozzer->CTO->architect->builder zincirinin eksik halkasıydı. CTO artık
  gerçek bir MC task açıyor, builder onu tamamlayınca kapatıyor - "doğar,
  MC'ye yazar, ölür" döngüsü artık tam.

- **2026-07-29 - Builder artık izole bir git worktree'de çalışıyor (Stage
  V)** - Haroon'un sistemindeki AYNI mekanizma (git worktree, sunucu
  gerektirmeyen gerçek bir git özelliği). Worktree oluşturulamazsa fail-soft
  olarak ana klasöre düşer.

- **2026-07-29 - documents tablosu eklendi (Stage W)** - Haroon'un "proje
  dokümanlarını (northstar.md gibi) dosya olarak değil MC'de tutuyoruz"
  fikrinin karşılığı. Şimdilik sadece northstar/plan/note tipleri var,
  gerçek sistemdeki gibi zengin bir doküman kategorisi yok.

- **2026-07-29 - confidence skoru artık gerçekten kullanılıyor (Stage X)** -
  arama sıralamasını etkiliyor, düşük confidence otomatik emekliye
  ayrılıyor. Haroon'un sisteminde bu metrik neredeyse hep aynı değerde
  kümelenmiş ("ölü metrik") bulunmuştu - bizim sistemimizde en azından
  gerçekten okunuyor ve kullanılıyor.

- **2026-07-29 - session_score 3 eksenli hale getirildi (Stage Y)** -
  Haroon'un context/exec/rules ayrımının küçük karşılığı. context_score
  gerçek bir context-budget takibi değil (checkpoint varlığına dayalı basit
  bir proxy), rules_score pretooluse.log'a dayanıyor. Bunun için
  pretooluse_log.py'nin deny yolu da değişti: önceden reddedilen yazma
  denemeleri hiç loglanmıyordu (sadece izin verilenler), şimdi
  "denied session=<id>: <reason>" satırı olarak session_id ile birlikte
  yazılıyor - yoksa rules_score'un okuyacağı hiçbir veri olmazdı.

- **2026-07-29 - context-map eklendi (Stage Z)** - Haroon'un elle-onaylanan
  node/edge ilişki grafiğinin küçük karşılığı. En önemli kısıt korundu: bir
  edge, HER İKİ node onaylanmadan oluşturulamıyor - kürasyon zorunlu,
  otomatik büyüme yok.

- **2026-07-29 - Session recovery paketi tamamlandı (Stage AA)** - risk,
  recent decisions, confidence bilgisi, degraded-uyarısı, ve paket boyutu
  ölçümü eklendi. On-demand genişletme (ek sorgu ile daha fazla hafıza
  çekme) HÂLÂ yok - bu, ayrı bir skill olarak eklenebilir, şimdilik kapsam
  dışı.

- **2026-07-30 - Known duplication: context_nodes/context_edges (Stage Z)
  vs graph_nodes/graph_edges (Stage R, 07-26).** İkisi de "node/edge
  ilişki grafiği" problem alanına hizmet ediyor ama farklı güven
  modelleriyle: graph_nodes/graph_edges çoğunlukla otomatik dolduruluyor
  (_upsert_memory_graph: memory dosyalarından keyword/wikilink çıkarımı,
  artı graph/edges.jsonl'den trusted-typed edge'ler), authority/
  confidence/currentness/curated alanlarıyla güven derecesi işaretleniyor
  ama curated=0 olan edge'ler de sorgulanabiliyor - kürasyon burada
  advisory (sonradan işaretleme), zorunlu bir kapı değil. context_nodes/
  context_edges (Stage Z) ise tam tersi: hiçbir otomatik doldurma yok,
  bir edge sadece HER İKİ node approve_node() ile onaylandıktan sonra
  add_edge() ile oluşturulabiliyor - kürasyon burada insert-time'da
  zorunlu bir kapı (add_edge ValueError fırlatıyor aksi halde). Ayrıca
  şema seviyesinde de örtüşme var: graph_nodes string node_id (slug)
  kullanırken context_nodes autoincrement int id kullanıyor, ikisi
  arasında hiçbir köprü/FK yok - aynı gerçek-dünya varlığı için iki ayrı,
  birbirinden habersiz kayıt oluşabilir.
  Öneri (kod değişikliği YAPILMADI, sadece not): Stage S'te graph_edges'e
  eklenen curated/confidence/currentness alanları, Stage Z'nin var olma
  sebebini (kürasyon) zaten kısmen karşılıyor. graph_nodes/graph_edges
  taraf, traverse_graph/graph_depends_on/graph_impact/graph_memory_search
  gibi gerçek kullanım noktalarına sahip olduğu için daha zengin ve daha
  çok yaslanılan taraf - context_nodes/context_edges şu an sadece
  list_context_map() tarafından okunuyor ve hiçbir otomatik besleme yok.
  Uzun vadede tek bir kürasyon modeline (graph_edges.curated + insert-time
  zorunlu kapı seçeneği) birleştirilmesi, iki paralel şemayı taşımaktan
  daha az karmaşık olur - ama bu, context-map'in "otomatik büyüme asla
  olmasın" garantisini graph_edges'in mevcut advisory kürasyon modeline
  nasıl taşıyacağına dair ayrı bir tasarım kararı gerektirir, bu yüzden
  şimdilik kapsam dışı bırakıldı.

- **2026-07-29 - MC seam layer eklendi (Stage AD)** - Codex'in önerisi, ama
  GÜVENLİ versiyon: query_rows() sadece SELECT'e izin veriyor (serbest
  execute() yok - mevcut guard'ları atlayabilecek bir "arka kapı" bilerek
  eklenmedi). write_gate()/readback_verify() var olan guard mantığının
  üzerine dokümantasyon/doğrulama katmanı, yeni bir yazma yolu değil.
  Ayrı bir lib/ dosyasına taşıma (Codex'in önerdiği gibi) şimdilik
  yapılmadı - mc.py tek dosya kalmaya devam ediyor, bu ayrı bir karar
  (dosya organizasyonu, ayrı bir Stage'de ele alınabilir).

- **2026-07-29 - Stage AC: context_nodes/context_edges (Stage Z) ile
  graph_nodes/graph_edges (Stage R, 07-26) arasındaki tekrar giderildi.**
  Zorunlu-kürasyon davranışı graph_edges'e taşındı (requires_dual_approval +
  add_curated_edge). Eski tablolar silinmedi ama artık kullanılmıyor
  (deprecated). propose_node/approve_node artık graph_nodes üzerinde
  çalışıyor (node_id = _graph_node_id(node_type, label), curated 0/1);
  add_edge() kaldırıldı, yerine add_curated_edge() geldi - HER İKİ node
  graph_nodes'ta curated=1 olmadan edge oluşturulamıyor (Stage Z'nin
  zorunlu-kapı kısıtı korundu). list_context_map() artık graph_nodes/
  graph_edges'ten okuyor; edge filtresi curated=1 AND requires_dual_approval=1
  - bu, curate_graph_edge()'in (Stage S, advisory) işaretlediği auto-graph
  edge'lerinin context-map'e sızmasını engelliyor. canvas_server.py'ye giden
  {"nodes":[], "edges":[]} sözleşmesi değişmedi.

- 2026-07-29 - complete_task() artık zorunlu kanıt istiyor (Stage AE, Codex
  önerisi) - var olan evidence tablosuna bağlandı, yeni tablo açılmadı.
  completed_by_role ayrı parametre istemek yerine get_role()'den otomatik
  türetiliyor - zaten bilinen bilgiyi tekrar sormamak için.
  verified_default_path Codex'in önerisinde belirsizdi, "evidence status !=
  fail" kontrolü olarak yorumlandı.

- 2026-07-29 - KRITIK BUG DUZELTILDI (Stage AF): Claude Code'un /clear'i yeni bir session_id urettigi dogrulandi (canli test: iki farkli UUID). Bu, rolun /clear sonrasi sessizce brozzer'a dustugu anlamina geliyordu - artik en son declare edilen rol yeni session_id'ye miras aliniyor.

- 2026-07-29 - Project resolution eklendi (Stage AG) - cwd'nin son klasor adi ile projects.name eslestiriliyor, basit bir eslesme. Eslesmezse Haroon'un ilkesine uygun sekilde 'unmapped' acikca belirtiliyor, sessizce bir projeye baglanmiyor.

- 2026-07-29 - client_session_uuid destegi eklendi (Stage AH) - Claude Code'un GERCEKTE bu alani saglayip saglamadigi dogrulanmadi (payload'da yoksa sessizce None kullaniliyor, hata vermiyor). Varsa Stage AF'deki 'en son rol' fallback'inden daha kesin bir baglanti sagliyor.

- 2026-07-29 - post_compact_recap.py ayri hook olarak cikarildi (Stage AI) - Haroon'un PostCompact hook'unun karsiligi, davranis degismedi, sadece kod session_start.py'den ayrildi.

- 2026-07-29 - auto-safepoint guclendirildi (Stage AJ) - opsiyonel git auto-commit eklendi (BBB_AUTO_COMMIT=1 ile acilir, varsayilan kapali - her turda otomatik commit riskli olabilecegi icin bilerek opt-in yapildi).

- 2026-07-29 - session_warn.py eklendi (Stage AK) - Codex'in Session Lifecycle listesindeki 9 alt-maddenin sonuncusu, boylece bu madde tamamlandi. Mevcut session_score() (Stage Y) fonksiyonunu kullaniyor, yeni bir skorlama mantigi icat etmedi.
