# bbb-os-core ile Memory/DB Parity Raporu

Tarih: 2026-08-17
Kapsam: yalnızca memory/db katmanı (mimari değil — CLAUDE.md'nin sert kısıtları
sabit: SQLite-only/tek dosya, sunucu yok, stdlib-only + numpy/sentence-transformers
istisnası). `bbb-os-core` (github.com/TrustSourcing/bbb-os-core, `main`) ve
`haroon-bbb-sync` salt-okunur referanstır — bu rapor hiçbir kod portu içermez,
sadece tespit ve öneridir.

Bu rapor iki girdiyi birleştirir: (1) 2026-08-12'de yapılmış, daha önce
`memory/decisions.md`'ye kaydedilmiş ilk karşılaştırma ve onun sonucunda kapatılan
7 madde (commit `0f6a112b`), (2) bu oturumda yapılan, bbb-os-core'un GÜNCEL
memory/db koduna (sql/*.sql, lib/memory_*.py, lib/mc_*.py) bakan yeni bir tarama.

---

## 1. Özet

- bbb-lite'ın `mc.py`'si artık 5314 satır, ~29 tablo — ilk kurulumdaki 3 tablolu
  (sessions/checkpoints/tasks) haliyle karşılaştırılamayacak kadar büyümüş durumda.
  Reuse receipts, task acceptance, maintenance battery, events outbox, GTD inbox,
  entity/decision graph, answer-quality rubric, compact/clear-sim snapshot'ları
  hepsi eklendi ve test edildi (~915 test, hepsi yeşil, son doğrulamada).
- 2026-08-12'deki ilk taramada zaten şu sonuca varılmıştı: bbb-lite, evidence
  kayıtları, health events, decision packets, entity/decision graph gibi
  alanlarda bbb-os-core'u BEKLEMİYOR, kendi adlarıyla önden uygulamış durumda.
  Gerçek boşluk sadece 7 maddeye indirgenmişti ve hepsi kapatıldı.
- O oturumda hedef açıkça **"%100 birebir" değil, "%90+ mantıksal/özellik
  benzerliği"** olarak belirlenmişti (bkz. bölüm 5 — bu ayrım önemli, aşağıda
  gerekçesiyle tekrar ediliyor).
- Bu oturumdaki yeni tarama, bbb-os-core'un memory/db katmanının bbb-lite'tan
  çok daha ağır bir "immutable receipt + trigger + multi-authority state
  machine" disiplini kullandığını gösterdi. Bunun bir kısmı (version history,
  retry sayacı, conflict kararının gerekçeli loglanması) küçültülerek
  taşınabilir; bir kısmı (owner/authority modeli, epistemic_class/derivation
  katmanı) tek-kullanıcılı bir öğrenme projesi için kavramsal olarak anlamsız
  ve production-only.

---

## 2. bbb-lite'ın mevcut memory/db envanteri (mc.py)

Tablolar (işlevsel gruplarla):

**Çekirdek**: `sessions`, `roles`, `checkpoints`, `tasks`, `projects`, `sprints`,
`documents`

**Arama/indeks**: `memory_index` (source_file UNIQUE, content, keywords,
embedding, confidence, doc_type, project, evidence_level, currentness,
content_hash, status, replaced_by, version), `doc_index`, `symbol_index`

**Reuse & kabul**: `reuse_receipts`, `task_activity`, `task_completions`,
`task_receipts` (accept-step, bbb-os-core paritesi maddesi 2)

**Graph**: `graph_nodes`, `graph_edges` (curated/requires_dual_approval/
currentness/confidence kolonlarıyla), `context_nodes`/`context_edges`
(deprecated, sadece eski satırlar için tutuluyor), `term_aliases`

**Sağlık/bakım**: `health_events`, `maintenance_runs`, `capability_boundaries`

**Karar/kayıt**: `decisions`, `decision_packets`, `dispatch_runs`,
`agent_outputs`, `evidence`, `events_log` (outbox_status kolonuyla),
`lifecycle_runs`, `turns` (session_id/speaker/role/message/engine/created_at —
önceki envanterde atlanmıştı, doğrulama oturumunda bulunup buraya eklendi;
bir şema değişikliği gerekmiyor, sadece dokümantasyon eksikliğiydi)

**Kalite/telemetri**: `answer_quality_scores`, `answer_receipts`,
`compact_snapshots`

**Uzlaşma/temizlik**: `pending_merges` (heal_memory() → approve_merge() akışı)

**Anahtar fonksiyonlar** (memory/db'ye özgü, tam liste değil): `search_memory`,
`index_memory_files`, `heal_memory`/`approve_merge`, `mark_memory_replaced`,
`retire_low_confidence_lessons`, `detect_decision_conflicts`,
`log_reuse_decision`, `accept_task_completion`, `run_maintenance_checks`,
`list_pending_events`/`mark_consumed`, `create_reminder`/`list_upcoming_reminders`/
`list_overdue_reminders`, `list_inbox_tasks`/`review` (GTD).

**Not — versiyon geçmişi bugün fiilen ne yapıyor**: `memory_index.version` her
içerik değişikliğinde artıyor ama bu SATIR İÇİ bir UPDATE — eski `content`
metni hiçbir yerde saklanmıyor, sadece "kaçıncı değişiklik" sayısı tutuluyor.
Ayrı bir yol olan `mark_memory_replaced()`/`approve_merge()` (heal_memory akışı)
ise gerçekten eski satırı silmeden `status='replaced'` + `replaced_by` işaretliyor
ve eski `.md` dosyasını `_archived-healed/`'e taşıyor — ama bu sadece "merge"
senaryosunda çalışıyor, genel bir "her düzenlemede eski hali sakla" mekanizması
değil.

---

## 3. bbb-os-core'un memory/db envanteri (güncel tarama, main branch)

Genel çerçeve: bbb-os-core da SQLite kullanıyor (`sql/schema.sql` + FTS5 sanal
tablolar) — motor farkı yok, üstüne kurulan disiplin farklı. Öne çıkan
dosyalar:

- `sql/memory_fact.sql`, `lib/memory_fact_store.py`, `lib/memory_fact_service.py`
  — çekirdek "fact" tablosu, append-only (UPDATE yok, her değişiklik yeni satır
  + `supersedes_id` zinciri).
- `lib/memory_recall.py`, `lib/memory_retrieval.py`, `lib/memory_vector_store.py`,
  `sql/memory_vector.sql` — recall/vektör katmanı (tek satır/key, model+dim
  açık, JSON-encode edilmiş vektör — bbb-lite'ın numpy blob'undan daha sade bir
  şema).
- `lib/memory_semantic_dedupe.py`, `lib/memory_promotion_decision_schema.py`,
  `sql/memory_promotion_decision.sql` — çakışma/dedup KARARI (promote/reject/
  hold/dedupe/supersede/correct + `reason_code` + `idempotency_key`) artı
  `memory_hold` state machine (open→claimed→resolved/expired, `retry_after`,
  `claim_epoch` ile eşzamanlılık kilidi).
- `lib/memory_owner_confirmation_schema.py`, `sql/memory_owner_confirmation.sql`
  — `owner_key` alanı + onay/düzeltme/red kararı + tek-kişi-otorite (yalnızca
  "haroon" onaylayabiliyor) trigger'ları.
- `lib/memory_curator.py`, `lib/memory_truth_store.py`, `lib/memory_extract.py`
  — bbb-lite'ta hiç karşılığı olmayan, katmanlı bir "epistemic_class" modeli
  (STATED/OBSERVED/IMPORTED/SUMMARY/DERIVED/SYNTHETIC) ve `memory_derivation`
  (policy-gated, min_observations eşikli çıkarım kayıtları).
- `lib/mc_client.py`, `lib/mc_schema_admission.py` — bbb-lite'ın `mc.py`
  guard'ının karşılığı, ama gateway/HTTP-bridge modeliyle.
- `notify_outbox` (schema.sql içinde) — `attempts`/`max_attempts` (varsayılan 3)
  ile gerçek retry sayacı; bbb-lite'ın `events_log.outbox_status`'ünde bu yok.
- `memory_archive_generation`/`memory_hygiene_receipt` — vector_reconcile,
  expiry_sweep, dedup_orphans, conflict_proposals gibi bulguları receipt
  zinciriyle kaydeden bir "memory hijyen taraması" — bbb-lite'ın
  `maintenance_runs`'una kavramsal olarak en yakın karşılık.

---

## 4. Fark tablosu

| # | Konu | bbb-os-core'da | bbb-lite'ta | Taşınabilirlik |
|---|------|-----------------|--------------|-----------------|
| 1 | Version history (gerçek eski içerik) | `memory_fact` hiç UPDATE edilmiyor; her değişiklik yeni satır + `supersedes_id` zinciri, eski satır `status='superseded'` olarak duruyor | `memory_index.version` sayaç olarak artıyor ama eski `content` hiçbir yerde saklanmıyor (merge-özel `mark_memory_replaced` yolu hariç) | **Evet** — append-only + `supersedes_id` deseni stdlib+SQLite ile doğrudan uygulanabilir |
| 2 | Retry sayacı | `notify_outbox.attempts`/`max_attempts` (varsayılan 3) | `events_log.outbox_status` var ama deneme sayısı/limit yok, `ask_ollama()`/embedding çağrıları başarısızsa loglanıp atlanıyor, hiç tekrar denenmiyor | **Evet** — mevcut outbox'a küçük bir kolon eklemesi, zaten var olan altyapının üstüne oturuyor |
| 3 | Conflict kararının gerekçeli loglanması | `memory_promotion_decision` (promote/reject/hold/dedupe/supersede/correct + `reason_code` + `idempotency_key`) | `detect_memory_conflicts`/`detect_decision_conflicts` sadece TESPİT ediyor; `pending_merges`/`approve_merge()` sadece merge senaryosunda insan onayı istiyor, genel bir "karar + gerekçe kodu" logu yok | **Kısmen** — mevcut `pending_merges` deseninin genişlemesi olarak, `decision`+`reason_code` alanları eklenebilir. `claim_epoch` eşzamanlılık kilidi çok-worker'lı sistemler için, tek kullanıcıda anlamsız — atlanmalı |
| 4 | Owner/authority modeli | `memory_owner_confirmation_receipts` + hardcoded tek-otorite (yalnızca "haroon") trigger'ları | Yok | **Hayır — production-only.** Tek kullanıcılı bbb-lite'ta "sahip kim" sorusu yok; taşınırsa anlamsız bir alan olarak kalır |
| 5 | Epistemic class / derivation modeli | `epistemic_class` (STATED/OBSERVED/IMPORTED/SUMMARY/DERIVED/SYNTHETIC) + `memory_derivation` (policy-gated, min_observations eşikli) | Yok — `graph_nodes`/`graph_edges` daha basit bir ilişki modeli kullanıyor | **Hayır — production-only.** Çok katmanlı, ağır; bbb-lite'ın basit graph'ının kapsamının çok ötesinde |
| 6 | Task/decision-level dedup | Ana tabloda YOK (sadece `reuse_receipts`/`notify_outbox` gibi yerlerde `idempotency_key UNIQUE` var) | Yok (bilinçli olarak bırakılmış, PROJECT-HISTORY.md bölüm 9) | **Gap değil** — bbb-os-core da bunu çözmemiş, mevcut kararı değiştirmeye gerek yok |
| 7 | Memory hijyen taraması | `memory_hygiene_receipt` (vector_reconcile/expiry_sweep/dedup_orphans/conflict_proposals/projection_coverage/archive_placement) | `run_maintenance_checks()` var (5 kontrol: PRAGMA integrity_check, orphaned graph_edges/context_edges, overdue reminders, memory currentness conflicts) ama memory-özel bulgular (embedding'i eksik satırlar, yetim `memory_index` girdileri) kapsamda değil | **Kısmen** — hafifletilmiş hali (receipt zinciri olmadan, sadece dry-run + bulgu listesi) mevcut `maintenance_runs` bataryasına yeni bir kontrol olarak eklenebilir |
| 8 | Vektör şeması sadeliği | `memory_vector.sql`: tek satır/key, model adı + boyut açık kolon, JSON-encode vektör | `memory_index.embedding` BLOB (numpy float32, model adı/boyutu şemada yok, koda gömülü) | **Küçük iyileştirme** — zorunlu değil ama okunabilirlik/gelecekteki model değişimi için model/dim'i şemaya taşımak ucuz bir adım |

---

## 5. Neden "%100 birebir" doğru hedef değil

Üç ayrı sebep var, hepsi bu depoda zaten yazılı kurallara dayanıyor:

1. **CLAUDE.md'nin sert kısıtları sabit**: SQLite-only/tek dosya, sunucu/daemon
   yok, stdlib-only (+numpy/sentence-transformers istisnası). bbb-os-core'un
   HTTP gateway (`mc_client.py`/`mc_gateway.py`), SSH tünelleri, systemd
   servisleri, Telegram/Whisper/Kokoro entegrasyonları bu kısıtları ihlal
   ediyor — bunlar "eksik" değil, bilinçli olarak dışarıda tutulan mimari
   seçimler.
2. **Owner/authority ve epistemic_class/derivation katmanları çok-kişili, canlı
   bir sisteme özgü.** bbb-lite tek kullanıcılı bir öğrenme projesi; "sahip
   kim", "kim onaylayabilir", "bu bilgi STATED mi DERIVED mi" gibi ayrımların
   burada karşılığı yok. Taşınırsa gerçek bir ihtiyacı karşılamayan, test
   edilemeyen ölü kod olur.
3. **Bu hedef zaten bir kez netleştirilmişti**: 2026-08-12'deki ilk oturumda,
   kullanıcıyla birlikte hedef açıkça "%90+ mantıksal/özellik benzerliği —
   mimari değil" olarak belirlenmiş, voice/telegram/SSH/HOSNI-gateway/daemon
   ve bir `phases` tablosu kapsam dışı bırakılmıştı (`memory/decisions.md`,
   2026-08-12 girdisi). "%100'e yakın" hedefi bu önceki kararla gerilim
   içinde — bu raporun amacı bu gerilimi gizlemek değil, açıkça göstermek.

Bu rapordaki "adım adım plan" (bölüm 6), bu üç sınırı ihlal etmeden, GERÇEKTEN
taşınabilir olan maddeleri kapatarak mümkün olan en yüksek mantıksal/özellik
benzerliğini hedefliyor — ama "her tablo, her satır bbb-os-core'dakiyle
birebir aynı olsun" anlamında bir %100 hiçbir zaman doğru ölçüt olmayacak.

---

## 6. Adım adım plan (taşınabilir maddeler, sırayla)

**Adım 1 — Version history: append-only + `supersedes_id`**
- `memory_index`'e `supersedes_id INTEGER` kolonu ekle (kendi tablosuna FK).
- `index_memory_files()`'ın mevcut "içerik değiştiyse UPDATE + version++"
  davranışını değiştir: içerik değiştiğinde eski satırı `status='superseded'`
  olarak BIRAK, yeni bir satır INSERT et (`supersedes_id` = eski satırın id'si,
  `version` = eski + 1). `search_memory()`/`semantic_search()` zaten
  `status='replaced'` satırları dışlıyor — aynı filtreye `'superseded'`
  eklenmesi yeterli.
- Test: aynı dosyayı iki kez farklı içerikle indeksle, eski satırın hâlâ eski
  metniyle DB'de durduğunu, yeni satırın `supersedes_id` ile ona işaret
  ettiğini doğrula.

**Adım 2 — Outbox retry sayacı**
- `events_log`'a `attempts INTEGER DEFAULT 0`, `max_attempts INTEGER DEFAULT 3`
  ekle.
- `mark_consumed()`'a paralel bir `mark_failed(event_id)` ekle: `attempts++`,
  `attempts >= max_attempts` ise `outbox_status='dead'`, değilse
  `outbox_status='pending'` kalsın (yeniden denenebilir).
- Test: 3 başarısız `mark_failed` sonrası `'dead'` durumuna geçtiğini, 2
  başarısızdan sonra hâlâ `'pending'` kaldığını doğrula.

**Adım 3 — Conflict kararını gerekçeli logla**
- `pending_merges`'e (veya ayrı küçük bir `memory_decisions` tablosuna)
  `reason_code TEXT` kolonu ekle (örn. `duplicate`/`outdated`/
  `contradiction`/`low_confidence`).
- `detect_memory_conflicts()`/`detect_decision_conflicts()`'ın döndürdüğü her
  çatışma için, `approve_merge()`'e paralel bir `reject_conflict(id,
  reason_code)` fonksiyonu ekle — böylece sadece "onaylandı" değil "neden
  reddedildi" de kayıt altına alınır.
- `claim_epoch`/`retry_after` gibi çoklu-worker kilit alanlarını EKLEME —
  tek kullanıcıda karşılığı yok.

**Adım 4 — Memory-özel hijyen kontrolü**
- `run_maintenance_checks()`'e yeni bir kontrol ekle: embedding'i NULL olan
  `memory_index` satırlarını ve `replaced`/`superseded` olup da hâlâ
  `search_memory()`'nin varsayılan filtresine sızan satırları bul, `detail`
  alanına bulgu listesini yaz. Receipt zinciri kurma — mevcut
  `maintenance_runs` deseni yeterli.

**Adım 5 — Vektör şemasını sadeleştir (opsiyonel, düşük öncelik)**
- `memory_index.embedding` BLOB'unun yanına `embedding_model TEXT`,
  `embedding_dim INTEGER` kolonları ekle — model değişiminde eski
  vektörlerin hangi modelle üretildiğini ayırt edebilmek için.

Her adım kendi başına, mevcut testleri bozmadan eklenebilir (hepsi additive —
yeni kolon/tablo, davranış değişikliği minimal). Adım 1 ve 2 en somut/düşük
riskli olduğu için önce onlarla başlanması öneriliyor.

---

## 7. Kapsam dışı bırakılanlar (gerekçeli)

- **Owner field / tek-otorite onay modeli** — tek kullanıcılı proje için
  anlamsız.
- **Epistemic_class / memory_derivation katmanı** — çok ağır, production-only,
  bbb-lite'ın basit graph modelinin kapsamının dışında.
- **`claim_epoch` eşzamanlılık kilidi** — çoklu-worker senaryosu, bbb-lite'ta
  yok.
- **Task/decision-level dedup** — bbb-os-core da çözmemiş, mevcut bilinçli
  karar (PROJECT-HISTORY.md bölüm 9) geçerliliğini koruyor.
- **HTTP gateway/mc_client, SSH, systemd, Telegram/Whisper/Kokoro** — CLAUDE.md
  ile doğrudan çelişen mimari parçalar, hiçbir koşulda taşınmayacak.

---

## Kaynaklar

- `PROJECT-HISTORY.md`, bölüm 7 (ilk 7 maddelik parity) ve bölüm 11 (açık
  workstream listesi)
- `memory/decisions.md`, 2026-08-12 girdisi (ilk bbb-os-core taraması, hedefin
  "%90+ mantıksal/özellik benzerliği" olarak netleştirilmesi)
- Bu oturumda yapılan güncel bbb-os-core taraması (main branch, gh CLI ile
  `sql/*.sql` + `lib/memory_*.py` + `lib/mc_*.py` üzerinden)
- `mc.py` (bu depo, güncel hali — 5314 satır, ~29 tablo)
