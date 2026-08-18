# bbb-os-core Memory/DB Parity — Uygulama Listesi

Tarih: 2026-08-17
Kapsam: yalnızca memory/db katmanı (`mc.py`, tek dosya, SQLite-only, sunucu yok,
stdlib-only + numpy/sentence-transformers istisnası — CLAUDE.md'nin sert
kısıtları bu listede de sabit kalıyor).

Bu liste, `BBB-OS-CORE-MEMORY-DB-PARITY-REPORT.md`'nin yerini alır ve onu
doğrulayan oturumun bulgularını içerir. İki fark var:

1. Önceki raporda "production-only, taşınmasın" denen maddeler (owner/authority
   modeli, epistemic_class/derivation katmanı) burada **dahil** — çünkü hedef bu
   kez açıkça "%100'e yaklaşmak". Bu maddeler tek-kullanıcılı bbb-lite'a nasıl
   anlamlı şekilde uyarlanacağıyla birlikte aşağıda var.
2. Doğrulama sırasında bulunan iki gerçek hata düzeltildi: (a) Adım 1'in eski
   ifadesi "`memory_fact` hiç UPDATE edilmiyor" yanlıştı — gerçekte `status`,
   `pinned`, `access_count`, `relevance_score`, `authority` alanları UPDATE
   ediliyor, sadece `value` (içerik) hiçbir zaman yerinde değiştirilmiyor. Bu
   ayrım aşağıda net. (b) bbb-lite'ın `mc.py`'sinde `turns` tablosu vardı ama
   önceki envanterde hiç geçmiyordu; bu listede notu var (bir işlem gerekmiyor,
   sadece kayda geçiyor).

## Kapsam dışı kalanlar (değişmedi)

Bunlar "memory/db" değil "mimari" oldukları için, hedef %100'e yaklaşmak olsa
bile CLAUDE.md'nin sert kısıtlarıyla doğrudan çelişiyorlar ve bu listenin
dışında kalıyor:

- HTTP gateway (`mc_client.py`/`mc_gateway.py`), SSH tünelleri, systemd
  servisleri, Telegram/Whisper/Kokoro entegrasyonları.
- `phases` tablosu ve genel proje-yönetimi genişlemeleri (2026-08-12 kararı
  hâlâ geçerli — bu ayrı bir konu, memory/db değil).

Aşağıdaki 8 adım tamamlandığında, memory/db katmanının **şema disiplini,
karar/gerekçe kaydı, provenance/authority izlenebilirliği ve epistemik
sınıflandırması** bbb-os-core ile mantıksal olarak birebire çok yakın olacak.
Motor (SQLite, tek dosya) zaten ortak.

---

## Adım 1 — Version history: append-only + `supersedes_id`

**Ne:** `memory_index`'e gerçek versiyon geçmişi kazandır. Şu an içerik
değiştiğinde `UPDATE ... version = version + 1` yapılıyor, eski `content`
hiçbir yerde kalmıyor.

**Şema:**
```sql
ALTER TABLE memory_index ADD COLUMN supersedes_id INTEGER
    REFERENCES memory_index(id);
```

**Davranış değişikliği (`index_memory_files()`):**
- İçerik değişmediyse: mevcut satırı olduğu gibi bırak (bugünkü gibi).
- İçerik değiştiyse: `ON CONFLICT DO UPDATE` yerine —
  1. eski satırı `status='superseded'` yap (bu tek UPDATE, içerik alanına
     dokunmuyor),
  2. yeni bir satır INSERT et: `supersedes_id = eski_id`,
     `version = eski_version + 1`, `status='active'`.
- `search_memory()`/`semantic_search()` filtresine `status NOT IN
  ('replaced','superseded')` ekle (şu an sadece `'replaced'` dışlanıyor).

