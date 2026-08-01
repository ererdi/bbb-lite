# Data Ownership Matrix

Codex/Haroon ekip belgesinin 4. maddesi ("data ownership": hangi veri
local'de mi server'da mı tutuluyor, tek makine offline kaldığında ne
olur, çakışan yazmalar nasıl çözülür) bu projeye uyarlanmıştır.

> **Not:** Tüm veriler local-only. Bu proje HOSNI/server içermiyor
> (bilinçli mimari karar, bkz. `memory/decisions.md`). `mc.db` tek bir
> yerel SQLite dosyasıdır ve ona yazan tek işlem `mc.py`'dir (bkz.
> `CLAUDE.md`: "Exactly one file, `mc.py`, touches the database
> directly"). Bu tablo, Haroon'un gerçek sisteminde (Mini/HOSNI
> ayrımı olan, çok bileşenli bir mimaride) bu ayrımın nasıl yapılması
> GEREKTİĞİNİ gösteren bir şablon — bugünkü bbb-lite için değil,
> ileride çok-makineli/sunucu-destekli bir versiyon düşünülürse
> referans olsun diye hazırlandı.
>
> Bu yüzden aşağıdaki 28 satırın hepsinde:
> - **Local/Server/Both** = `Local (only)`
> - **Sync Rule** = `N/A - single machine, no sync needed`
> - **Conflict Strategy** = `N/A - single writer (mc.py), no concurrent access`
>
> Tekrarı azaltmak için bu üç sütun satır satır yazılmadı; her satırda
> yalnızca **Source of Truth** (o tablonun verisini kim/ne üretiyor) ve
> **Offline Behavior** (dosyaya erişilemezse ne olur) dolduruldu.

## Tablo

