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

- 2026-07-29 - compact_snapshot() genisletildi ve clear_recovery_verified() eklendi (Stage AL) - Codex'in 4. maddesi. Tum yeni alanlar VAR OLAN tablolardan (dispatch_runs, evidence, answer_receipts, documents, graph_nodes/edges) okunuyor, yeni tablo acilmadi. Fail-soft: eksik veri hata vermiyor, 0/None/False ile devam ediyor.

- 2026-08-01 - Stage AL duzeltmesi: has_active_document artik bool | None - projects tablosunda session_id sutunu olmadigi icin (gercek bir session->project yolu yok) None ("bilinmiyor") donduruyor, False ("yok") degil; sutun ileride eklenirse mc.py degismeden gercek degeri hesaplayacak sekilde dinamik kontrol ediliyor. Ayrica recent_decision_ids(limit=2) eklendi - recent_decisions() metin icin oldugu gibi kaldi, ID donduren ayri bir fonksiyon.

- 2026-07-29 - Memory frontmatter zenginlestirildi + project-scoped arama eklendi (Stage AM). Curator'in 'oneri/approval' modeline gecirilmesi YAPILMADI (mevcut tam-otomatik daily_check felsefesiyle celisiyor, ayri bir karar gerektirir). Yeni evidence_level alani, Stage X'teki sayisal confidence'in YERINE degil yanina eklendi - ikisi farkli seyler (biri sayisal guven skoru, digeri Haroon'un epistemik kategorisi).

- 2026-07-29 - Stage AN (1/3): doc_index/symbol_index temel sema. Duzeltme notu: onceki 'Stage P code memory' iddiasi YANLISTI - o is hic yapilmamisti (ne code_index tablosu ne de search_code/index_codebase fonksiyonlari vardi); ayrica ast tabanli bir _extract_symbols mantigi da hic yoktu, bu Stage AN ile gercek ilk implementasyon, sifirdan yazildi. Simdilik sadece .py dosyalari indeksleniyor, diger diller (js/ts/sql/sh) sonraki bir adima birakildi.

- 2026-07-29 - Stage AN (2/3): cyg_exists/cyg_similar eklendi - Haroon'un Golden Rule'unun (reuse-first) pratik karsiligi. pretooluse_log.py artik bunu kullaniyor. Duzeltme notu: onceki 'Stage T search_code bilgilendirme mantigi' iddiasi da YANLISTI - pretooluse_log.py hicbir zaman kod-yeniden-kullanim kontrolu yapmiyordu, bu bilgilendirme (engelleme yok) ilk kez burada eklendi.

- 2026-07-29 - Stage AN (3/3) TAMAMLANDI: CLI scriptleri eklendi. Codex'in 6. maddesi (Graph Memory/Code Graph) artik tam - doc_index/symbol_index semasi, cyg_exists/cyg_similar, ve CLI araclarinin hepsi gercek, test edilmis.

- 2026-07-29 - Stage AP: task tamamlama Codex'in 3. maddesine gore sikilastirildi - subtask kontrolu, test-kaniti zorunlulugu (require_test_evidence), ready_for_review ara durumu eklendi. Tam 5 durum (pending/in_progress/blocked/ ready_for_review/verified_complete) yerine 4 kullanildi - 'verified_complete' ile 'done' ayni sey sayildi, isim degismedi (geriye donuk uyumluluk icin).

- 2026-07-29 - Stage AQ: data-ownership matrix (docs/data-ownership-matrix.md) oluşturuldu - bizde sunucu olmadığı için tüm veriler 'local-only', sync/conflict senaryoları N/A. Bu madde Haroon'un sistemi için ANLAMLI (Mini/HOSNI ayrımı var), bizim için büyük ölçüde teorik - şablon olarak hazırlandı, gerçek kod ihtiyacı minimal (sadece db_health_check()).

- 2026-07-29 - Stage AR: exact/alias/normalize katmanı eklendi (Haroon'un ekip belgesi, madde 5) - SADECE memory/log_lesson için, tasks/decisions/rules'a genişletilmedi (kapsam dışı, ayrı bir iş). normalize_term basit bir heuristik (gerçek bir dilbilimsel tekil/çoğul çözümleyici değil). "Human review for uncertain matches" de kapsam dışı - mevcut tam-otomatik felsefe korundu.

- 2026-07-29 - Stage AS: memory_index'e content_hash, timestamps, status/replaced_by, version eklendi (Haroon'un ekip belgesi, madde 6). Owner alanı EKLENMEDİ (tek kullanıcı olduğu için anlamsız, kapsam dışı) - decisions.md'ye bu gerekçe not edildi. Ayrı bir 'audit history' tablosu açılmadı - version/updated_at/status zaten temel bir audit izi sağlıyor, tam log tablosu şimdilik gereksiz karmaşıklık.