**Not:** bbb-os-core'daki `memory_fact` de aynı mantıkla çalışıyor — `status`
alanı UPDATE edilir (`'accepted'→'superseded'`), ama `value` hiçbir zaman
UPDATE edilmez. Buradaki uygulama da aynı ayrımı koruyor: sadece `status`
UPDATE edilir, `content`/`title`/`keywords` gibi içerik alanları edilmez.

**Test:** aynı dosyayı iki farklı içerikle iki kez indeksle → eski satırın
`status='superseded'` ve eski `content` ile DB'de durduğunu, yeni satırın
`supersedes_id` ile ona işaret ettiğini, `search_memory()`'nin eski satırı
artık döndürmediğini doğrula.

---

## Adım 2 — Outbox retry sayacı

**Ne:** `events_log`'a gerçek retry/dead-letter mantığı ekle
(`notify_outbox.attempts`/`max_attempts` karşılığı).

**Şema:**
```sql
ALTER TABLE events_log ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE events_log ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
```

**Yeni fonksiyon:** `mark_failed(event_id)` — `mark_consumed()`'a paralel:
- `attempts += 1`
- `attempts >= max_attempts` ise `outbox_status = 'dead'`
- değilse `outbox_status` `'pending'` kalır (yeniden denenebilir)

**Test:** 3 kez `mark_failed` çağrısından sonra `'dead'`; 2 çağrıdan sonra hâlâ
`'pending'`.

---

## Adım 3 — Karar + hold state machine (`memory_promotion_decision` + `memory_hold` paritesi)

Bu, önceki raporun "kısmen taşınabilir" dediği maddenin tam versiyonu —
bbb-os-core'daki `memory_promotion_decision`/`memory_hold` çiftinin doğrudan
karşılığı.