| Tablo | Local/Server/Both | Source of Truth | Sync Rule | Offline Behavior | Conflict Strategy |
|---|---|---|---|---|---|
| sessions | Local (only) | `mc.py` — SessionStart hook, her yeni session_id (ve Stage AH'teki client_session_uuid varsa) burada doğar | N/A - single machine, no sync needed | Dosya yoksa/okunamıyorsa hook başında `init_db()` çağrısı dosyayı yaratır; gerçek "erişilemez" durumu (örn. kilitli/salt-okunur disk) sqlite3.OperationalError olarak patlar | N/A - single writer (mc.py), no concurrent access |
| checkpoints | Local (only) | `mc.py` — Stop hook, oturum sonunda current_task/next_action/risk yazılır | N/A - single machine, no sync needed | Yazılamazsa checkpoint kaybolur, bir sonraki SessionStart eski (son başarılı) checkpoint'i okur | N/A - single writer (mc.py), no concurrent access |
| tasks | Local (only) | `mc.py` — task CRUD fonksiyonları (create/list/complete_task) | N/A - single machine, no sync needed | Erişilemezse task durumu güncellenemez, en son bilinen durum geçerli sayılır | N/A - single writer (mc.py), no concurrent access |
| projects | Local (only) | `mc.py` — proje kaydı, Stage AG'deki cwd-eşleşmesiyle okunur | N/A - single machine, no sync needed | Eşleşme yapılamazsa Stage AG ilkesine göre 'unmapped' döner, sessizce bir projeye bağlanmaz | N/A - single writer (mc.py), no concurrent access |
| sprints | Local (only) | `mc.py` — project_id'ye bağlı sprint kaydı | N/A - single machine, no sync needed | Erişilemezse sprint bilgisi olmadan devam edilir | N/A - single writer (mc.py), no concurrent access |
| memory_index | Local (only) | `mc.py` — `index_memory_files()`, `memory/*.md` dosyalarından türetilir | N/A - single machine, no sync needed | Yeniden çalıştırılabilir: kaynak `.md` dosyaları hâlâ diskte, index kaybolsa da `index_memory_files()` yeniden kurar | N/A - single writer (mc.py), no concurrent access |
| task_activity | Local (only) | `mc.py` — task üzerindeki her aktivite kaydı (append-only log) | N/A - single machine, no sync needed | Erişilemezse o aktivite kaydedilmez, geçmiş log'da boşluk oluşur (append-only olduğu için geri dönüş yok) | N/A - single writer (mc.py), no concurrent access |
| roles | Local (only) | `mc.py` — Stage AF/AH'teki rol deklarasyonu, session_id (veya client_session_uuid) başına | N/A - single machine, no sync needed | Erişilemezse rol bilinmiyor sayılır, brozzer varsayılan role düşülür (Stage AF'nin düzelttiği bug'ın tersi durumu) | N/A - single writer (mc.py), no concurrent access |
| decisions | Local (only) | `mc.py` — `memory/decisions.md`'nin DB karşılığı, karar kaydı append-only | N/A - single machine, no sync needed | Erişilemezse karar DB'ye yazılmaz; `memory/decisions.md` insan-okunur yedek olarak kalır | N/A - single writer (mc.py), no concurrent access |
| documents | Local (only) | `mc.py` — project_id'ye bağlı doküman içerikleri | N/A - single machine, no sync needed | Erişilemezse doküman okunamaz/güncellenemez | N/A - single writer (mc.py), no concurrent access |
| graph_nodes | Local (only) | `mc.py` — `propose_node()`/`approve_node()`, Stage AC'den beri context_nodes'un yerini aldı | N/A - single machine, no sync needed | Erişilemezse context-map boş döner (list_context_map curated=1 filtresiyle çalışır) | N/A - single writer (mc.py), no concurrent access |
| graph_edges | Local (only) | `mc.py` — `add_curated_edge()` (zorunlu dual-approval kapısı) ve `curate_graph_edge()` (advisory, Stage S) | N/A - single machine, no sync needed | Erişilemezse context-map edge'siz döner | N/A - single writer (mc.py), no concurrent access |
| doc_index | Local (only) | `mc.py` — `index_docs()`/`upsert_doc_file()`, Stage AN; kaynak proje dosyaları | N/A - single machine, no sync needed | Yeniden çalıştırılabilir: kaynak dosyalar diskte, `index_docs()` yeniden kurar | N/A - single writer (mc.py), no concurrent access |
| symbol_index | Local (only) | `mc.py` — `index_symbols()`/`upsert_symbol_file()`, Stage AN; AST tabanlı, kaynak .py dosyaları | N/A - single machine, no sync needed | Yeniden çalıştırılabilir: kaynak dosyalar diskte, `index_symbols()` yeniden kurar | N/A - single writer (mc.py), no concurrent access |
| compact_snapshots | Local (only) | `mc.py` — `save_snapshot()`, PreCompact/PostCompact anlarında session state fotoğrafı | N/A - single machine, no sync needed | Erişilemezse snapshot alınmaz, compare_snapshots eksik veriyle çalışır (fail-soft) | N/A - single writer (mc.py), no concurrent access |
| task_completions | Local (only) | `mc.py` — `complete_task()`, Stage AE/AP; evidence tablosuna zorunlu bağlı | N/A - single machine, no sync needed | Erişilemezse görev tamamlanamaz (evidence kaydı da atılamayacağı için complete_task baştan başarısız olur) | N/A - single writer (mc.py), no concurrent access |
| turns | Local (only) | `mc.py` — konuşma turu logu (speaker/role/message) | N/A - single machine, no sync needed | Erişilemezse o tur loglanmaz | N/A - single writer (mc.py), no concurrent access |
| decision_packets | Local (only) | `mc.py` — dispatch öncesi karar paketi (id TEXT, dış sistemlerden gelen bir kimlikle eşleşebilir) | N/A - single machine, no sync needed | Erişilemezse paket oluşturulamaz, dispatch başlatılamaz | N/A - single writer (mc.py), no concurrent access |
| dispatch_runs | Local (only) | `mc.py` — decision_packets'e bağlı çalıştırma kaydı, durum/özet | N/A - single machine, no sync needed | Erişilemezse run durumu güncellenemez, son bilinen durum geçerli sayılır | N/A - single writer (mc.py), no concurrent access |
| agent_outputs | Local (only) | `mc.py` — dispatch_run_id'ye bağlı rol bazlı çıktı logu | N/A - single machine, no sync needed | Erişilemezse çıktı kaydedilmez | N/A - single writer (mc.py), no concurrent access |
| evidence | Local (only) | `mc.py` — `complete_task()`/test-kanıtı zorunluluğu (Stage AE/AP) burayı besler | N/A - single machine, no sync needed | Erişilemezse kanıt kaydı oluşmaz, buna bağlı complete_task de başarısız olur | N/A - single writer (mc.py), no concurrent access |
| events_log | Local (only) | `mc.py` — genel olay logu (event_type/source), append-only | N/A - single machine, no sync needed | Erişilemezse olay kaydedilmez | N/A - single writer (mc.py), no concurrent access |
| capability_boundaries | Local (only) | `mc.py` — capability adı PRIMARY KEY, enabled/reason çiftiyle manuel/hook tarafından set edilir | N/A - single machine, no sync needed | Erişilemezse yetenek durumu okunamaz; çağıran kod muhafazakâr (devre dışı) varsaymalı | N/A - single writer (mc.py), no concurrent access |
| answer_quality_scores | Local (only) | `mc.py` — `session_score()` tabanlı puanlama (Stage Y), turn_id'ye bağlı | N/A - single machine, no sync needed | Erişilemezse puanlama yapılmaz, session_warn.py eksik veriyle çalışır | N/A - single writer (mc.py), no concurrent access |
| answer_receipts | Local (only) | `mc.py` — cevap başına makbuz; decision_packet/evidence/quality_score id'lerini birbirine bağlar | N/A - single machine, no sync needed | Erişilemezse makbuz oluşmaz | N/A - single writer (mc.py), no concurrent access |
| lifecycle_runs | Local (only) | `mc.py` — her hook çalıştığında (hook_name/source/status) burayı loglar | N/A - single machine, no sync needed | Erişilemezse o hook çalışması loglanmaz (ama hook'un kendisi genelde fail-soft devam eder) | N/A - single writer (mc.py), no concurrent access |
| context_nodes | Local (only) — **deprecated (Stage AC)** | `mc.py` — artık yazılmıyor, sadece eski satırların kaybolmaması için tablo duruyor; yerini `graph_nodes` aldı | N/A - single machine, no sync needed | Erişilemezse hiçbir etkisi yok (aktif kod yolu artık graph_nodes/graph_edges okuyor) | N/A - single writer (mc.py), no concurrent access |
| context_edges | Local (only) — **deprecated (Stage AC)** | `mc.py` — artık yazılmıyor, sadece eski satırların kaybolmaması için tablo duruyor; yerini `graph_edges` aldı | N/A - single machine, no sync needed | Erişilemezse hiçbir etkisi yok (aktif kod yolu artık graph_nodes/graph_edges okuyor) | N/A - single writer (mc.py), no concurrent access |

28 tablo (mc.py `init_db()` içindeki tüm `CREATE TABLE` ifadeleri, `context_nodes`/
`context_edges` dahil — Stage AQ görev tanımındaki 25'lik listede bu ikisi
unutulmuş, deprecated olsalar da eksiksizlik için buraya eklendi).

## En yakın "server unavailable" karşılığı

Haroon'un sistemindeki "server (HOSNI) erişilemez, Mini'ye düş" senaryosunun
bizdeki en yakın karşılığı: **"mc.db dosyasına erişilemez mi?"** kontrolü.
Bunun için `mc.py`'ye `db_health_check(db_path=DB_PATH) -> dict` eklendi —
bkz. fonksiyonun docstring'i. Dosya yoksa/okunamıyorsa `reachable: False`
döner; `mode` her zaman `"local-only"` — çünkü düşülecek başka bir mod yok.