- 2026-07-29 - Stage AT: çelişki TESPİTİ eklendi (Haroon'un ekip belgesi, madde 7) - otomatik ÇÖZME değil, sadece görünür uyarı (human-review ruhuna uygun, ama bizde 'insana gönder' diye ayrı bir kuyruk yok, direkt session_start çıktısında gösteriliyor). 'Local vs server' senaryosu kapsam dışı - sunucu yok.

- 2026-07-29 - Stage AU: failure visibility eklendi (Haroon'un ekip belgesi, madde 8) - health_events tablosu + get_health_state() 3 durumlu (healthy/degraded/blocked). 'Recovery required/validation failed/sync pending' durumları kapsam dışı - sunucu/sync olmadığı için anlamsız. ÖNEMLİ: mevcut fail-soft davranış DEĞİŞMEDİ, sadece artık loglanıyor - session'lar hâlâ hatalardan dolayı çökmüyor, sadece artık görünür.

- 2026-07-30 - Stage AV: assistant_server.py artık sunum arayüzü olarak tüm özellikleri (görev yönetimi, sağlık durumu, session score, çelişki tespiti) gerçekten kullanıyor - önceden bunlar sadece Claude Code hook'larında çalışıyordu, chat arayüzünde hiç görünmüyordu.

- 2026-07-30 - Stage AW: assistant_server.py'ye exists: ve projects komutları eklendi (ucuz, salt-okunur). Dispatch ve context-map bilerek chat'e bağlanmadı - dispatch her çağrıda gerçek token harcar, context-map görsel bir araç (Canvas'ın işi). Sunum planı: chat arayüzü (rol, görev, karar, hafıza, sağlık, exists, projects) + Canvas (context-map, session/task görselleştirme) birlikte, iki ayrı pencere olarak gösterilecek.

- 2026-07-30 - Stage AX: decision: komutu artık memory'ye de yazıyor (aranabilir); yeni 'confirm task N with test:' komutu gerçek test-kanıtıyla complete_task'ı geçerli şekilde tamamlıyor. Eski 'complete task N' hâlâ guard-demo amaçlı kalıyor, kasıtlı olarak reddediyor.

- 2026-07-30 - Stage AY: start_task() ve chat komutu 'start task N' eklendi - Stage AX'teki tam döngüyü (todo -> in_progress -> kanıtla tamamlama) artık tamamen chat üzerinden yapılabilir hale getiriyor.

- 2026-08-05 - Fable'ın BUS incelemesi sonrası düzeltme: heal_memory() artık otomatik uygulamıyor, onay adımı (approve_merge) zorunlu hale getirildi - BBB'nin daha önce bu yüzden gerçek veri kaybettiği desenden kaçınmak için.

- 2026-08-05 - BUS görevi Aşama 1: graph_edges'e gerçek "calls"/"references" edge'leri eklendi (Fable'ın tespiti: graph_edges hem bizde hem BBB estate'inde boştu - node'lar/semboller indekleniyordu ama edge hiç yazılmıyordu).

  Neden "calls" ve "references", "import" değil: ikisini seçtim çünkü ikisi de tek bir dosyanın import satırından değil, gerçek kullanımdan çıkarılıyor - "reuse-before-build" (Golden Rule, Stage AN) sorusuna doğrudan cevap veriyorlar: "X'i kim çağırıyor" ve "X hangi dokümanda geçiyor". "import" edge'i sadece "A dosyası B modülünü import ediyor" der - hangi sembolün gerçekten kullanıldığını söylemez, ve import grafiği zaten Python'un kendi ast.Import/ImportFrom düğümlerinden triviyal olarak çıkarılabilir; buradaki asıl eksik olan (Fable'ın BBB'de canlı doğruladığı boşluk) sembol-seviyesinde kim-neyi-çağırıyor bilgisiydi.

  "calls" edge'i (index_symbols() içinde): her sembolün gövdesindeki ast.Call düğümleri (_direct_calls(), nested def/class sınırında durur - iç içe bir fonksiyonun çağrıları kendi sembolüne ait sayılır, dışarıdakine değil) toplanıp proje-çapında bir isim indeksine (name_index) karşı çözülüyor. Sadece TAM OLARAK BİR eşleşme varsa edge yazılıyor - aynı isimde birden fazla sembol varsa (örn. iki farklı sınıfın aynı adlı metodu) sessizce atlanıyor. Gerekçe: yanlış bir edge, hiç edge olmamasından daha kötü - "kim X'i çağırıyor" sorusuna yanlış cevap vermektense hiç cevap vermemeyi seçtim. Bilinen sınır: proje içinde rastgele tek bir isimde çakışan bir stdlib/attribute çağrısı (örn. some_dict.get(...)) proje içinde de "get" adında BENZERSİZ bir metod varsa yanlışlıkla ona bağlanabilir - import/tip çözümlemesi yapılmıyor, isimden statik eşleştirme.

  "references" edge'i (index_docs() içinde): bir doküman metninde, zaten symbol_index'te olan bir sembol adı (kelime sınırıyla, \b...\b) geçiyorsa doc -> symbol edge'i yazılıyor. Burada calls'ın aksine birden fazla eşleşme varsa hepsine edge yazılıyor - çünkü doküman metni doğası gereği belirsiz (hangi "build" metodundan bahsettiği net değil), tek bir doğru hedef aramak yanlış bir kesinlik iddiası olurdu; calls'ta ise gerçek bir çağrı gerçekten TEK bir fonksiyona gider, o yüzden orada belirsizliği atlamak doğru, burada hepsini bağlamak doğru.

  Yazma şekli: görevde istendiği gibi add_curated_edge() (Stage Z/AC'nin insan-onaylı context-map kapısı) DEĞİL, düz _upsert_graph_edge() kullanıldı - curated=0, authority="auto-call-analysis" / "auto-doc-reference", confidence="inferred". Bu arka-plan/otomatik doldurma, insan-onaylı context map ile karışmıyor; _upsert_graph_edge zaten ON CONFLICT(src,dst,type) DO UPDATE ile upsert yaptığı için tekrar çalıştırınca çoğalmıyor (idempotent, teste bağlandı).

  Kanıt (gerçek proje üzerinde): index_symbols/index_docs çalışmadan önce graph_stats()['edges'] == 0; çalıştıktan sonra 1386 (1318 calls + 68 references, 930 sembol + 11 doküman indekslendi). Yeni find_callers() fonksiyonu (mevcut genel traverse_graph(direction="in")'i sarmalıyor, yeni bir traversal mantığı icat etmedi) edge'ler yazılmadan önce [] döndürüyor, yazıldıktan sonra gerçek çağıranları döndürüyor - örn. find_callers("_upsert_graph_edge") 6 gerçek çağıran buluyor (index_symbols, index_docs, add_curated_edge, load_graph_edges, _upsert_memory_graph, ...). Bu "edge'siz bulamaz, edge'le bulur" kanıtı test_code_index.py'de test_find_callers_returns_nothing_without_edges_but_finds_with_them olarak da teste bağlandı.

  Kapsam dışı bırakılan: Stage 2 (BBB estate'e PR olarak taşıma) ayrı bir iş - bu görev sadece kendi prototipimizdeki (bbb-lite) düzeltmeyi kapsıyor.

- 2026-08-05 - BUS görevi Aşama 2 (hazırlık): Haroon'un gerçek graph kodu (haroon-bbb-sync, main dalı - "rebuild" değil, GitHub API ile salt-okunur incelendi, clone yok) okundu. Gerçek delik doğrulandı: lib/bbb_graph_edges.py'deki tek edge-yazma yolu load_edges(), bu da SQL'e değil, git-senkronize ~/.bbb/graph/edges.jsonl dosyasından okuyor; scripts/bbb-graph-ingest.py ve scripts/bbb-graph-doc-upsert.py (onların sembol/doküman indeksleyicileri) graph_edges'e hiç dokunmuyor - append_edge_source() fonksiyonu var ama hiçbir yerden çağrılmıyor. Bu, Fable'ın canlı gözlemlediği "248 doc + 13.599 sembol indekslenmiş, graph_edges BOŞ" durumunun tam sebebi.

- 2026-08-05 - BUS görevi Aşama 2 (gerçek plan, taslak): mc.export_sync_edges() eklendi - bizim calls/references edge'lerimizi (Stage BUS-1) Haroon'un edges.jsonl şemasına (src/dst/type/evidence/weight/project_id) çevirip okuyor; bin/bbb_graph_edge_builder.py bunu dosyaya yazan CLI (onların scripts/bbb-graph-edge-builder.py'sinin taslağı - isimlendirme, mevcut bin/bbb_graph_ingest.py <-> scripts/bbb-graph-ingest.py eşleşmesiyle aynı desende: bizde alt çizgi, onlarda tire).

  Edge tipi eşlemesi - calls -> "depends-on", references -> "derived-from": "calls"/"references" icat etmek yerine ONLARIN mevcut sözlüğüne (depends-on, supersedes, part-of, derived-from) uyduk, çünkü load_edges() zaten bilinmeyen bir type'ı sessizce "unknown_types" listesine düşürüyor - kendi isimlerimizi göndersek PR'da hiçbir şey KIRILMAZ ama edge'ler onların traverse()/depends_on()/impact() fonksiyonlarına görünmez kalırdı, bu da tüm işi anlamsız kılardı. "A, B'yi çağırıyor" => "A, B'ye bağımlı" (depends-on) doğal bir çeviri - impact() zaten "bunu değiştirirsem ne kırılır" sorusunu depends-on'un tersini (direction="in") alarak cevaplıyor, tam da calls-edge'in çağıran tarafını bulmak istediğimiz şey. "Doküman X sembolünden bahsediyor" => "doküman X'ten türetilmiş" (derived-from) tam eşleşme değil (bizim "references" daha zayıf bir ilişkiyi ifade ediyor - doküman sembolü türetmiyor, ondan BAHSEDİYOR) ama onların sözlüğünde "bir şeyin başka bir şeyden geldiği" fikrini taşıyan tek tip bu; en yakın uyum buydu, gerçek bir "mentions" tipi PR'da ayrıca önerilebilir ama şimdilik mevcut sözlüğe sadık kalındı.

  Format birebir eşleşmesi: çıktı alanları TAM OLARAK load_edges()'in okuduğu alanlar (src, dst, type, evidence, weight, project_id) - fazlası yok, PRIMARY KEY (src,dst,type) ile ON CONFLICT DO UPDATE davranışı zaten aynı. src/dst referansları bizim iç slug'lı graph_nodes.node_id'miz DEĞİL (onların şemasında node tablosu yok) - "{dosya_yolu}::{nitelikli_ad}" (sembol) veya sadece dosya yolu (doküman) gibi okunabilir, forward-slash'lı (Windows'ta indekslense bile) string'ler; bu referans biçiminin onların "sembol adı" konvansiyonuyla birebir aynı olduğu KESİN DEĞİL (bbb_graph_index.py'nin symbol-hit şeklinde ayrı bir "ref" alanı görülmedi) - bu, gerçek PR incelemesinde açık soru olarak bırakıldı.

  Test: 127.0.0.1:8889'a hiç bağlanılmadı (kısıt gereği) - sadece kendi bbb-lite projemiz üzerinde bin/bbb_graph_edge_builder.py çalıştırılıp üretilen jsonl elle incelendi (1394 satır, hepsi geçerli JSON, "depends-on"/"derived-from" tipleri doğru dağılmış) ve mc.export_sync_edges() + CLI için 6 yeni test eklendi. Henüz Haroon'un reposuna push edilmedi - onay bekleniyor.

- 2026-08-05 - export edge builder'a skip_dirs parametresi eklendi - memory/ atlaması bbb-lite'a özel bir varsayımdı, Haroon'un gerçek reposunda farklı davranması gerekebilir, artık parametrik. AYRICA NOT: bu script'in kendisi mekanik olarak taşınabilir ama gerçek haroon-bbb-sync ağacına karşı HENÜZ ÇALIŞTIRILMADI (clone yasağı nedeniyle) - PR'da bunu açıkça belirtmemiz gerekiyor, doğrulama Haroon'un kendi tarafında yapılmalı.

- mc.db .gitignore'da (satır 1) - hicbir zaman git'e girmedi, versiyonlanmadi. Bu yuzden 5 Agustos'taki '0 -> 1386 edge' kaniti (BUS-1, calls/references) o anki mc.db durumuna aitti ama dosyanin kendisi hic commitlenmedigi icin sonraki oturumlara persist olmadi - bugun kontrol edildiginde graph_edges'te calls=0, references=0 bulundu (sadece eski 10 depends-on + 175 mentions, son guncelleme 1 Agustos). 2026-08-07: kullanicinin acik onayiyla bin/bbb_graph_ingest.py GERCEK mc.db uzerinde calistirildi (kopya degil): graph_edges 185 -> 1634 (calls: 0 -> 1355, references: 0 -> 94, depends-on ve mentions degismedi), symbol_index 730 -> 961, doc_index 9 -> 11. Ardindan assistant_server.answer_message('callers: get_role') canli mc.db'ye karsi calistirilip engine='callers-command' ile 24 gercek callers dondugu dogrulandi.

- 2026-08-07 - Kişi-node tekilleştirme + tarihli hatırlatma: Haroon'un toplantıda anlattığı Muayyad örneğinin gerçek karşılığı. tasks.due_date (TEXT, nullable, "YYYY-MM-DD") eklendi; get_or_create_person_node(name) graph_nodes'ta node_type='person' arayıp yoksa oluşturuyor; create_reminder(description, due_date, people) add_task + her isim için get_or_create_person_node çağırıp task node'undan kişi node'una "involves" edge'i yazıyor; list_upcoming_reminders(within_days) açık (status != 'done') ve due_date bugün ile bugün+within_days arasında olan task'ları listeliyor.

  Neden yeni "involves" edge tipi eklendi (GRAPH_EDGE_TYPES'daki supersedes/depends-on/evidence-for/contradicts/derived-from'dan biri değil): bunların hiçbiri "bu görev şu kişiyle ilgili" ilişkisini ifade etmiyor - en yakını "evidence-for" ama o kanıt-iddia ilişkisi, kişi-görev değil. "calls"/"references" (BUS-1, 2026-08-05) için zaten GRAPH_EDGE_TYPES dışına çıkma emsali var (authority sınıflandırması edge type sözlükte yoksa "unclassified"e düşüyor, bu kabul edilebilir - _authority_class_for_edge zaten authority parametresi açıkça verilirse onu kullanıyor). Burada da authority="reminder", confidence="stated" (ne "inferred" - algoritmik çıkarım değil, ne "verified" - insan-onaylı context-map değil; kullanıcının doğrudan söylediği bir gerçek) ile _upsert_graph_edge() düz kullanıldı - add_curated_edge() DEĞİL, çünkü o insan çift-onay kapısı (Stage AC/Z) hatırlatma akışına uygun değil.

  Tekilleştirme mekanizması: yeni bir normalizasyon fonksiyonu yazılmadı, mevcut _graph_node_id("person", name) -> _slug() zaten kullanılıyor (küçük harfe çevirip alfanumerik-olmayanı tire yapıyor) - "Haroon", "haroon", " HAROON " hepsi aynı node_id'ye ("person:haroon") düşüyor, index_symbols/index_docs'un "symbol"/"doc" node'ları için zaten yaptığı şeyin aynısı.

  Test (9 yeni, hepsi geçti - test_mc.py toplam 432 test, hepsi ok): aynı ismin tekrar oluşturulmadığı (created=False ikinci çağrıda), büyük/küçük harf ve boşluk farkının aynı node'a düştüğü, yeni ismin created=True döndüğü, create_reminder'ın "Erdi var, Haroon yeni" senaryosunda doğru created değerlerini döndürdüğü, involves edge'inin gerçekten yazıldığı, aynı kişinin iki ayrı hatırlatmada tekrar node almadığı, list_upcoming_reminders'ın pencere dışını (çok uzak tarih, tarihsiz task) ve geçmiş/tamamlanmış hatırlatmaları dışarıda bıraktığı doğrulandı.

- 2026-08-07 - remind: komutu "brozzer rolü engelliyor" gibi göründü ama gerçek sebep zombie process'ti (port 8100'de eski+yeni süreç çakışması) - rol izni hiçbir zaman remind:/task:/decision:'ı kontrol etmiyor. Ders: HTTP arayüzünde garip davranış görülürse, önce kaç sürecin aynı portu dinlediğini kontrol et.

- 2026-08-07 - remind: ve decision: komutlarında tekrarlanan bir örüntü: model, kendi context'inde "data izni False" gördüğünde, gerçekte kod-seviyesinde hiç kontrol edilmeyen bu izni "engelleyici" sanıp, kendi kendine "yapamadım" diye yanlış bir anlatı üretiyor - halbuki komutun kendisi izinden bağımsız çalışıyor. Bu, modelin kendi durumu hakkında halüsinasyon görebileceğinin somut bir kanıtı; bu yüzden her zaman gerçek DB kaydına bakılmalı, modelin anlatısına değil.

- 2026-08-07 - Bilinen sınırlama: create_reminder() görev-seviyesinde tekrar kontrolü yapmıyor (sadece kişi-node seviyesinde dedup var). Aynı description+due_date ile remind: birden fazla kez çağrılırsa, her seferinde yeni bir task_id açılıyor (test: "test tekrar kontrolü" iki kere gönderildi, task_id 12 ve 13 - iki ayrı kayıt). Şimdilik bilerek düzeltilmedi, ileride istenirse ele alınabilir.

- 2026-08-09 - Varlık tekilleştirme kişi dışına genişletildi (organization/client/project) - Haroon'un toplantıda verdiği "Genie şirketi + müşterisi" örneğinin karşılığı. get_or_create_entity_node(name, entity_type="person") eklendi - get_or_create_person_node() ile birebir aynı mantık (dedup _graph_node_id(entity_type, name) slug'ı üzerinden), tek fark node_type artık parametrik. get_or_create_person_node() SİLİNMEDİ, geriye dönük uyumluluk için get_or_create_entity_node(name, "person") çağıran ince bir sarmalayıcıya çevrildi - mevcut çağıranlar (create_reminder) ve testler değişmeden geçiyor. entity_type node_id'nin bir parçası olduğu için aynı isim farklı tiplerde çakışmıyor ("person:genie" ile "organization:genie" ayrı node'lar) - bir kişi ile aynı adı taşıyan bir şirket birbirine karışmıyor.

  link_entities(name_a, relation, name_b, type_a="person", type_b="person") eklendi: her iki varlığı get_or_create_entity_node ile bulup/oluşturup aralarına "relates-to" tipinde bir edge yazıyor - relation (örn. "works-with") edge type'ı değil, evidence alanına yazılıyor, çünkü GRAPH_EDGE_TYPES kapalı bir sözlük (supersedes/depends-on/...) ve keyfi varlıklar arası serbest-metin ilişkiler oraya uymuyor; "relates-to", create_reminder()'ın task->person edge'leri için "involves"in oynadığı rolün aynısını oynuyor. Düz _upsert_graph_edge (curated=0, authority="link_entities", confidence="stated") kullanıldı - add_curated_edge() DEĞİL, insan çift-onay kapısı (Stage AC/Z) burada uygun değil.

  Test (6 yeni, hepsi geçti): get_or_create_person_node'un hâlâ dedup yaptığı, aynı isim+aynı tipte entity_node'un dedup yaptığı, AYNI isim FARKLI tipte iki ayrı node oluşturduğu (person:genie vs organization:genie), doğru node_type'ın yazıldığı, link_entities'in relation'ı evidence'a yazan doğru "relates-to" edge'i oluşturduğu, ve link_entities'in ikinci çağrıda hem iki varlığı hem edge'i dedup ettiği (created=False, edge_created=False) doğrulandı.

- 2026-08-09 - Kararlar artık graph'a bağlanabiliyor (concerns edge'i) - Haroon'un "cold decisions graph'ta korunur" vizyonunun karşılığı. mc.link_decision_to_entity(decision_packet_id, entity_name, entity_type="organization") eklendi: decision_packets satırını doğrulayıp node_type='decision' bir graph node oluşturuyor (node_id = decision:<packet_id> - uuid4 hex zaten geçerli bir _slug() olduğu için bu round-trip metadata_json'a bakmadan tersine çevrilebiliyor), get_or_create_entity_node ile varlığı bulup/oluşturuyor, aralarına "concerns" tipinde bir edge yazıyor (düz _upsert_graph_edge, curated=0 - link_entities'in "relates-to"suyla aynı güven seviyesi, insan çift-onay kapısı değil). mc.get_decisions_for_entity(entity_name, entity_type="organization") eklendi: aynı yönde ("concerns", decision -> entity) graph_edges'i tarayıp node_id'den packet_id'yi geri çıkarıp decision_packets satırlarını döndürüyor; bilinmeyen bir varlık için varlık node'u oluşturmuyor, sadece boş liste dönüyor (bu bir okuma, get_or_create değil).

  bin/assistant_server.py'deki "decision: <metin>" komutu opsiyonel bir "| <varlık adı>" eki destekliyor artık (örn. "decision: HOSNI yerine SQLite kullanıyoruz | Genie") - _split_decision_entity() son "|"den sonrasını varlık adı olarak ayırıyor, verilirse mc.link_decision_to_entity() çağrılıyor; ek yoksa davranış tamamen eskisi gibi (entity=None, hiçbir şey linklenmiyor). Yeni "decisions-for: <varlık adı>" komutu mc.get_decisions_for_entity()'yi çağırıp sonucu listeliyor (exists:/callers:/depends:/mentions: ile aynı salt-okunur komut deseni, gerçek Claude çağrısı yok).

  Test (7 mc.py + 3 assistant_server.py = 10 yeni, hepsi geçti - toplam 867 test, hepsi ok): link_decision_to_entity'nin doğru "concerns" edge'ini (curated=0) yazdığı, ikinci çağrıda hem varlığı hem edge'i dedup ettiği, bilinmeyen packet_id için ValueError fırlattığı; get_decisions_for_entity'nin linkli kararı bulduğu, linksiz kararları görmezden geldiği, entity_type'a göre ayrım yaptığı (organization vs person aynı isimde çakışmıyor), birden fazla kararı bulduğu; ve gerçek (mock'suz) bir senaryoda "decision: X | Genie" ile paket oluşturulup ("| Genie" eki decision metnine sızmadan) "decisions-for: Genie" ile geri bulunduğu, "|" eki olmayan düz "decision:"in hiçbir "concerns" edge'i yaratmadığı, bilinmeyen bir varlık için "none found" döndüğü doğrulandı.

- 2026-08-09 - compact:/clear-sim: eklendi - GERÇEK Claude Code context-sıkıştırması burada yok (düz HTTP sunucusu), bu yüzden altındaki mekanizmaları (snapshot/karşılaştırma, rol miras alma) doğrudan çağırarak simüle ediyoruz. Gerçek /clear testi hâlâ sadece Claude Code terminalinde (hooks açıkken) yapılabilir.

  "compact:" (bin/assistant_server.py) mevcut session_id için sırasıyla mc.save_snapshot(sid, "pre") ve mc.save_snapshot(sid, "post") çağırıyor (aralarında hiçbir mutasyon yok - back-to-back), sonra Stage R'nin gerçek mc.compare_snapshots(sid)'ini çalıştırıp sonucu gösteriyor: bulgu yoksa "compact simulation: N/N items intact" (N = mc.compact_snapshot()'daki task+decision+checklist+role_declared toplamı, hooks/post_compact_recap.py'nin "compact recovery verified" satırıyla aynı hesap), bulgu varsa "compact simulation: DEGRADED" + bulgu listesi. Dürüstlük notu: pre/post arka arkaya alındığı için bu komut sadece "kayıp yok" durumunu gerçekten üretebilir - gerçek bir /compact sırasında olabilecek kayıp senaryosunu (context sıkışırken bir şeyin silinmesi) bu haliyle tetikleyemez, sadece kayıp *tespit* mekanizmasının (compare_snapshots) çalıştığını kanıtlar.

  "clear-sim: <yeni_session_id>" mevcut canlı session_id'ye DOKUNMUYOR - sadece verilen yeni id için hooks/session_start.py'nin Stage AF mantığının birebir aynısını çalıştırıyor: mc.get_most_recent_role() ile en son deklare edilen rolü buluyor, varsa mc.set_role(yeni_id, rol) ile yeni id'ye yazıyor ("role inherited: <rol>"), yoksa session_start.py'deki gibi set_role çağırmadan "NOTE: no previous role found, defaulting to brozzer" diyor; ardından mc.clear_recovery_verified(yeni_id) sonucunu (role, verified flag, açık görev sayısı, checkpoint durumu) gösteriyor.

  Test (4 parsing + 2 compact + 2 clear-sim = 8 yeni, hepsi geçti): compact:'in gerçek compact_snapshots tablosuna 'pre' ve 'post' satırı yazdığı ve mc.compare_snapshots()'in bağımsızca çağrılınca da aynı "kayıp yok" sonucunu verdiği (mock yok); DEGRADED formatlamasının mc.compare_snapshots()'in gerçek dönüş şeklini (bulgu string listesi) doğru bastığı (sadece bu testte compare_snapshots stub'landı, çünkü _compact_report kendi pre/post'unu arka arkaya aldığı için kendi çağrısı içinde gerçek bir kayıp senaryosu üretemez); clear-sim'in rolü BAŞKA bir session'dan (mock değil, gerçek mc.get_most_recent_role/set_role) doğru şekilde yeni id'ye miras aldırdığı VE canlı "web" session'ının role_exists=False kalarak dokunulmadığının kanıtlandığı; önceki rol hiç yoksa set_role çağrılmadan dürüst NOTE mesajının döndüğü doğrulandı.

- 2026-08-10 - Halüsinasyon örüntüsü 3. kez doğrulandı (decision:/remind: komutları) - model her zaman "data izni yok, kaydedemedim" diyor ama komut GERÇEKTEN kaydediyor (bu sefer decision_packets, K3-seat kararı, packet id=0aa4959...). Bu artık tek seferlik değil, TUTARLI bir davranış - modelin kendi "izin" algısı, gerçek guard mantığıyla hiç örtüşmüyor. Sunumda gösterilecek net bir örnek.

- 2026-08-12 - GitHub'da `TrustSourcing/bbb-os-core` adında yeni bir repo bulundu -
  kullanıcı bunu "bbb-lite'ın mantıksal olarak gelişmiş hali" olarak tanımladı ve bbb-lite'ı
  buna göre geliştirmek istedi. İnceleme sonucu bu repo'nun genel amaçlı bir şablon
  OLMADIĞI, Haroon'un HOSNI adlı makinede çalışan CANLI continuity sistemi olduğu ortaya
  çıktı: `hooks/core_hook.py` kanonik kimlik/politika/hafızayı bir HOSNI Mission Control
  API'sinden enjekte ediyor (repo'nun kendi CLAUDE.md'si "NON-CANONICAL" diyor),
  `PROVENANCE.md` doğrudan `/home/hosni/elohi-voice` ve "Lucy'nin CANLI beyni"ne referans
  veriyor - yani bizim CLAUDE.md'deki sert sınırın (`/home/hosni/elohi-*`, port 8890/8891 -
  asla dokunulmaz, canlı müşteri sistemi) bahsettiği ekosistemin ta kendisi. Ayrıca
  SSH tünelleri, systemd daemon'lar, npm/React web katmanı, Whisper/Kokoro/Telegram
  entegrasyonları içeriyor - bunların hiçbiri bbb-lite'ın "SQLite-only/tek dosya/sunucu
  yok/stdlib-only" kurallarıyla uyuşmuyor.

  Kullanıcıyla birlikte kapsam netleştirildi: bbb-os-core salt-okunur referans (haroon-bbb-sync
  ile aynı kural - asla clone edilmez, asla yazılmaz), CLAUDE.md'nin sert kısıtları SABİT
  kalır, hedef "%90+ mantıksal/özellik benzerliği" - mimari değil. voice/telegram/SSH/
  HOSNI-gateway/daemon kısımları ve bir `phases` tablosu tamamen kapsam dışı bırakıldı.
  Araştırma bbb-lite'ın zaten `evidence`/`answer_receipts`/`health_events`/
  `decision_packets`/entity-decision graph gibi birçok bbb-os-core kavramını farklı adlarla
  ÖNDEN uyguladığını, hatta graph/decision alanında bbb-os-core'un önünde olduğunu ortaya
  çıkardı. Gerçek boşluklar 5 maddeye indirgendi ve hepsi SQLite-only/tek dosya (`mc.py`)
  kısıtları içinde uygulandı:

  1. **reuse_receipts** - `cyg_exists()`/`cyg_similar()` kararlarını (`reuse`/`build_new`/
     `no_match`) loglayan yeni tablo + `log_reuse_decision()`; `bin/cyg_exists.py`'ye
     `--decision`/`--session` bayrağı eklendi.
  2. **task_receipts accept-step** (bbb-os-core'un "reported ≠ accepted" + certifier +
     completion-authority üçü birden) - `complete_task()`'ın ÜSTÜNE eklenen ayrı, opsiyonel
     bir kabul kaydı (`accept_task_completion()`), mevcut `task_activity`/`task_completions`/
     `complete_task()` davranışı DEĞİŞTİRİLMEDİ. Yetkilendirme, bbb-os-core'un sabit-kodlanmış
     tek onaylayıcı kişisi yerine mevcut `roles`/`ROLE_PERMISSIONS` mekanizmasına yeni bir
     "accept" yetkisi eklenerek yapıldı (architect/builder=True, brozzer/advisor=False).
     Kullanıcıyla onaylandı: kabul eden session'ın raporlayan session'dan farklı olması
     ZORUNLU DEĞİL (bbb-lite tek kullanıcılı, bbb-os-core'un ayrımı çok-kişili canlı sistem
     varsayımına dayanıyor).
  3. **run_maintenance_checks()** (bbb-os-core'un daemon'lu "Living Contract" self-check'inin
     daemon'suz hali) - `hooks/session_start.py` ve `hooks/stop_checkpoint.py`'ye fail-soft
     bağlandı, yeni scheduler/süreç YOK. 5 kontrol: PRAGMA integrity_check, orphaned
     graph_edges, orphaned context_edges, overdue reminders, memory currentness conflicts.
     Önemli tasarım notu: geçen kontrolller `health_events`'e YAZILMIYOR - `get_health_state()`
     son 1 saatteki HER health_events satırını sayıyor, geçen kontrolleri de yazmak tek
     temiz bir çalıştırmayı yanlışlıkla "blocked" gösterirdi (regresyon testiyle kanıtlandı).
     Sadece başarısız kontroller hem yeni `maintenance_runs` tablosuna hem `health_events`'e
     yazılıyor.
  4. **events_log.outbox_status** (bbb-os-core'un publication/outbox'ının minimal karşılığı) -
     nullable kolon, `record_event()` yeni satırlara `'pending'` yazıyor, eski satırlar
     `NULL` kalıyor (geriye dönük doldurulmuyor). `list_pending_events()`/`mark_consumed()`
     eklendi ama gerçek bir tüketici/kuyruk sistemi KURULMADI - bbb-lite'ın tüketecek canlı
     bir ikinci bileşeni yok, bu sadece ileride biri gelirse diye "okunmadı/okundu" etiketi.
  5. **GTD inbox/review** (`list_inbox_tasks()`, `list_overdue_reminders()`, `review()`) -
     yeni tablo yok; "inbox" için `tasks.sprint_id IS NULL` kullanıldı (plandaki ilk varsayım
     `project_id` idi ama `tasks` tablosunda böyle bir kolon yok, proje bağlantısı sadece
     `sprint_id -> sprints.project_id` üzerinden dolaylı - şema incelemesiyle düzeltildi).
     `bin/assistant_server.py`'ye salt-okunur "review"/"gözden geçir" chat komutu eklendi.

  Toplam ~60 yeni test (`test_reuse_receipts.py`, `test_task_acceptance.py`,
  `test_events_outbox.py`, `test_gtd_inbox.py`, `test_maintenance_checks.py` + mevcut
  dosyalara ekler), hepsi geçti. Yeni hook çağrısı (`run_maintenance_checks`) 9 ayrı hook
  testinin `_BOUND_FUNCS` listesine eklenmek zorunda kaldı - eklenmeden önce bu testler
  gerçek proje `mc.db`'sine karşı çalışıp orada bulduğu gerçek bir checkpoint yüzünden
  `semantic_search` mock'unu beklenmedik şekilde tetikliyordu (test izolasyonu kırılıyordu).
  Tam paket 915 test, hepsi yeşil (0 hata) - detaylar `.claude/plans/flickering-brewing-
  bentley.md` planında.

- 2026-08-18 - bbb-os-core memory/db parity implementasyonunun Adım 1-4'ü tamamlandı
  (`BBB-OS-CORE-MEMORY-DB-PARITY-IMPLEMENTATION-PLAN.md`, 2026-08-17 tarihli 8 maddelik
  liste - kapsam sadece memory/db katmanı, `mc.py` tek dosya, SQLite-only). Adım 1:
  `memory_index`'e `supersedes_id` + gerçek append-only versiyon geçmişi - içerik artık
  yerinde UPDATE edilmiyor, değiştiğinde eski satır `status='superseded'` olup `content`'i
  sabit kalıyor, yeni satır `supersedes_id` ile ona geri işaret ediyor. Adım 2:
  `events_log`'a `attempts`/`max_attempts` + `mark_failed()` (outbox retry/dead-letter
  sayacı). Adım 3: `memory_decisions` (immutable karar log'u, `BEFORE UPDATE`/
  `BEFORE DELETE` trigger'larıyla `RAISE(ABORT)`) + `memory_hold` (state machine:
  `open`/`claimed`/`resolved`/`expired`, terminal state kilidi, `claimed`'e her girişte
  `claim_epoch` ilerlemesi zorunlu) - bbb-os-core'un `memory_promotion_decision`/
  `memory_hold` çiftinin karşılığı; `detect_memory_conflicts()`/`detect_decision_conflicts()`
  artık her tespitte bir `hold` kararı yazıp hold açıyor (idempotency_key ile tekrar
  tespitte duplicate yaratmıyor), `approve_merge()` `dedupe`/`supersede` kararı yazan bir
  sarmalayıcı oldu, yeni `reject_conflict()` eklendi. Adım 4: `memory_index.authority`
  kolonu (`erdi`/`agent`/`inference`/`external`, bbb-os-core'un `haroon`/`team`/`agent`/
  `external`/`inference` enum'unun tek-kullanıcıya indirgenmiş hali) + `memory_confirmations`
  (immutable onay/düzeltme/red makbuzu) + `confirm_memory()`.

  En önemli tasarım kararı (ve bir düzeltme): `confirm_memory()` ilk yazıldığında
  `decision='rejected'` için `memory_index.status = 'rejected'` yazıyordu - bu değer
  bbb-os-core'a bakılmadan icat edilmişti. Kontrol edildi: bbb-os-core'un
  `sql/memory_fact.sql`'indeki `memory_fact.status` enum'u yalnızca
  `('candidate','accepted','superseded','retracted','discarded')` - `'rejected'` diye bir
  değer YOK; core'da red iki ayrı mekanizmayla yapılıyor (`retracted` - sadece
  `accepted`'ten başlayan, ayrı bir immutable receipt tablosu gerektiren admin-only bir
  CAS akışı; ve `memory_owner_confirmation_receipts.decision` - ki bu da `memory_fact.status`'a
  hiç yazılmıyor, sadece `entity_alias` lifecycle'ını besliyor). Düzeltme: `confirm_memory()`
  artık `decision='rejected'` VE `decision='corrected'` için ikisi de `memory_index.status =
  'superseded'` yazıyor; hangi kararın (`rejected` mi `corrected` mi) verildiği
  `memory_confirmations.decision` alanında immutable olarak kayıtlı kalıyor, `status` enum'u
  ayrıca genişletilmedi. Gerekçe: bbb-lite'ın kendi status sözlüğüne core'da karşılığı
  olmayan yeni bir değer eklemek yerine core'un davranışına (yeni bir state icat etmemek,
  kararın *nedenini* ayrı bir immutable receipt'te tutmak) sadık kalmak - hem şema
  drift'ini önlüyor hem ileride core'dan gerçek bir "rejected" karşılığı (örn. `discarded`)
  taşınması gerekirse önceden icat edilmiş bir değerle çakışmıyor.

  Test: 680 test, hepsi yeşil. Hiçbir şey commit edilmedi - tüm değişiklikler (`mc.py`,
  `test_mc.py`) working tree'de duruyor, açık onay bekliyor. Adım 5-8 henüz başlamadı.

- 2026-08-18 - Adım 5 (epistemic class + `memory_derivation`) tamamlandı - planın en
  karmaşık ve "opsiyonel/düşük öncelikli" işaretli maddesi. `memory_index`'e
  `epistemic_class` (`STATED`/`OBSERVED`/`IMPORTED`/`SUMMARY`/`DERIVED`/`SYNTHETIC`,
  varsayılan `IMPORTED` - bugüne kadar var olan her satır bir dosyadan geldi, erdi'nin
  chat'te doğrudan söylediği bir şey değil) + `source_span`/`source_digest` kolonları
  eklendi. bbb-os-core'un kuralı birebir taşındı: `epistemic_class='STATED'` olan satır
  hem `source_span` hem `source_digest` (sha256, 64 hex) dolu olmak zorunda - bu, tek bir
  yardımcı kolon icat etmeden `source_digest`'in kendi `ALTER TABLE ADD COLUMN`'ına
  binen bir `CHECK(epistemic_class != 'STATED' OR (source_span IS NOT NULL AND
  length(source_digest) = 64))` ile uygulandı. Doğrulandı: SQLite, `ALTER TABLE ADD
  COLUMN`'a eklenen bir `CHECK`'in tablodaki diğer (önceden var olan) kolonlara referans
  vermesine izin veriyor - planın öngördüğü ayrı bir "dummy" kolon gerekmedi.

  Yeni tablo `memory_derivation` (çıkarım kaydı: `child_id`, `parent_ids_json`,
  `parent_set_digest`, `kind` (`ABSTRACTION`/`BEHAVIORAL_INFERENCE`), `producer`,
  `confidence`, `status`, `created_at`, `revalidated_at`) - planın kendi SQL'i "en az 2
  parent" kuralını sadece bir yorum satırı (`-- json array, length >= 2`) olarak
  bırakmıştı; burada gerçek bir `CHECK(json_array_length(parent_ids_json) >= 2)` ile DB
  seviyesinde uygulandı (SQLite'ın gömülü JSON1 uzantısı - `sqlite3.sqlite_version` 3.50,
  `json_array_length` mevcut - doğrulanarak kullanıldı). Plandaki notla uyumlu: bugün
  bbb-lite'ta çok-kaynaklı bir çıkarım akışı (`heal_memory()` sonrası ortak sonuç çıkarma
  gibi) yok, o yüzden bu adımın teslimatı sadece şema - `memory_derivation`'a yazan bir
  Python fonksiyonu YOK, testler ham SQL INSERT ile şemayı doğruluyor. Fonksiyonel
  kullanım ayrı bir iş kalemi olarak plandaki gibi bırakıldı.

  6 yeni test (`MemoryEpistemicClassDerivationTestCase`), artı `test_init_db_creates_tables`
  tablo listesine `memory_derivation` eklendi. Tam paket 752 test, hepsi yeşil. Hiçbir şey
  commit edilmedi - `mc.py`/`test_mc.py`/`memory/decisions.md` working tree'de duruyor,
  açık onay bekliyor. Adım 6-8 henüz başlamadı.

- 2026-08-18 - Adım 6 (memory-özel hijyen taraması) tamamlandı - ama önce planın kendi
  metninde İKİ ayrı hata bulunup düzeltildi (kullanıcı onayıyla), implementasyona
  bunlardan sonra geçildi.

  **Hata 1 - handler sayısı:** plan metni "5 handler" diyordu, gerçek sayı `TrustSourcing/
  bbb-os-core`'da (`sql/memory_p3b2.sql`, `memory_hygiene_receipt.handler` CHECK'i;
  `lib/memory_p3b2_ops.py:891-893` `HYGIENE_HANDLERS` ile doğrulandı) **6**:
  `vector_reconcile`, `expiry_sweep`, `dedup_orphans`, `conflict_proposals`,
  `projection_coverage`, `archive_placement`. Kullanıcı kararı: sadece ilk 4 ile devam,
  `projection_coverage` (`lib/memory_p3b2_ops.py:939-943` - `memory_projection_outbox`
  state='completed' karşılığı arıyor) ve `archive_placement` (`:945-947`, tam bir
  arşiv-generation alt sistemine bağlı - `build_archive_generation`/`record_archive_
  scale_ramp`/`rollback_archive_generation`/`backup_and_verify_restore`,
  `lib/memory_p3b2_ops.py:1069-1437`) kapsam dışı bırakıldı - ikisi de bbb-lite'ta
  hiç olmayan ve planlanmayan bir outbox/projection tüketicisi ya da arşiv-generation
  alt sistemi gerektiriyor; bu, planın kendi "mimari kapsam dışı" listesiyle (HTTP
  gateway/SSH/Telegram/daemon/`phases`) aynı kategori.

  **Hata 2 - `dedup_orphans` eşlemesi:** plan metni bunu "graph_edges/context_edges'te
  yetim satırları bul (zaten kısmen var, genişlet)" diye tanımlıyordu - yanlış. Gerçek
  `dedup_orphans` (`lib/memory_p3b2_ops.py:923-928`) graph/edge ile ilgisiz: aynı `key`
  ile birden fazla `status='accepted'` `memory_fact` satırı var mı diye bakıyor (literal
  "dedup", "orphan" kelimesi isim benzerliğinden kaynaklı bir yanlış çağrışım). Kullanıcı
  kararı: bu maddeyi KAPAT, yeni kod yazma. Gerekçe: core'da bu kural (`sql/
  memory_fact.sql`'de doğrulandı - `key` üzerinde UNIQUE index/trigger yok) SADECE
  app-level'da zorlanıyor, dedup_orphans onun DB-seviyesinde bir telafi mekanizması.
  bbb-lite ise Adım 1'de `idx_memory_index_active_source_file` (`mc.py:600-602`, partial
  UNIQUE INDEX, `status='active'` olan satırlar için `source_file` üzerinde) ile bunu
  zaten DB seviyesinde garanti altına almıştı - yani bbb-lite bu hata sınıfına yapısal
  olarak bağışık. Bu "eksik özellik" değil, Adım 1'in zaten daha güçlü çözmüş olması;
  `orphaned_graph_edges`/`orphaned_context_edges` (mevcut, `mc.py:3072-3097`) ise zaten
  TAM ve eksiksiz - bunlar hiçbir bbb-os-core handler'ının karşılığı değil, bbb-lite'ın
  kendi icadı, "genişletilecek" bir eksik yok.

  **Uygulanan 3 yeni kontrol** (`run_maintenance_checks()`, `mc.py:3143-3203`), mevcut 5
  kontrolün detay formatına (düz string özet, liste DEĞİL) uydurularak:
  - `vector_reconcile` - `status='active' AND embedding IS NULL` olan `memory_index`
    satırlarını sayıyor.
  - `expiry_sweep` - `status IN ('replaced','superseded')` olan her satır için gerçek
    `search_memory()`'yi (mock değil, varsayılan `include_replaced=False` ile) çağırıp
    içeriğin hâlâ dönüp dönmediğini kontrol ediyor - bugün search_memory()'nin filtresi
    doğru çalıştığı için bu her zaman "pass" döner, ama gelecekte o filtre bozulursa
    (regresyon) bunu yakalayacak gerçek bir uçtan-uca kontrol.
  - `conflict_proposals` - `memory_hold`'da `state='open' AND retry_after <= now()`
    olan satırları buluyor.

  **retry_after doldurma mekanizması eklendi** (Adım 3'ten beri boştu - `mc.py`
  `record_memory_decision()`'ın `memory_hold` INSERT'i `retry_after` kolonunu hiç
  yazmıyordu, doğrulandı: `grep retry_after` sadece şema satırı + bunu önceden bilerek
  Adım 6'ya bırakan bir docstring notu (`expire_memory_hold()`, o zamanki haliyle)
  veriyordu). Önce core'da bir emsal olup olmadığı kontrol edildi: `lib/
  memory_fact_service.py`'de retry_after'ı gerçekten SET eden 3 ayrı çağrı noktası
  (`persist_path_decision_from_connection:2452-2454`,
  `apply_judged_fact_from_connection:2544-2546`, `persist_nonwrite:2923-2925`) var,
  ÜÇÜ DE `dt.timedelta(minutes=15)` kullanıyor - tek bir yerde değil, tutarlı bir
  kural. Kullanıcının "core'da emsal varsa onu kullan" talimatına uyularak kullanıcının
  kendi "günlük ölçek" önerisi değil, core'un gerçek 15-dakika değeri birebir taşındı
  (`mc.py` yeni `_MEMORY_HOLD_RETRY_AFTER = timedelta(minutes=15)` sabiti,
  `record_memory_decision()`'ın hemen üstünde). `record_memory_decision()`'ın
  `memory_hold` INSERT'i artık `retry_after = created_at + 15dk` yazıyor - `conflict_
  proposals` artık gerçek veride de tetiklenebilir.

  Test: 5 yeni handler testi (`test_maintenance_checks.py` -
  `test_detects_active_row_missing_embedding`,
  `test_expiry_sweep_passes_when_superseded_row_is_really_excluded` (gerçek,
  mock'suz), `test_detects_replaced_row_leaking_into_search_memory` (mock'lu -
  expiry_sweep'in mock'suz asla fail edemeyeceği, sadece gelecekteki bir regresyonu
  yakalayabileceği için), `test_conflict_proposals_passes_for_a_fresh_hold`,
  `test_detects_overdue_memory_hold`) + 1 yeni test (`test_mc.py` -
  `test_hold_decision_sets_retry_after_15_minutes_out`). Adım 5'teki kalıtım
  sürprizini tekrarlamamak için: bu sefer YENİ bir `McTestCase` alt sınıfı AÇILMADI -
  hepsi var olan sınıflara (`RunMaintenanceChecksTestCase`,
  `MemoryDecisionHoldTestCase`) eklendi, bu yüzden çarpma yok. `test_mc.py`: 752 -> 753
  (+1, tam beklenen). `test_maintenance_checks.py`: 12 -> 17 (+5, tam beklenen, bu
  sınıf zaten düz `unittest.TestCase`, `McTestCase` değil).

  **Ayrı bulgu - Adım 6 kapsamı dışında, ama tam-paket taramada ortaya çıktı:** bu adıma
  kadar hep sadece `test_mc.py` (veya `test_maintenance_checks.py`) tek başına
  çalıştırılmıştı, hiçbir oturumda `python -m unittest discover` ile TÜM `test_*.py`
  (28 dosya) birlikte çalıştırılmamıştı. İlk kez tam taramada (1099 test) 1 hata
  bulundu: `test_stage_as_memory_metadata.py::test_reindex_changed_content_bumps_
  version_and_updated_at` - bu Adım 6'nın DEĞİL, Adım 1'in (`supersedes_id` + gerçek
  append-only versiyon geçmişi) bir yan etkisi. Kök sebep: bu test dosyasının `_row()`
  yardımcı fonksiyonu (`test_stage_as_memory_metadata.py:47-52`) `SELECT * FROM
  memory_index WHERE source_file = ?` yapıp `ORDER BY`/`status` filtresi olmadan
  `.fetchone()` çağırıyor - Adım 1 öncesi (satır içi UPDATE, her `source_file`'ın tek
  satırı) bu güvenliydi, Adım 1 sonrası (aynı `source_file`'ın artık birden fazla
  satırı olabiliyor: eski `status='superseded'` + yeni `status='active'`) bu artık
  hangi satırın döneceğini garanti etmiyor. ÖNEMLİ: bu bir production-kod hatası
  DEĞİL - `mc.py`'nin kendi gerçek fonksiyonları (`mc.py:4088`, `mc.py:4159`) aynı
  sorguyu zaten doğru şekilde `AND status = 'active'` ile yapıyor; sadece bu test
  dosyasının yardımcı fonksiyonu Adım 1 ile birlikte güncellenmemiş, salt bir test
  açığı. "Sadece `run_maintenance_checks()` ve ilgili yardımcı fonksiyonlara dokun"
  kısıtı gereği bu Adım 6 kapsamında DÜZELTİLMEDİ - ayrı bir onay/iş kalemi bekliyor.

  Hiçbir şey commit edilmedi - `mc.py`/`test_mc.py`/`test_maintenance_checks.py`/
  `memory/decisions.md` working tree'de duruyor, açık onay bekliyor. Adım 7-8 henüz
  başlamadı.

- 2026-08-18 - `test_stage_as_memory_metadata.py::_row()` düzeltildi - Adım 6 raporunda
  bulunan, Adım 1'in bir downstream etkisiydi. Kök sebep tekrar: Adım 1 sırasında yapılan
  downstream taraması (`mark_memory_replaced`, `log_lesson`, `find_similar_lesson_groups`,
  `retire_low_confidence_lessons`) yalnızca `mc.py` içindeydi, ayrı `test_*.py` dosyalarını
  kapsamıyordu - bu yüzden bu dosyanın kendi test yardımcı fonksiyonu gözden kaçtı.

  **Düzeltme, kullanıcının önerdiği düz `status='active'` filtresinden FARKLI çıktı -
  önce literal öneri denendi, gerçek veriyle test edildi, iki testi bozduğu görüldü:**
  `test_mark_memory_replaced_sets_status_and_replaced_by` ve `test_heal_memory_marks_
  old_notes_replaced_and_hides_them_from_search` bilerek `status='replaced'` olan bir
  satır bekliyor (`mark_memory_replaced()`, `mc.py:4140-4162` - source_file başına HER
  ZAMAN tek satır, yerinde UPDATE, Adım 1'in `supersedes_id` zincirinden tamamen ayrı bir
  mekanizma) - düz `AND status='active'` filtresi bu satır için `None` döndürüp
  `TypeError` ile patlıyordu. Doğru düzeltme: `_row()` artık `ORDER BY id DESC LIMIT 1`
  kullanıyor (status filtresi YOK) - hem Adım 1'in supersede zincirini (2 satır: en
  yüksek id = en güncel/aktif) hem `mark_memory_replaced()`'i (tek satır, id filtresi
  zaten trivial) doğru ele alıyor.

  **Bu düzeltme sırasında AYRI, gerçek bir `mc.py` hatası ortaya çıktı (henüz
  düzeltilmedi, onay bekliyor):** `test_created_at_set_once_and_never_changes` hâlâ
  FAIL veriyor - ama artık DOĞRU satırı karşılaştırdığı için. `index_memory_files()`'ın
  kendi docstring'i (`mc.py:4062-4063`) "created_at is set once, on the row's first
  insert, and never overwritten afterward" diyor, ama supersede-insert dalı
  (`mc.py:4115-4128`) yeni satırın `created_at`'ine `existing["created_at"]` değil `now`
  yazıyor (`mc.py:4125`, tuple'daki ilk `now`). Bu, önceden `_row()`'un aynı hatası
  yüzünden maskeleniyordu - eski test yanlış (eski, superseded) satırı çekiyordu, o
  satırın `created_at`'i haliyle kendisiyle eşitti, bu yüzden hata hiç görünmüyordu.
  Düzeltme iki değişiklik gerektiriyor: (a) `existing` SELECT'ine (`mc.py:4086-4091`)
  `created_at` kolonunu eklemek (şu an sadece `id, version, content_hash` çekiyor), (b)
  `mc.py:4125`'teki ilk `now`'ı `existing["created_at"]` ile değiştirmek. Kapsam dışı
  bırakıldı - kullanıcının bu mesajdaki talimatı sadece test yardımcısını kapsıyordu,
  bu gerçek `mc.py` üretim mantığını değiştiren AYRI bir değişiklik, ayrı onay
  gerektiriyor.

  Düzeltme sonrası `test_stage_as_memory_metadata.py` tek başına: 7 testten 6'sı geçti,
  1'i (yukarıdaki bilinen, henüz düzeltilmemiş `created_at` hatası yüzünden) FAIL.

  **Sistemik tarama (görev 3):** `mc.py`'de `source_file` üzerinden `memory_index`
  sorgulayan TÜM yerler tek tek kontrol edildi - hepsi zaten doğru filtreli:
  `mc.py:4088` (`index_memory_files`, `AND status='active' ORDER BY id DESC LIMIT 1`),
  `mc.py:4159` (`mark_memory_replaced`, `AND status='active'`), `mc.py:5188`
  (`log_lesson`, `WHERE status='active'`), `mc.py:5255` (`find_similar_lesson_groups`,
  `AND status NOT IN ('replaced','superseded')`), `mc.py:5445`
  (`retire_low_confidence_lessons`, `AND status='active'` - bu fonksiyonun hemen
  ardından gelen filtresiz `DELETE FROM memory_index WHERE source_file=?`, `mc.py:5455`,
  KASITLI ve dokümante: "Once a file is retired, the DELETE below removes all of its
  rows, active and superseded alike" - bir hata değil), `mc.py:4251`/`4300`
  (`search_memory`/`semantic_search`, ikisi de varsayılan `status NOT IN (...)`).
  `test_*.py` genelinde de tarandı: `test_mc.py`'deki diğer tüm `WHERE source_file`
  sorguları (satır 1214, 1227, 1239, 1248, 1291, 1305, 1318, 1668, 2606, 2654, 2663,
  2673, 2756) tek tek kontrol edildi - hepsi ya tek `index_memory_files()` çağrısından
  sonra (o an için tek satır garantili) ya da zaten doğru `ORDER BY id`/`fetchall()`
  kullanıyor (`test_index_memory_files_preserves_superseded_row_and_links_new_version`,
  satır 1233-1271, Adım 1 sırasında zaten doğru yazılmış). **Sonuç: bu hata izole bir
  örnekti, sistemik bir desen DEĞİL** - tek kaçan yer `test_stage_as_memory_
  metadata.py::_row()`'du.

  Hiçbir şey commit edilmedi. Bekleyen onay: `created_at` hatasının şimdi mi yoksa ayrı
  bir iş kalemi olarak mı düzeltileceği.

- 2026-08-18 - Adım 1'e ait ayrı düzeltme: `created_at` iddiası kod DEĞİL, docstring
  hataymış - core'a bakılarak doğrulandı, düzeltildi. `index_memory_files()`'ın
  docstring'i (`mc.py:4059-4063`, önceki hali) "created_at is set once, on the row's
  first insert, and never overwritten afterward" diyordu; Adım 6 sırasında bulunan
  `test_stage_as_memory_metadata.py::_row()` hatası düzeltilince bu iddiayı test eden
  `test_created_at_set_once_and_never_changes` gerçek (yeni) satırı karşılaştırmaya
  başladı ve FAIL verdi.

  Önce core kontrol edildi (kullanıcı talimatı): `lib/memory_fact_store.py:578-703`
  (`apply_from_connection` - dosyanın kendi docstring'i "Sole DML owner ... for Mission
  Control memory_fact" diyor, `promote`/`supersede` için TEK yazma yolu). `supersede`
  işleminde yeni satırın `INSERT INTO memory_fact(...)` kolon listesi
  (`lib/memory_fact_store.py:633-637`) **`created_at`'i hiç içermiyor** - şemanın
  `DEFAULT CURRENT_TIMESTAMP`'ine (`sql/memory_fact.sql`) bırakılıyor. Yani core'da da
  her yeni satır KENDİ INSERT anının `created_at`'ini alıyor, supersede ettiği satırdan
  devralmıyor. INSERT sonrası da (aynı fonksiyon içinde, eski satırın sadece `status`'u
  `'superseded'`e çevriliyor) `created_at`'e hiçbir yerde dokunulmuyor - devralma yok.

  Sonuç: bbb-lite'ın KODU zaten doğruydu (`mc.py:4125`, yeni satıra `now` yazıyor,
  eskisinden devralmıyor) - core'un gerçek davranışıyla birebir uyumlu. Hatalı olan
  sadece docstring'in iddiasıydı - bu iddia core'dan gelmiyordu, muhtemelen Adım 1
  öncesi bir dönemden kalma (o zaman reindex tek satır üzerinde yerinde UPDATE'ti,
  UPDATE created_at'e hiç dokunmadığı için iddia o zaman doğruydu) ve Adım 1 çok-satırlı
  zinciri getirdiğinde hiç güncellenmemişti - core'a hiç bakılmadan yazılmış, yanlış bir
  iddia. **Seçenek (b) uygulandı:** kod DEĞİŞMEDİ, docstring düzeltildi (yeni satırın
  kendi `created_at`'ini aldığını, core'un `apply_from_connection`'ına referansla
  netleştirir), `test_created_at_set_once_and_never_changes` ikiye ayrıldı:
  `test_created_at_unchanged_when_content_unchanged` (içerik değişmeyince satır hiç
  dokunulmuyor - hâlâ doğru, ayrı ve açık bir testle kaldı) ve
  `test_created_at_is_fresh_per_version_not_inherited_across_supersede` (içerik
  değişince yeni satırın `created_at`'i eskisinden FARKLI olmalı - doğru semantiğe göre
  yeniden yazıldı, `assertNotEqual`).

  Test: `test_stage_as_memory_metadata.py` tek başına 7 -> 8 test (+1, yeni ayrılan
  test), hepsi yeşil.

  Hiçbir şey commit edilmedi.

- 2026-08-18 - Adım 7 (vektör şeması sadeleştirme) tamamlandı - planın "düşük risk,
  düşük öncelik" işaretli maddesi.

  **Adım 0'da bulunan mimari fark, kullanıcı onayıyla bilerek KOPYALANMADI:**
  core'da `embedding_model`/`embedding_dim` karşılığı satır-bazlı değil -
  `memory_embedding_generation` (`sql/memory_recall.sql`) diye ayrı, normalize bir
  tablo; `model`/`dim` orada bir "generation"ın (tüm bir yeniden-embed çalışmasının,
  `shadow`/`eligible`/`active`/`retired` durumlarıyla) özelliği, tek tek satırların
  değil - çoklu model/versiyon geçişini (canlı re-embedding migration'larını)
  desteklemek için. bbb-lite'ta böyle bir geçiş hiç yaşanmadı ve planlanmıyor (tek
  model: `all-MiniLM-L6-v2`, projenin başından beri) - bu yüzden planın literal önerisi
  (satır-bazlı iki kolon) ile devam edildi, aynı gerekçeyle Adım 6'da
  `projection_coverage`/`archive_placement`in kapsam dışı bırakılmasıyla aynı kategori:
  core'un ağır mekanizması bbb-lite'ta karşılığı olmayan bir senaryoyu (çoklu
  generation yönetimi) çözüyor.

  **Şema** (`mc.py:592-639` bloğunun devamı, Adım 5'in `epistemic_class`/
  `source_digest` eklerinin hemen ardından):
  - `mc.py:617-618` - `embedding_model TEXT` (düz `ALTER TABLE ADD COLUMN`).
  - `mc.py:619-639` - `embedding_dim INTEGER` eklendi + tek seferlik backfill
    (`mc.py:635-639`): `embedding IS NOT NULL` olan tüm mevcut satırlara
    `embedding_model=_EMBEDDING_MODEL_NAME`, `embedding_dim=length(embedding)/4`
    yazan bir `UPDATE`. Backfill'in belirsiz olmaması özellikle kontrol edildi: gerçek
    `mc.db`'de (test fixture değil) 11 satırın 11'i de zaten `status='active'` VE
    `embedding IS NOT NULL` - bbb-lite'ın tüm tarihinde tek model kullanıldığı için
    (model değişikliği hiç olmadı) her satıra aynı sabit `embedding_model` değeri
    yazmak güvenli, "hangi model üretti" belirsizliği yok.

  **Dinamik `embedding_dim`, sabit 384 DEĞİL** (kullanıcı talimatı): `mc.py:4118-4123`,
  `index_memory_files()` içinde `embedding = _embed_or_none(...)` çağrısından hemen
  sonra `embedding_dim = len(embedding) // 4` (float32 = 4 byte/dim) hesaplanıyor -
  üretilen vektörün KENDİ byte uzunluğundan, `SentenceTransformer.
  get_sentence_embedding_dimension()` metodunu ayrıca çağırmadan (aynı bilgiyi zaten
  elimizdeki bytes'tan çıkarmak, modele ikinci bir çağrı yapmaktan daha ucuz ve tek
  doğruluk kaynağı - vektörün gerçek boyutuyla asla çelişemez). Backfill'deki
  `length(embedding) / 4` SQL ifadesi aynı mantığı DB tarafında tekrarlıyor - iki
  farklı türetme yolu, aynı gerçek kaynaktan (byte uzunluğu), asla `_EMBEDDING_MODEL_
  NAME` sabitinden bağımsız kalmıyor.

  **Her iki INSERT dalına da yazıldı** (`index_memory_files()`'ın iki ayrı yazma
  noktası, Adım 1'in supersede zincirinden beri iki yerde tekrarlanıyor): ilk-insert
  dalı (`mc.py:4132-4146`, kolon listesine `embedding_model, embedding_dim` eklendi)
  ve Adım 1'in supersede-insert dalı (`mc.py:4156-4171`, aynı ekleme) - ikisi de ayrı
  ayrı test edildi (aşağıda).

  Test (4 yeni, `MemoryIndexTestCase`, `test_mc.py`):
  - `test_index_memory_files_stores_embedding_model_and_dim` - yeni dosya indexlenince
    `embedding_model='all-MiniLM-L6-v2'`, `embedding_dim=384` (gerçek model).
  - `test_supersede_insert_also_stores_embedding_model_and_dim` - içerik değişip
    Adım 1'in supersede-insert dalı tetiklenince YENİ (aktif) satırın da doğru
    doldurduğu - ilk-insert dalının testinden ayrı, ikinci dalı özel olarak kapsıyor.
  - `test_embedding_dim_is_read_dynamically_not_hardcoded` - `mc._embed_or_none`'ı
    384 değil 17-boyutlu sahte bir vektör döndürecek şekilde mock'layıp
    `embedding_dim`'in gerçekten 17 yazdığını doğruluyor - hardcoded olsaydı hâlâ 384
    yazardı.
  - `test_backfill_fills_embedding_model_and_dim_for_pre_migration_rows` - migration
    öncesi durumu simüle etmek için `ALTER TABLE memory_index DROP COLUMN` (SQLite
    3.35+, `sqlite3.sqlite_version` 3.50 ile doğrulanmıştı) kullanılarak iki kolon
    geri düşürülüp gömme (embedding) olan bir satır ham SQL ile eklendi, sonra
    `mc.init_db()` tekrar çağrılıp backfill'in gerçekten çalıştığı doğrulandı - bu
    codebase'de "hesaplanan backfill" (sabit bir DEFAULT değil, başka bir kolondan
    türetilen) için ilk emsal, önceki migration'ların (`authority` vb.) hepsi düz
    `DEFAULT` değeriyle çözülüyordu.

  `MemoryIndexTestCase` tek başına: 26 -> 30 test (+4), hepsi yeşil.

  Hiçbir şey commit edilmedi.

- 2026-08-18 - **Adım 8 (doğrulama ve kayıt) - 8 adımlık `BBB-OS-CORE-MEMORY-DB-
  PARITY-IMPLEMENTATION-PLAN.md`'nin tamamı için üst-seviye özet.** Bu girdi, yukarıdaki
  tek tek Adım 1-7 girdilerinin yerine geçmiyor - onların üzerine, planın BÜTÜNÜNÜ
  2026-08-12'deki asıl kapsam kararına bağlayan tek bir kapsayıcı not.

  2026-08-12 kararı şöyleydi: bbb-os-core salt-okunur referans, CLAUDE.md'nin sert
  kısıtları (SQLite-only/tek dosya/sunucu yok/stdlib-only) SABİT, hedef "%90+
  mantıksal/özellik benzerliği - mimari değil" (yukarıda, bu dosyada 2026-08-12
  girdisi). O tarihte owner/authority modeli ve epistemic_class/derivation katmanı
  "tek kullanıcıda anlamsız"/"production-only, taşınmasın" diye AÇIKÇA kapsam dışı
  bırakılmıştı (`BBB-OS-CORE-MEMORY-DB-PARITY-REPORT.md`'nin önceki hali).

  8 adımlık liste (2026-08-17 tarihli) bu kararı BİLEREK genişletti: owner/authority
  (Adım 4) ve epistemic_class/derivation (Adım 5) artık kapsamda, çünkü hedef bu kez
  açıkça "%100'e yaklaşmak" idi (planın kendi giriş bölümü, madde 1). Ama bu genişleme
  SEÇİCİYDİ, sınırsız değildi - mimari kısıtlar (HTTP gateway, SSH tünelleri, systemd
  daemon, Telegram/Whisper/Kokoro, `phases` tablosu) 2026-08-12'den beri hiç
  değişmedi ve planın kendi "Kapsam dışı kalanlar" bölümünde tekrar teyit edildi.
  Uygulama sırasında bu SEÇİCİ genişletme prensibi iki kez daha somut olarak devreye
  girdi: Adım 6'da `projection_coverage`/`archive_placement` (core'un gerçek 6
  handler'ından 2'si) dışarıda bırakıldı - ikisi de bbb-lite'ta hiç olmayan ve
  planlanmayan bir outbox/projection tüketicisi ya da arşiv-generation alt sistemi
  gerektiriyordu, yani "memory/db" değil "mimari" kategorisindeydi (aynı 2026-08-12
  ayrımı). Adım 7'de core'un normalize `memory_embedding_generation` tablosu (çoklu
  model/versiyon geçişini desteklemek için var) bilerek KOPYALANMADI, yerine
  bbb-lite'ın hiç yaşamadığı ve planlamadığı bir senaryo için gereksiz karmaşıklık
  olacağı gerekçesiyle satır-bazlı iki kolon (`embedding_model`/`embedding_dim`)
  tercih edildi.

  Sonuç: 8 adımın 5'i (1,2,3,6,7) 2026-08-12'nin orijinal "mantıksal/özellik
  benzerliği, mimari değil" sınırının İÇİNDE kaldı; 2'si (4,5) o sınırı bilerek ve
  açıkça genişletti (owner/authority, epistemic_class/derivation); ama HİÇBİRİ mimari
  kısıtları (sunucu/daemon/SSH/Telegram/HTTP gateway) ihlal etmedi - genişleme sadece
  "hangi memory/db KAVRAMLARI taşınsın" sorusuna dairdi, "nasıl bir SİSTEM olsun"
  sorusuna değil. `mc.py` hâlâ tek dosya, SQLite hâlâ tek yerel dosya, hâlâ sunucu yok.

  Doğrulama: `python -m unittest discover` ile TÜM `test_*.py` (28 dosya, sadece
  `test_mc.py` değil - bu disiplin Adım 6'nın sonunda standart hale getirildi) 1104
  test, 0 hata. İki gerçek bug bu süreçte bulunup düzeltildi (ayrı Adım 1 girdileri,
  yukarıda): `test_stage_as_memory_metadata.py::_row()`'un Adım 1'in çok-satırlı
  zincirinden sonra yanlış satırı seçmesi, ve `index_memory_files()`'ın `created_at`
  hakkında core'a hiç bakılmadan yazılmış yanlış bir docstring iddiası. Hiçbir şey
  commit edilmedi - tüm değişiklikler (`mc.py`, `test_mc.py`, `test_maintenance_
  checks.py`, `test_stage_as_memory_metadata.py`, `PROJECT-HISTORY.md`, bu dosya)
  working tree'de duruyor, commit kararı ayrıca (kullanıcı tarafından) verilecek.

- **2026-08-18 — `confirm_memory()`'a var olmayan `memory_id` için varlık kontrolü
  eklendi.** Sorun: fonksiyon `memory_id`'nin `memory_index` tablosunda gerçekten var
  olup olmadığını hiç kontrol etmiyordu — `confirm_memory(999999, "confirmed", "chat")`
  sessizce çalışıp DEĞİŞTİRİLEMEZ `memory_confirmations` defterine var olmayan bir kayıt
  için sahte bir onay/red yazıyordu. Şemadaki `memory_confirmations.memory_id REFERENCES
  memory_index(id)` ifadesi bunu engellemiyor, çünkü SQLite'ta foreign key zorlaması
  varsayılan olarak kapalı ve proje hiçbir yerde `PRAGMA foreign_keys = ON` çalıştırmıyor
  — yani şemadaki `REFERENCES` sadece dokümantasyon, gerçek bir kısıt değil. Çözüm:
  `start_task()`/`request_review()`'da zaten kullanılan "aynı bağlantı içinde SELECT +
  fetchone() is None → `ValueError(f"unknown X: {value}")`" deseniyle tutarlı olacak
  şekilde, INSERT'ten önce `memory_index`'te satırın var olup olmadığına bakan bir kontrol
  eklendi (`mc.py:4238-4241`, `raise ValueError(f"unknown memory_id: {memory_id}")`).
  Geçersiz `memory_id` artık hiçbir INSERT/UPDATE çalıştırmadan hata fırlatıyor; geçerli
  `memory_id`'lerle davranış değişmedi. 3 yeni test eklendi (`test_mc.py`,
  `MemoryConfirmations` sınıfı): bilinmeyen id için `ValueError` fırlatıldığını doğrulayan,
  bu durumda `memory_confirmations`'a hiç satır yazılmadığını doğrulayan, ve bilinen bir
  id ile eski davranışın aynı kaldığını doğrulayan regresyon testi. `python -m unittest
  discover`: 1107 test, 0 hata. Hiçbir şey commit edilmedi.

- **2026-08-21 — Faz 3 Bölüm A: entity/entity_alias, core'un tam entity-resolution
  sisteminin KÜÇÜLTÜLMÜŞ bir uyarlaması, birebir kopyası değil.** bbb-os-core'un
  entity/alias sistemi `memory_assertion` (subject/attribute/value + zamansal
  geçerlilik) katmanına bağımlı ve "protected entity" için çift-onay, periyodik
  yeniden-onay makbuzu gibi production-ölçekli mekanizmalar taşıyor. bbb-lite'ta
  `memory_assertion` hiç yok (ve bu turda da açılmıyor — kapsam dışı), o yüzden şu
  uyarlamalar bilerek yapıldı:
  - `entity`/`entity_alias`/`fact_entity` doğrudan `memory_index`'e bağlanıyor,
    `memory_assertion`'a değil — `fact_entity.memory_id REFERENCES memory_index(id)`.
  - `entity_alias.status` state machine'i (held→accepted/revoked, accepted→
    revoked/superseded, revoked/superseded terminal) core'daki gibi ama "protected
    entity" ayrımı yok — TÜM non-'erdi' authority alias'lar aynı kurala tabi
    (`mc.py`'deki `entity_alias_status_lifecycle` trigger'ı, ~1330 civarı).
  - Periyodik yeniden-onay/iptal akışı (core'un ayrı `memory_alias_confirmation_
    receipt`'i) YOK — bu turda sadece İLK kabul/red kararı ele alınıyor (görev
    tanımında açıkça kapsam dışı bırakıldı).
  - `entity_confirmations`, `memory_confirmations`'ın AYNI şeklini taşıyan ama AYRI
    bir tablo (`mc.py:~1290`) — sebep: `memory_confirmations.memory_id` `NOT NULL
    REFERENCES memory_index(id)`, bir entity_alias onayının işaret edeceği bir
    `memory_index` satırı yok; bu NOT NULL'ı gevşetmek `memory_confirmations`'ın var
    olan tüm garantisini zayıflatırdı sırf ikinci, alakasız bir makbuz türünü
    sığdırmak için.
  - `entity_alias_status_lifecycle` trigger'ı, `authority != 'erdi'` olan bir alias
    accepted/revoked/superseded'e geçerken `entity_confirmations`'ta o `alias_id`
    için uygun `decision`'lı bir satır arıyor (confirmed→accepted,
    corrected/rejected→revoked/superseded) — core'un "owner confirmation required
    for non-owner authority" ilkesinin bire bir uyarlaması, sadece "protected"
    ayrımı olmadan.
  Yazıcı fonksiyonlar: `create_entity()`, `add_entity_alias()`, `confirm_entity_
  alias()`, `set_entity_alias_status()` (`mc.py:~4615-4750`) — `set_entity_alias_
  status()` geçiş geçerliliğini Python'da önceden kontrol ETMİYOR, `claim_memory_
  hold()` ile aynı ilkeyle trigger'ın kendisi gerçek kaynak (geçersiz geçiş veya
  eksik onay `sqlite3.IntegrityError` fırlatır). `fact_entity` bu turda SADECE şema
  — Adım 5'teki `memory_derivation` ile aynı emsal: bbb-lite'ta henüz bir
  memory_index satırını bir entity'ye bağlayan hiçbir akış yok, o yüzden hiçbir
  yazıcı fonksiyon eklenmedi (kullanılmayan kod yazmamak için). 11 yeni test
  (`EntityAliasLifecycleTestCase`, `test_mc.py`, + kalıtımla gelen `McTestCase`
  taban testleri) — hepsi geçti. Hiçbir şey commit edilmedi.

- **2026-08-21 — Faz 3 Bölüm B: memory_derivation'ın ABSTRACTION inşa kuralı,
  core'un katı 'STATED' şartı yerine 'STATED VEYA IMPORTED' olarak uyarlandı.**
  Core, bir ABSTRACTION derivation'ın her parent'ının epistemic_class'ının salt
  'STATED' olmasını şart koşuyor (yani "erdi'nin doğrudan söylediği" kaynaklardan
  üretilmiş olması). bbb-lite'ta bugün üretilen notların neredeyse tamamı
  `index_memory_files()` ile diskten okunan `memory/*.md` dosyaları — bunlar
  schema DEFAULT'una göre 'IMPORTED', 'STATED' değil (Adım 5, `mc.py:~596-600`
  dokümantasyonu: "Default 'IMPORTED' matches reality for every row that
  predates this column: it came from a markdown file on disk, not from erdi
  saying something directly in chat"). `approve_merge()`'ün Faz 2'de aktive
  edilen tek gerçek ABSTRACTION üreticisi de tam olarak bu IMPORTED notları
  birleştiriyor. Core'un katı kuralını birebir kopyalasaydık, bugünkü tek üretici
  akışın HİÇBİR ZAMAN geçemeyeceği bir trigger yazmış olurduk — "kağıt üzerinde
  var, pratikte hep reddeder" tipik kod-ölü senaryosu. Bunun yerine `STATED OR
  IMPORTED` kabul edildi (`mc.py`'deki `memory_derivation_abstraction_guard`
  trigger'ı) — hâlâ anlamlı bir kısıt (SUMMARY/DERIVED/SYNTHETIC parent'lar hâlâ
  reddediliyor, bkz. `test_abstraction_guard_rejects_non_stated_or_imported_
  parent`), ama bugünkü gerçek veriyle çalışabilen bir kısıt.

  İkinci uyarlama: core'un "parent'lar aynı scope'u paylaşmalı" kontrolü,
  bbb-lite'ta ayrı bir scope kavramı olmadığı için "parent'lar aynı `project`
  alanını paylaşmalı"ya indirgendi (memory_index.project zaten var olan tek
  yakın karşılık).

  Guard, child'ın `epistemic_class = 'DERIVED'` olmasını da şart koşuyor — ama
  `index_memory_files()` hiçbir satıra bunu yazmıyor (DEFAULT 'IMPORTED'), o
  yüzden `approve_merge()`'e, `memory_derivation` INSERT'inden hemen önce, child
  satırını `epistemic_class = 'DERIVED'`ye çeken bir UPDATE eklendi — semantik
  olarak da doğru: birleştirilmiş/healed bir not, ham bir import değil, gerçekten
  bir abstraction'dır. Aynı yerde yeni `memory_inference_producer_receipt`
  tablosuna `action='produced'` bir satır da düşülüyor.

  BEHAVIORAL_INFERENCE için hiçbir guard eklenmedi (görev tanımında açıkça
  kapsam dışı — bbb-lite'ta gerçek bir üreticisi yok).

  4 yeni test (+ 1 var olan testin güncellenmesi, yeni sayılmıyor):
  `MemoryEpistemicClassDerivationTestCase`'e 3 yeni (`test_abstraction_
  guard_rejects_non_derived_child`, `test_abstraction_guard_rejects_project_
  mismatch`, `test_abstraction_guard_rejects_non_stated_or_imported_parent`) +
  var olan `test_memory_derivation_with_two_parents_written`'ın yeni guard'a
  uyacak şekilde güncellenmesi (child artık açıkça `epistemic_class='DERIVED'`
  ile ekleniyor); `MemoryDecisionHoldTestCase`'e 1 regresyon testi
  (`test_approve_merge_still_writes_derivation_and_producer_receipt`) — normal
  `approve_merge()` akışının hâlâ hem `memory_derivation` hem `memory_inference_
  producer_receipt` yazdığını doğruluyor. Hiçbir şey commit edilmedi.

- **2026-08-21 — Faz 4: memory_assertion, core'un tam SAV katmanı yerine
  BİLEREK küçültülmüş, memory_index'e PARALEL/opsiyonel bağlanan bir versiyon
  olarak eklendi.** Core'da `memory_assertion` tüm veri modelinin altında
  yatan katman — her `memory_index` benzeri satır aslında bir SAV
  (subject-attribute-value) iddiasının üstüne kurulu. Bu turda o rijitliği
  birebir kopyalamadık: `memory_index`'in şeması hiç değişmedi (tek bir kolon
  bile eklenmedi), `memory_assertion` bunun yanında duran, `memory_id`'si
  NULL olabilen yeni ve bağımsız bir tablo. Bir not hâlâ normal şekilde
  `memory_index`'te yaşıyor; `memory_assertion` sadece o notun içinden
  çıkarılabilecek yapılandırılmış, tekil bir iddiayı isteğe bağlı olarak
  temsil ediyor. `epistemic_class`'ın `STATED` için `source_span`/
  `source_digest` şartı, `memory_index`'teki AYNI CHECK deseni (Adım 5) —
  uyarlama değil, birebir taşıma.

  `memory_assertion_relation`'ın kapalı kelime dağarcığı
  (`supports`/`contradicts`/`supersedes`/`derived_from`) `GRAPH_EDGE_TYPES`
  (`mc.py:35`) ile büyük ölçüde örtüşüyor ama aynı sabit değil — `graph_edges`
  düğüm-tipi-agnostik genel bir graf kenarı, `memory_assertion_relation` ise
  özel olarak iki `memory_assertion` satırı arasında; ayrı CHECK kalması
  bilinçli (core'da da ayrı tablo/ayrı vocab).

  Bu turda da (Faz 3'teki `fact_entity`/Adım 5'teki `memory_derivation` ile
  aynı emsal) hiçbir otomatik iddia çıkarma akışı (NLP/LLM pipeline) yok —
  sadece CRUD altyapısı: `create_assertion()`, `link_assertion_relation()`,
  `get_assertions_for_memory()`, `get_assertions_for_subject()`. Bir not
  dosyasından bir iddia türetmek isteyen gelecekteki bir akış bu
  fonksiyonları çağırabilir; bugün hiçbiri çağırmıyor.

  7 yeni test (`MemoryAssertionTestCase`, `test_mc.py`) + `test_init_db_
  creates_tables`'ın beklenen tablo setine `memory_assertion`/`memory_
  assertion_relation` eklenmesi (yeni sayılmıyor, var olan testin
  güncellenmesi). Hiçbir şey commit edilmedi.

- **2026-08-21 — Session/checkpoint recovery proje sınırı tanımıyordu, düzeltildi
  (session management parity, Adım 1).** Bug: `sessions` ve `checkpoints`
  tablolarında `project_id` yoktu, bu yüzden `mc.latest_checkpoint(session_id)`
  kendi checkpoint'i olmayan bir session için DB'deki mutlak en son checkpoint'e
  (herhangi bir projeden) düşüyordu — örn. Nextven'de açılan yeni bir session,
  kendi checkpoint'i yoksa bbb-lite'ın son checkpoint'ini "recovery" olarak
  gösterebiliyordu.

  Düzeltme: `sessions.project_id` ve `checkpoints.project_id` eklendi
  (client_session_uuid migration-guard pattern'i birebir taklit edilerek).
  `hooks/session_start.py`'de `resolve_project(cwd)` artık `ensure_session(...)`
  çağrısından ÖNCE yapılıyor, sonucu `ensure_session(..., project_id=...)`'a
  veriliyor. `write_checkpoint()` her satıra session'ın project_id'sini otomatik
  yazıyor.

  `latest_checkpoint()`'in fallback davranışı BİLEREK ikiye bölündü:
  - session'ın project_id'si BİLİNİYORSA: fallback `WHERE project_id = ?` ile o
    projeye sınırlı (bug'ın doğrudan düzeltmesi).
  - project_id BİLİNMİYORSA (session hiç `ensure_session` ile kaydedilmemiş VEYA
    cwd gerçekten unmapped): eski global fallback (tüm `checkpoints` tablosunda
    en son satır) AYNEN korundu — bu, yeni/sınıflandırılmamış bir session'a hâlâ
    faydalı bir "son görev" bağlamı verme amaçlı bilinçli bir tasarımdı (bkz.
    `memory/lesson-latest-checkpoint-fallback.md`, ve onu kanıtlayan
    `test_latest_checkpoint_falls_back_when_session_has_none`, `test_mc.py`).
    Bu ayrımı yeni `test_latest_checkpoint_does_not_cross_project_boundary` ve
    `test_latest_checkpoint_falls_back_within_same_project` testleri kanıtlıyor.

- **2026-08-21 — Session management parity, Adım 2: `sessions.surface`
  etiketi eklendi (henüz kullanılmıyor).** Tespit: bbb-lite'a üç farklı
  giriş noktasından session yazılıyor ama hangi yüzeyden geldikleri hiçbir
  yerde kayıtlı değildi — `hooks/session_start.py` (gerçek Claude Code
  UUID'leri), `bin/dispatch.py` (sabit `dispatch-cli` kimliği), ve
  `bin/assistant_server.py` (sabit `web-assistant` kimliği, Canvas kendi
  id'sini göndermezse). Bu üçü aynı `sessions`/`checkpoints` şemasını
  paylaşıyor ama `dispatch-cli`/`web-assistant` KALICI sözde-session'lar
  oldukları için `started_at`e göre değerlendirilmeleri yanlış (her zaman
  "eski" görünürler, halbuki her çağrıda tekrar aktif oluyorlar).

  Bu adımda: `sessions`e `surface TEXT CHECK(surface IN
  ('claude-code-hook','dispatch-cli','web-assistant'))` eklendi
  (`project_id`'nin migration-guard pattern'i birebir taklit edilerek, NULL'a
  izin veren bir ALTER TABLE — SQLite'ta `NULL IN (...)` CHECK'i ihlal
  saymıyor). `ensure_session()`e `surface` parametresi eklendi, var olan
  satırda `project_id` ile simetrik davranıyor: `surface=None` verilirse
  mevcut değer korunuyor, dolu verilirse UPDATE ediliyor. Üç giriş noktası
  kendini işaretliyor: `claude-code-hook`, `dispatch-cli`, `web-assistant`.

  Bilinçli olarak SADECE etiketleme — fallback/recovery mantığına
  dokunulmadı. Adım 3'te bu etiket, `dispatch-cli`/`web-assistant` gibi
  kalıcı sözde-session'ların `started_at` yerine son aktiviteye göre
  değerlendirilmesi için kullanılacak (bu adımın motivasyonu, henüz
  uygulaması değil).

- **2026-08-21 — Session management parity, Adım 3: `sweep_stale_sessions()`
  artık kalıcı sözde-session'lar için checkpoint tazeliğine bakıyor,
  `started_at`e değil.** Bug (gerçek, doğrulanabilir): `bin/daily_check.py`
  günlük `mc.sweep_stale_sessions(hours=24)` çağırıyor, ama bu fonksiyon
  'active' session'ları SADECE `started_at` yaşına göre 'stale'
  işaretliyordu. Adım 2'de etiketlenen `dispatch-cli`/`web-assistant` ise
  KALICI, tek satırlık session'lar — `ensure_session()` satır zaten varsa
  `started_at`'e dokunmuyor. Sonuç: bu iki kimlik ilk oluşturulduktan 24
  saat sonra SONSUZA DEK 'stale' kalıyordu, her gün aktif kullanılsalar
  bile — hiçbir şey onları geri 'active' yapmıyordu. Bu da
  `check_floor()`'daki "N session stale" uyarısını kalıcı yanlış pozitif
  yapıyordu.

  Düzeltme: `sweep_stale_sessions()` artık `surface`e göre ikiye ayrılıyor.
  `surface IN ('dispatch-cli','web-assistant')` için sinyal SON AKTİVİTE —
  o session_id'ye ait en son checkpoint'in `created_at`'i (yoksa
  `started_at`'e düşülüyor, tamamen terk edilmiş bir pseudo-session hâlâ
  yakalanabilsin diye). `surface = 'claude-code-hook'` veya NULL (Adım 2
  öncesi eski satırlar) için davranış AYNEN eskisi gibi: `started_at`
  yaşına bakılıyor — her hook session'ı zaten kendi UUID'si, doğal olarak
  süresi sınırlı.

  Bu, `check_safepoint_stale()`'in zaten kullandığı sinyalle (per-session
  checkpoint tazeliği, `latest_checkpoint()`'in cross-session fallback'ine
  düşmeden) birleştirme — `check_safepoint_stale()`'e dokunulmadı, sadece
  asıl mutasyonu yapan `sweep_stale_sessions()` aynı doğru sinyale
  taşındı.

- **2026-08-21 — Session management parity, Adım 4: `sessions`e `ended_at`/
  `summary` audit kolonları eklendi — SADECE gerçekten terminal olan
  geçişlere (`completed`, `stale`) uygulandı, `degraded`'a BİLEREK
  uygulanmadı.** `mark_session_completed()` artık `status='completed'`
  yanında `ended_at`'i (şimdi, ISO UTC) yazıyor; opsiyonel `summary`
  parametresi verilmezse `summary` NULL kalıyor (uydurma bir varsayılan
  metin yazılmıyor). `sweep_stale_sessions()`'ın 'active'→'stale' çeviren
  UPDATE'i de aynı şekilde `ended_at` ve otomatik üretilmiş
  `summary='Automatically marked stale: no fresh checkpoint within N
  hours'` yazıyor — mevcut `WHERE status='active'` koşulu zaten sadece o
  anda gerçekten geçiş yapan satırları etkilediği için, önceden zaten
  'stale' olan satırlar her sweep çalışmasında tekrar damgalanmıyor.

  `mark_session_degraded()`'a KASITLI olarak dokunulmadı. Sebep zaten
  kendi docstring'inde açık: "compact recovery could not be verified
  intact" terminal bir durum değil — session'ın kendisi devam edebilir,
  sadece bütünlüğü şüpheli diye işaretleniyor (bkz. 2026-07-27 kararı,
  fail-soft'un bilinçli istisnası). Ona `ended_at` koymak "bu session
  bitti" gibi yanlış bir sinyal verirdi. Bu, `test_mc.py`'de açık bir
  regresyon testiyle korunuyor (`mark_session_degraded` sonrası
  `ended_at`'in hâlâ NULL olduğunu doğrulayan test) — ileride biri bunu
  "eksik kalmış" sanıp ekleyebilir, test onu engellesin diye.

- **2026-08-21 — Session management parity, Adım 5: `/clear` sonrası
  kurulan session bağlantısı artık açık bir `parent_session_id` kolonunda,
  sadece TEK YÖNLÜ.** Daha önce bu bağlantı `find_session_by_client_uuid()`
  ile her seferinde dolaylı hesaplanıyordu ama hiçbir yere yazılmıyordu —
  sorgulanamıyordu ("bu session'ın çocuğu var mı" sorusuna cevap yoktu).

  Bilinçli tercih: `successor_session_id` diye AYRI, ikinci bir kolon
  EKLENMEDİ. İki yönlü bir şema, yeni bir session açıldığında geçmişte
  kalmış (belki zaten `completed`/`stale` olmuş) bir satıra geri dönüp
  `successor_session_id` yazmayı gerektirirdi — bu da iki satırın senkron
  kalmasını garanti etmek için ekstra bir sorumluluk yaratırdı (write iki
  yere gider, biri başarısız olursa tutarsızlık). Bunun yerine sadece
  `parent_session_id` var (tek yazma, session doğarken); ters yön
  (`get_successor_session_id()`) `WHERE parent_session_id = ?` ile
  sorgulanıyor — `find_session_by_client_uuid()`'nin zaten kullandığı
  "en yeniyi al" sorgu stiliyle birebir tutarlı. Tek doğruluk kaynağı
  `parent_session_id`; successor bilgisi ondan türetiliyor, ayrıca
  saklanmıyor.

  `parent_session_id` KASITLI olarak immutable: `ensure_session()` sadece
  INSERT dalında yazıyor, var olan bir session'ın satırı UPDATE edilirken
  (client_session_uuid/project_id/surface gibi) asla dokunulmuyor — bu bir
  soy kütüğü (lineage) gerçeği, session hangi önceki session'dan
  türediyse o hep öyle kalır, sonradan değişmez. `hooks/session_start.py`
  zaten `ensure_session()`'dan ÖNCE hesapladığı `linked_session_id`'yi
  (Stage AH, `find_session_by_client_uuid` ile) artık aynı çağrıya
  `parent_session_id=` olarak da geçiyor — ekstra bir hesaplama yok, var
  olan değer bir kolona yazılıyor.

- **2026-08-21 — Session management parity, Adım 6: `bin/dispatch.py`'nin
  sabit `DISPATCH_SESSION_ID` kimliği artık invocation-özel.** Bug:
  `dispatch()` her çalıştırıldığında `ensure_session`/`add_task`/
  `complete_task` hep aynı `"dispatch-cli"` kimliğini kullanıyordu; aynı
  fonksiyonda zaten `create_dispatch_run` ile gerçekten invocation'a özel
  bir `run_id` (uuid4) üretiliyordu ama session kimliğine hiç yansımıyordu.
  Sonuç: iki `dispatch()` çalıştırması art arda/yakın zamanda olursa
  `tasks_touched_by_session("dispatch-cli")` ikisinin task'larını
  birbirine karıştırırdı.

  Düzeltme: `create_dispatch_run` çağrısı `ensure_session`'dan ÖNCEYE
  alındı (`run_id` üretilsin diye), sonra `session_id =
  f"{DISPATCH_SESSION_ID}-{run_id}"` türetiliyor ve `dispatch()` içindeki
  `ensure_session`/`add_task`/`complete_task` çağrıları bu türetilmiş
  kimliğe geçti. `create_dispatch_run`'ın kendi çağrısı hâlâ sabit
  `DISPATCH_SESSION_ID`'yi kullanıyor (bilerek — o an `run_id` henüz yok,
  tavuk-yumurta sorunu) — yani `dispatch_runs.session_id` sütunu sabit
  kalıyor, ama bbb-lite'ın asıl session/task izlemesi (`sessions`,
  `tasks.session_id`) invocation-özel kimliğe geçti.

  `run_id` `None` dönerse (fail-soft: `create_dispatch_run` DB hatasıyla
  başarısız olursa) `session_id` eski sabit `DISPATCH_SESSION_ID`'ye geri
  düşüyor — `f"...-None"` gibi anlamsız bir kimlik üretmek yerine, bu
  projenin fail-soft felsefesiyle tutarlı bir varsayılan. `DISPATCH_SESSION_ID`
  sabitinin kendisi silinmedi — hem prefix hem bu fallback için hâlâ
  gerekli.