**Yeni tablo — kararların kendisi (immutable):**
```sql
CREATE TABLE memory_decisions (
    id TEXT PRIMARY KEY,
    candidate_digest TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN
        ('promote','reject','hold','dedupe','supersede','correct')),
    reason_code TEXT NOT NULL,
    resulting_ids_json TEXT NOT NULL DEFAULT '[]',
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
```
(SQLite'ta gerçek immutability için `BEFORE UPDATE`/`BEFORE DELETE` trigger'ları
ile `RAISE(ABORT, ...)` — bbb-os-core'daki desenin aynısı.)

**Yeni tablo — bekleyen kararların state machine'i:**
```sql
CREATE TABLE memory_hold (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE REFERENCES memory_decisions(id),
    candidate_digest TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('open','claimed','resolved','expired')),
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    retry_after TEXT,
    resolved_by TEXT,
    resolution_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```
+ bbb-os-core'daki `memory_hold_state_lifecycle` trigger'ının birebir
uyarlanmış hali (terminal state'ten çıkış yok, `open→claimed/resolved/expired`,
`claimed→open/resolved/expired`, `claimed` state'ine geçişte `claim_epoch`
ilerlemek zorunda).

**Not — `claim_epoch` neden hâlâ dahil:** bbb-lite tek process/tek kullanıcı
olduğu için bu alan pratikte hiçbir zaman gerçek bir çakışmayı çözmeyecek —
işlevsel olarak "atıl" kalacak. Yine de şema-seviyesinde dahil ediyoruz çünkü
hedef %100'e yaklaşmak; state machine'in kendisi (terminal state kilidi,
geçerli geçiş kontrolü) tek kullanıcıda da gerçek bir fayda sağlıyor
(yanlışlıkla çözülmüş bir hold'u tekrar açmayı engelliyor). Bunu bilerek
bırakıyoruz, gizlemiyoruz.

**Bağlama:** `detect_memory_conflicts()`/`detect_decision_conflicts()`'ın her
tespitinde bir `memory_decisions` satırı yaz. `decision='hold'` olan her satır
için bir `memory_hold` satırı aç. `approve_merge()` artık
`decision='supersede'` veya `'dedupe'` yazan bir sarmalayıcı olsun. Yeni
`reject_conflict(id, reason_code)` fonksiyonu `decision='reject'` yazsın.

**Test:** promote/reject/hold/resolve tam döngüsü — hold açılıp `claimed`'e
geçip `resolved`'e kapanana kadar her adımda geçersiz geçişlerin
`RAISE(ABORT)` ile reddedildiğini doğrula.

---

## Adım 4 — Owner/authority modeli (tek kullanıcıya uyarlanmış)

Önceki raporda "tek kullanıcıda anlamsız" denen madde. Uyarlanmış hali: "sahip
kim" sorusu tek kullanıcıda trivial (her zaman erdi), ama **erdi'nin
doğrudan söylediği vs. agent'ın çıkarım yaptığı** ayrımı tek kullanıcıda da
gerçek bir sinyal — hangi hafıza satırının ne kadar güvenilir olduğunu
etkiler.

**Şema — `memory_index`'e provenance kolonu:**
```sql
ALTER TABLE memory_index ADD COLUMN authority TEXT NOT NULL DEFAULT 'agent'
    CHECK(authority IN ('erdi','agent','inference','external'));
```
(bbb-os-core'daki `haroon/team/agent/external/inference` enum'unun tek
kullanıcıya indirgenmiş hali — `team` çıkarıldı, çünkü bbb-lite'ta ekip yok.)

**Yeni tablo — onay/düzeltme/red makbuzları (immutable):**
```sql
CREATE TABLE memory_confirmations (
    id TEXT PRIMARY KEY,
    memory_id INTEGER NOT NULL REFERENCES memory_index(id),
    decision TEXT NOT NULL CHECK(decision IN
        ('confirmed','corrected','rejected')),
    channel TEXT NOT NULL,
    decided_by TEXT NOT NULL DEFAULT 'erdi',
    proof_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
```
+ `BEFORE UPDATE`/`BEFORE DELETE` immutability trigger'ları.

**Yeni fonksiyon:** `confirm_memory(memory_id, decision, channel,
proof_refs=None)` — bir `memory_confirmations` satırı yazar; `decision in
('corrected','rejected')` ise ilgili `memory_index` satırını `status`
üzerinden işaretler (Adım 1'in `supersedes_id` zincirini tetikleyebilir).

**Test:** bir hafıza satırını `confirmed` yap, sonra `corrected` ile
düzelt — makbuzun immutable kaldığını, `memory_index`'in yeni satırla
supersede edildiğini doğrula.

---

## Adım 5 — Epistemic class + `memory_derivation`

Önceki raporda "çok ağır, production-only" denen madde — %100 hedefi için
dahil, ama en yüksek karmaşıklığa sahip adım olduğu için son sırada ve
opsiyonel/düşük öncelikli işaretli tutulabilir.

**Şema — `memory_index`'e sınıflandırma:**
```sql
ALTER TABLE memory_index ADD COLUMN epistemic_class TEXT NOT NULL
    DEFAULT 'IMPORTED'
    CHECK(epistemic_class IN
        ('STATED','OBSERVED','IMPORTED','SUMMARY','DERIVED','SYNTHETIC'));
ALTER TABLE memory_index ADD COLUMN source_span TEXT;
ALTER TABLE memory_index ADD COLUMN source_digest TEXT;
```
bbb-os-core'daki kural aynen taşınır: `epistemic_class='STATED'` olan bir
satırın `source_span` VE `source_digest` (sha256, 64 hex) dolu olması şart
— SQLite `CHECK` ile.

**Yeni tablo — çıkarım kaydı:**
```sql
CREATE TABLE memory_derivation (
    id TEXT PRIMARY KEY,
    child_id INTEGER NOT NULL REFERENCES memory_index(id),
    parent_ids_json TEXT NOT NULL,      -- json array, length >= 2
    parent_set_digest TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('ABSTRACTION','BEHAVIORAL_INFERENCE')),
    producer TEXT NOT NULL,
    confidence REAL,
    status TEXT NOT NULL CHECK(status IN ('active','stale','held','revoked')),
    created_at TEXT NOT NULL,
    revalidated_at TEXT
);
```

**Bağlama:** yalnızca en az 2 kaynaktan (>=2 `memory_index` satırı) bir genel
sonuç çıkarıldığında (`heal_memory()` sonrası "bu N dosyadan ortak şu
sonuç çıktı" gibi bir akış varsa) bu tabloya yazılır. Bugün bbb-lite'ta böyle
bir çıkarım akışı yoksa, bu adımın ilk teslimatı sadece **şemanın hazır
olması** olabilir — fonksiyonel kullanım ayrı bir iş kalemi.

**Test:** `epistemic_class='STATED'` ile `source_span`/`source_digest`
boşken INSERT'in `CHECK` ihlaliyle reddedildiğini; 2 parent'la bir
`memory_derivation` satırının başarıyla yazıldığını doğrula.

---

## Adım 6 — Memory-özel hijyen taraması

`run_maintenance_checks()`'e bbb-os-core'daki `memory_hygiene_receipt`
handler taksonomisinin karşılığı eklenir (5 handler, receipt zinciri
olmadan — mevcut `maintenance_runs` deseni yeterli, ayrı bir receipt tablosu
zorunlu değil):

- `vector_reconcile` karşılığı: `embedding IS NULL` olan aktif satırları bul.
- `dedup_orphans` karşılığı: `graph_edges`/`context_edges`'te yetim satırlar
  (zaten kısmen var, genişlet).
- `expiry_sweep` karşılığı: `status IN ('replaced','superseded')` olup hâlâ
  `search_memory()`'nin varsayılan filtresine sızan satır var mı.
- `conflict_proposals` karşılığı: Adım 3'teki `memory_hold` tablosunda
  `state='open'` olup `retry_after` süresi geçmiş kayıtlar.

Her biri `run_maintenance_checks()`'in `detail` alanına bulgu listesi olarak
yazılır.

**Test:** her handler için en az bir "bulgu var" ve bir "bulgu yok" senaryosu.

---

## Adım 7 — Vektör şeması sadeleştirme

**Şema:**
```sql
ALTER TABLE memory_index ADD COLUMN embedding_model TEXT;
ALTER TABLE memory_index ADD COLUMN embedding_dim INTEGER;
```
Yeni embedding üretilen her satırda bu iki alan da doldurulur (bugünkü model
adı/boyutu koda gömülü — artık şemada açık). Düşük risk, düşük öncelik;
diğer adımlarla birlikte herhangi bir sırada yapılabilir.

---

## Adım 8 — Doğrulama ve kayıt

- Tüm mevcut test paketi (bugün ~915 test) yeşil kalmalı; her adım kendi
  testleriyle eklenir (additive — hiçbir tabloyu/kolonu silme yok).
- `memory/decisions.md`'ye yeni bir 2026-08-17 girdisi: bu listenin
  2026-08-12'deki "%90+ mantıksal/özellik benzerliği, mimari değil" kararını
  bilerek genişlettiğini, owner/authority ve epistemic_class/derivation'ın
  artık kapsamda olduğunu, mimari kısıtların (HTTP gateway/SSH/Telegram/
  daemon) değişmediğini not et.
- `PROJECT-HISTORY.md`'ye bu implementasyon listesinin tamamlanma durumunu
  işleyecek bir bölüm ekle (adım adım checkbox).
- `mc.py` tablo envanterini güncellerken `turns` tablosunu da listeye ekle —
  önceki raporda atlanmıştı (bir şema değişikliği gerekmiyor, sadece
  dokümantasyon eksikliğiydi).

---

## Sıra önerisi

1 → 2 (bağımsız, düşük risk, hemen) → 3 (temel karar/hold altyapısı) →
4 (3'ün üzerine küçük ek) → 6 (1/3/4'ün bulgularını toplar) → 7 (istenilen
zamanda) → 5 (en karmaşık, en son) → 8 (sürekli, her adımdan sonra).
