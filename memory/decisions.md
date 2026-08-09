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
