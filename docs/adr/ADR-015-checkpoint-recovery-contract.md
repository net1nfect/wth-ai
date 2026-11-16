# ADR-015 — Kontrak Checkpoint Lintas-Engine (Recovery Contract)

| Field | Nilai |
|---|---|
| Status | Accepted |
| Domain | Platform / Reliability |
| Terkait | ADR-010 (fail-closed), ADR-014 (context & checkpoint follow-up) |

## Konteks

Sebelum ADR ini, checkpoint hanya ada sebagai bentuk khusus autopilot
(`{target, cycles_done, attempted[(method,url)], steps[]}`). Audit membuktikan
bentuk itu:

- tidak berversi, sehingga perubahan struktur tidak terdeteksi;
- tidak membedakan "pernah dicoba" dari "selesai dan terverifikasi" — step
  yang terputus (`ran=False`) tetap dilewati saat resume, jadi pekerjaan yang
  belum selesai dianggap beres;
- menerima nilai tidak valid (`cycles_done=-3`) dan melempar `AttributeError`
  pada input berbentuk salah;
- tidak bisa dipakai engine lain tanpa menyalin semantik autopilot.

Kebutuhan recovery berbeda per engine, tetapi konsep umumnya sama. Solusi
harus memisahkan kontrak umum dari payload engine tanpa memaksa satu format
yang terlalu spesifik.

## Keputusan

### 1. `CheckpointEnvelope` sebagai kontrak umum (`wth_ai/jobs/checkpoint.py`)

Field wajib:

| Field | Makna |
|---|---|
| `schema_version` | Versi struktur (`2` saat ini). Versi asing/hilang → `CheckpointValidationError`. |
| `engine` | Harus punya adapter terdaftar di `ENGINE_VALIDATORS`. Saat ini hanya `autopilot`; engine tanpa adapter ditolak (`CheckpointValidationError`), bukan diterima sebagai string bebas. |
| `target` | Identitas eksekusi. Wajib non-kosong; resume hanya berlaku bila target cocok. |
| `cursor` | Posisi eksekusi (cycle/index). Wajib int >= 0. |
| `steps[]` | `StepRecord` per langkah. |
| `resource_budget` | Anggaran saat checkpoint dibuat (`max_cycles`, `max_steps_per_cycle`; 0 = unbounded). Validasi int >= 0. Berguna mendeteksi resume dengan limit berbeda; bukan pengganti validasi cursor. |
| `engine_state` | Payload JSON spesifik engine. Divalidasi adapter pada konstruksi, `to_dict()`, dan `from_dict()`. Tidak untuk secret. |

`StepRecord`: `key`, `operation_id`, `outcome`, `verified`, `attempts`,
`status`, `evidence_ref`, `note`. `operation_id` adalah identitas operasi yang
stabil dan lebih spesifik dari `key`; `identity` mengembalikan
`operation_id` atau `key` sebagai fallback record legacy.

### 2. Klasifikasi status & transisi eksplisit

```
PENDING   -> belum dicoba
INFLIGHT  -> mulai; tidak ada bukti selesai
COMPLETED -> selesai
FAILED    -> dicoba lalu gagal
UNKNOWN   -> hasil tidak diketahui / dari checkpoint lama; bukan sukses
```

`mark()` memvalidasi transisi. `COMPLETED` hanya boleh tetap `COMPLETED`
(tidak bisa kembali ke `INFLIGHT`/`FAILED`/`UNKNOWN`), dan `COMPLETED+verified`
tidak boleh diturunkan menjadi `COMPLETED` tanpa verifikasi. Recovery yang
memang perlu menguji ulang langkah completed harus memakai `reopen(reason=...)`
yang mengosongkan `verified` dan mencatat alasannya.

Aturan resume:

- **`should_skip()`/`should_skip_identity()` hanya true untuk `COMPLETED` dan
  `verified`.** Step `COMPLETED` tanpa verifikasi tetap dijalankan ulang.
- `retryable()` true untuk `FAILED` dan `UNKNOWN`.
- `INFLIGHT` tidak dianggap sukses.
- **Langkah destruktif yang belum `COMPLETED+verified` tidak dijalankan ulang
  otomatis.** Autopilot menandainya `blocked_reason` yang memuat instruksi
  verifikasi + `cleanup`; pengulangan buta bisa menggandakan efek samping.
  Langkah non-destruktif mengikuti aturan retry biasa.
- Hasil destruktif dengan respons HTTP tetap `UNKNOWN`, bukan
  `COMPLETED+verified`: status HTTP membuktikan request selesai, bukan efek
  state yang diinginkan. Tanpa verifier eksplisit, langkah itu tidak boleh
  diulang otomatis.

### 3. Identitas operasi & key HTTP

`make_step_key(method, url)` tetap dipakai untuk kompatibilitas dan tampilan,
tetapi **tidak cukup** membedakan dua operasi berbeda yang menembak URL sama
(mis. `BL-001` dan `BL-002` sama-sama `POST /api/cart`). `probe_operation_id()`
menghasilkan identitas stabil:

- `METHOD URL#payload:<id>` bila probe punya `payload_id` (stabil lintas
  proses);
- `METHOD URL#shape:<digest>` dari field pembentuk request bila tidak ada
  `payload_id`;
- `METHOD URL` sebagai fallback.

Identitas disimpan di `StepRecord.operation_id` dan di setiap langkah
`engine_state`. Resume/dedupe memakai identitas lebih dulu; key hanya fallback
untuk record legacy. Konsekuensinya dua operasi dengan URL sama menghasilkan
dua record, dan satu operasi `UNKNOWN` tidak memblokir sibling-nya.

### 4. Migrasi legacy

`CheckpointEnvelope.from_legacy(data, engine=...)` menerima bentuk v1.
`attempted` dipetakan ke `UNKNOWN` + `verified=False` (konservatif). Langkah
`engine_state` lama yang tidak punya `outcome` juga dipaksa `UNKNOWN` saat
dimuat; flag `ran`/`status` lama tidak pernah dinaikkan menjadi
`COMPLETED+verified` tanpa eksekusi baru.

### 5. Idempotensi, persistensi, dan validasi

- `mark()` adalah upsert per **identitas**: dua operasi berbeda dengan key sama
  menjadi dua record; `mark()` untuk identitas yang sama memperbarui record itu.
- `to_dict()`/`from_dict()` roundtrip stabil dan teruji.
- Input non-mapping → `CheckpointValidationError`; tipe field yang salah juga
  (`cursor` non-int, `status` non-angka, `target` non-string, `ran` non-bool,
  `status` negatif, `outcome` asing).
- `attempts` kumulatif lintas resume (retry state tidak di-reset) dan tidak
  diwarisi antar operasi berbeda yang kebetulan memakai key sama.
- Payload tidak menyimpan secret/body: probe yang disimpan di `engine_state`
  hanya `method`, `url`, `destructive`, `payload_id`. Body/header dibangun
  ulang dari katalog saat resume.
- `JobStore.save_checkpoint()` menulis tmp + `fsync` + `os.replace`, jadi crash
  saat menulis tidak meninggalkan JSON setengah jadi. Untuk dua penulis pada
  job yang sama dipakai `save_checkpoint_cas()` (lihat di bawah).

## Verifikasi efek langkah destruktif (jangkar)

Masalah: status HTTP 200 membuktikan request selesai, bukan bahwa perubahan
state-nya sudah tidak ada. Sebelumnya setiap langkah destruktif berstatus
`UNKNOWN` selamanya, sehingga resume menahannya tanpa jalan keluar.

Keputusan: **anchor read-only**, diambil SEBELUM operasi:

1. `Payload.verify_probe` mendeklarasikan request GET/HEAD yang menyebut objek
   yang disentuh; `verify_fields` menyebut field yang harus ada.
2. Autopilot membaca anchor, menjalankan operasi, lalu membaca anchor lagi.
3. `verify_effect()` membandingkan: indikator efek payload (`-1`, `qty`
   pecahan, `plan` baru, dll.) harus hilang, dan state harus kembali ke
   bentuk jangkar. `COMPLETED` untuk langkah destruktif **hanya** sah bila
   `verification_ref` terisi.
4. Anchor tidak terbaca → `unavailable` (bukan "bersih"). Payload tanpa anchor
   → `UNKNOWN` dengan alasan yang menuntut verifikasi manual.
5. `cleanup_probe` deklaratif dijalankan SETELAH verifikasi (dan juga saat efek
   masih terlihat); hasilnya dilaporkan apa adanya, dan keberhasilan cleanup
   tidak menaikkan verdict efek menjadi `COMPLETED`.

Untuk payload tanpa indikator statis (kelas `GEN-*`):

- Anchor generik `generic_verify_probe()` menunjuk URL operasi dengan method
  read-only (GET, atau HEAD bila operasi HEAD/OPTIONS). Parameter/body operasi
  tidak dibawa.
- Satu-satunya bukti positif adalah **transisi marker**: marker terlihat pada
  anchor SEBELUM operasi, lalu tidak terlihat sesudahnya. Marker yang tidak
  pernah terlihat → `unavailable`, bukan `verified` dan bukan "target rusak".
- `anchor.expected` diperiksa pada keadaan SESUDAH operasi. Field yang hilang
  di sana berarti objek berubah/hilang, bukan kembali ke jangkar.
- Status/header identik **tidak pernah** cukup untuk `verified`; ia hanya
  pembanding informatif (`posture_diff`) untuk alasan penolakan. Header volatil
  (`date`, `etag`, `set-cookie`, `content-length`, `cf-ray`, dll.) diabaikan.

Probe bantu (anchor/cleanup) ditandai `mutating: False` saat dirakit, sehingga
counter operasi, audit, dan test harness bisa membedakannya dari operasi uji.

## Digest definisi payload (anti-drift katalog)

Anchor tidak disimpan di checkpoint; ia dibangun ulang dari katalog. Karena itu
perubahan definisi payload antar-proses bisa mengubah arti "terverifikasi".
Setiap langkah payload membawa `definition_digest` (SHA-256 kanonik dari
`Payload.to_dict()`, termasuk `probe`, `verify_probe`, `verify_fields`, dan
`cleanup_probe`). Saat resume:

- digest yang berbeda → `resume_rejected_reason` menyebut payload yang berubah;
  bukti `COMPLETED` lama diturunkan ke `UNKNOWN` dan tidak dipakai untuk skip;
- langkah non-destruktif dijalankan ulang dari definisi saat ini;
- langkah destruktif TIDAK dijalankan ulang otomatis — ia diblokir dan
  dilaporkan butuh verifikasi manual, karena efek lama mungkin masih ada;
- checkpoint lama tanpa digest tetap kompatibel dan tidak pernah dituduh
  berubah;
- bila sudah ada record dengan digest saat ini, record lama dianggap historis
  sehingga resume berikutnya tidak menolak terus-menerus.

## CAS/lease checkpoint

`CheckpointEnvelope` membawa `run_id` (identitas penulis) dan `revision`
(monoton). `save_checkpoint_cas()` menolak tulisan dengan `revision` ≤ yang
tersimpan, sehingga writer yang bangun terlambat tidak menghapus progres
writer yang lebih baru. Penulis dengan `run_id` berbeda boleh mengambil alih
hanya bila `revision`-nya lebih tinggi. Checkpoint legacy (tanpa
`run_id`/`revision`) tidak mengunci job: revision 0 berarti "tidak diketahui".
Penolakan CAS diperlakukan seperti kegagalan tulis: langkah destruktif
diblokir dan alasannya dilaporkan.

## Kebijakan umur checkpoint (TTL)

Checkpoint membawa `saved_at`. `CheckpointEnvelope.is_stale(max_age)` adalah
predikat murni; `run_autopilot(checkpoint_max_age_seconds=...)` menerapkannya
saat resume dan menolak checkpoint tua dengan `resume_rejected_reason`.
`0`/`None` = tanpa batas (perilaku lama). Checkpoint tanpa `saved_at` tidak
pernah basi — umur tak diketahui bukan alasan membuang state. Di handler,
batas diambil dari `metadata.checkpoint_max_age_seconds` job.

TTL tetap opt-in, tetapi engagement yang mengizinkan langkah destruktif tanpa
batas umur mendapat peringatan eksplisit: `report.checkpoint_policy_warning`
diisi dan handler menuliskannya ke log job. Peringatan ini tidak mengubah
perilaku (tidak ada default tersembunyi), hanya membuat risiko terlihat.

## Checkpoint per-tool recon

`ReconEngine.run()` menerima `checkpoint` dan `resume`. Sebelum setiap tool
dicatat `INFLIGHT`; setelah selesai dicatat `COMPLETED`/`FAILED` + `run`
lengkap (`ToolRun.to_dict`). Resume melewati tool `COMPLETED` dan memutar
ulang `run`-nya ke normalizer, sehingga data lama tetap ada. Tool
`INFLIGHT`/`FAILED` dijalankan lagi (baca ulang aman). Penandaan dari worker
paralel diserialkan dengan lock. `on_start` dipanggil di thread worker TEPAT
sebelum `runner.run`, sehingga `INFLIGHT` sudah tersimpan sebelum tool
menyentuh target. Guard `_save_recon_checkpoint` menangkap kegagalan
konstruksi/validasi envelope maupun kegagalan tulis; tanpa guard itu, exception
dari `on_start` bisa membuat tool sehat dilaporkan `failed` atau tidak pernah
jalan. Payload divalidasi oleh adapter `recon`.

## Metadata risiko saran proaktif

`NextTest` membawa `mutating`, `idempotent`, `reversible`, `blast_radius`,
`requires_auth`, `destructive`. Nilainya diturunkan `derive_risk()` dari
METHOD (fakta HTTP) plus reversibility eksplisit. Reversibility tidak diketahui
→ konservatif destruktif. `_next_to_step()` meneruskan `destructive` ke probe,
sehingga saran POST masuk gerbang checkpoint destruktif yang sama.

## Adapter per engine

| Engine | Status | Catatan |
|---|---|---|
| `autopilot` | **Mendukung resume** | `AutopilotCheckpoint` adalah adapter tipis ke envelope; `run_autopilot(checkpoint=..., resume=...)` menulis snapshot awal, snapshot `INFLIGHT` sebelum setiap langkah, snapshot hasil/exception, dan snapshot akhir cycle. Payload engine = riwayat `AutopilotStep` slim (tanpa body). |
| `recon` | **Mendukung resume per-tool** | `ReconEngine.run(checkpoint=..., resume=...)`. State = mapping tool → {`outcome`, `run`} + `finished` + `profile`. Tool `COMPLETED` dilewati dan hasilnya diputar ulang. |
| `worker` | **Tidak terdaftar** | Worker mengelola status/approval/terminal. Checkpoint ber-engine `worker` ditolak karena belum ada adapter/validator; auto-resume sengaja tidak ditambahkan. |
| `executor` | **Tidak terdaftar** | Tidak ada checkpoint. |

## Tanggung jawab orkestrasi (batas yang tidak dicampur)

| Komponen | Tanggung jawab | Tidak bertanggung jawab |
|---|---|---|
| `PlannerAgent` | Menyusun context berprioritas (pinned policy + body ber-budget) | Menyimpan state job / resume |
| `WorkerEngine` | Claim job, approval gate, sinyal stop kooperatif saat timeout/exception, transisi status terminal berdasarkan verdict handler | Auto-resume engine; menulis checkpoint engine; membunuh thread handler |
| `handler_autopilot` | Menjembatani job → engine, memuat/menulis checkpoint, mencatat degradasi recovery, meneruskan `should_stop` | Menentukan status terminal sendiri (worker yang menetapkan) |
| `run_autopilot` | Eksekusi per-cycle, klasifikasi outcome langkah, snapshot checkpoint, gerbang langkah destruktif | Menyimpan file (callback `checkpoint` yang menyimpan) |
| `JobContext` | Progress/log + sinyal stop (`should_stop`, `request_stop`) | Membunuh thread handler (Python tidak bisa) |
| `JobStore` | Persistensi atomik + load fail-closed | Menafsirkan semantik langkah |
| `ReconEngine` | Satu pass recon; checkpoint per-tool (INFLIGHT sebelum tool, COMPLETED + hasil sesudahnya); baseline/diff | Menyimpan file checkpoint (callback yang menyimpan); menjamin hasil tool idempoten selain "baca ulang aman" |

Status job tetap eksplisit: `queued → running → completed|failed|stopped|cancelled`
(dengan `waiting_approval` sebagai persinggahan). `completed` hanya dipakai
bila handler mengembalikan verdict sukses **dan** tidak ada pekerjaan yang
tertinggal karena stop; `stop_incomplete` membuat handler mengembalikan
`status="stopped"`, sehingga worker tidak menulis `completed` palsu. Langkah
yang tertinggal tetap dilaporkan lewat `unfinished_steps` dan
`blocked_reason`.

## Alternatif yang ditolak

- **Membuat format khusus per engine dari awal** — menduplikasi validasi dan
  semantik status; resume antar engine tidak bisa dibandingkan.
- **Menyimpan seluruh `AutopilotReport` di checkpoint** — mencampur output
  job dengan state recovery, membengkak, dan berisiko menyimpan isi respons.
- **Menganggap `attempted` = selesai** — kesalahan yang justru ditemukan di
  audit; menimbulkan pekerjaan yang hilang diam-diam.
- **Auto-resume di WorkerEngine** — mengulang tindakan tanpa verifikasi
  keadaan; tidak aman untuk handler yang punya efek samping.

## Konsekuensi

- Recovery autopilot sekarang: hanya skip `COMPLETED+verified` per
  **identitas operasi**; retry `FAILED`; ulangi `INFLIGHT`/`UNKNOWN` untuk
  langkah non-destruktif; blokir langkah destruktif yang belum terverifikasi.
- Snapshot checkpoint ditulis SEBELUM cycle pertama, **SEBELUM setiap langkah
  destruktif dikirim** (`INFLIGHT`), setelah setiap hasil/exception, dan di
  akhir cycle. Crash di mana pun setelah `INFLIGHT` tidak bisa membuat operasi
  destruktif dianggap belum pernah jalan.
- Langkah destruktif **tidak dijalankan sama sekali** bila checkpoint
  `INFLIGHT` gagal dipersistensikan: tanpa checkpoint, efeknya tidak bisa
  direkonsiliasi setelah crash. Run berhenti dengan `stop_incomplete=True`.
- Kegagalan menulis checkpoint dicatat di `report.checkpoint_error` dan
  **tidak** mengubah `stopped_reason` (dua kegagalan berbeda tidak dicampur).
- `should_stop` diteruskan handler → engine. Worker memanggil
  `ctx.request_stop()` saat timeout/exception, dan engine berhenti di batas
  cycle/step setelah menyimpan hasil langkah yang sedang berjalan. Job
  dilaporkan `stopped`, bukan `completed`, bila ada pekerjaan tertinggal.
- Handler melaporkan `unfinished_steps`, `blocked_reason`,
  `resource_budget_changed`, dan `checkpoint_error` supaya job `completed`
  tidak menyamarkan pekerjaan atau degradasi recovery.
- Resume dengan target berbeda / cursor melebihi `max_cycles` ditolak dan
  dilaporkan lewat `report.resume_rejected_reason`. Perubahan resource budget
  dilaporkan (`resource_budget_changed`), bukan dipakai untuk membuang bukti
  completed. Perubahan definisi payload juga dilaporkan di sana dan membuat
  bukti `COMPLETED` lama tidak dipakai.
- Engagement dengan langkah destruktif tanpa TTL mengisi
  `report.checkpoint_policy_warning`; handler menuliskannya ke log dan
  mengembalikannya di hasil job. Ini peringatan, bukan error, dan tidak
  mengubah status job.

## Batas yang diketahui

- Langkah non-destruktif yang `INFLIGHT`/`UNKNOWN` diulang saat resume.
- **Verifier efek bersifat deklaratif, bukan universal.** `destructive_verify`
  membandingkan anchor read-only (diambil SEBELUM operasi) dengan keadaan
  sesudahnya, memakai indikator efek per payload + diff field anchor. Payload
  `GEN-*` memakai anchor generik dan hanya bisa `verified` lewat transisi
  marker; marker yang tidak pernah terlihat tetap `UNKNOWN`/`unavailable`.
  Menambah anchor/indikator adalah pekerjaan katalog, bukan sesuatu yang bisa
  ditebak verifier.
- **Anchor dibangun ulang dari katalog saat resume, bukan disimpan.** Bentuknya
  deterministik dari placeholder + nilai tetap. Perubahan definisi antar-proses
  dideteksi oleh `definition_digest` per langkah: bukti `COMPLETED` lama tidak
  dipakai, langkah non-destruktif diulang, dan langkah destruktif diblokir
  sampai operator memverifikasi keadaan. Checkpoint lama tanpa digest tetap
  diterima (kompatibilitas).
- **Cleanup hanya untuk `cleanup_probe` deklaratif.** Instruksi `cleanup` teks
  (mis. "hapus baris keranjang") tetap manual. Cleanup yang gagal dilaporkan
  sebagai gagal di `note` dan tidak pernah menaikkan verdict efek ke
  `COMPLETED` — pemulihan bukan bukti bahwa bug-nya tidak ada.
- **Gerbang destruktif menghentikan run, bukan melewati satu langkah.** Bila
  checkpoint `INFLIGHT` untuk kandidat destruktif pertama gagal ditulis (atau
  ditolak CAS), engine berhenti dengan `stop_incomplete=True`; langkah
  non-destruktif setelahnya tidak dijalankan pada run itu.
- **TTL checkpoint opt-in, bukan default.** `checkpoint_max_age_seconds`
  (`metadata` job) menentukan batas; `0`/tidak diset = tanpa batas (perilaku
  lama). Checkpoint tanpa `saved_at` tidak pernah dianggap basi. Tidak ada
  default umur yang "diciptakan" oleh mesin. Bila engagement mengizinkan
  langkah destruktif, ketiadaan batas dilaporkan sebagai
  `checkpoint_policy_warning` (log + hasil job), bukan ditegakkan diam-diam.
- **CAS melindungi penulis checkpoint, bukan proses.** `save_checkpoint_cas`
  menolak snapshot dengan `revision` basi sehingga progres proses lain tidak
  hilang diam-diam. Thread handler yang sudah timeout tetap bisa menyelesaikan
  satu request in-flight; sinyal stop memperkecil jendela ini, bukan
  menghilangkannya.
- **Thread handler tidak bisa dibunuh.** Timeout bersifat kooperatif; engine
  berhenti di batas berikutnya, bukan membatalkan request yang sudah terkirim.
- **Recon hanya resumable per-tool untuk tool builtin/external.** Setiap tool
  dianggap "baca ulang aman" saat `INFLIGHT`/`FAILED`; hasil `COMPLETED`
  diputar ulang ke normalizer sehingga data tidak hilang. Tahap non-tool
  (scoring, report) selalu dihitung ulang. Recon tetap tanpa baseline pihak
  ketiga; `WorkerEngine` tetap tanpa auto-resume.
- **Adaptive bypass tidak di-checkpoint per langkah.** Bypass mewarisi sifat
  destruktif induk dan disimpan pada snapshot berikutnya; crash di tengah
  retry adaptif kembali ke status `INFLIGHT` induk dan tidak mengulang bypass
  destruktif secara otomatis.

## Verifikasi

Diuji oleh:

- `tests/test_checkpoint_contract.py` — validasi adapter per-engine, penolakan
  engine tanpa adapter.
- `tests/test_checkpoint_store_integration.py` — roundtrip store.
- `tests/test_autopilot_checkpoint.py`, `tests/test_autopilot_resume_semantics.py`
  — semantik resume.
- `tests/test_recovery_observability.py`,
  `tests/test_autopilot_handler_wiring.py`,
  `tests/test_worker_and_shell_timeout_reliability.py`.
- `tests/test_crash_consistency_fault_injection.py` — 21 skenario fault-injection
  in-memory.
- `tests/test_crash_simulation_e2e.py` — 9 skenario lewat `JobStore` NYATA
  (crash sebelum snapshot, setelah mutasi, setelah verifikasi, penulisan gagal,
  resume berulang, checkpoint korup, skema tak kompatibel).
- `tests/test_destructive_verify.py` — 14 kasus verifier-jangkar (urutan anchor,
  efek tersisa, cleanup jujur, `COMPLETED` wajib `verification_ref`).
- `tests/test_destructive_verify_generic.py` — 16 kasus payload tanpa indikator
  statis (transisi marker, marker tidak pernah terlihat, expected hilang,
  posture informatif, cleanup dengan `before_had_marker`).
- `tests/test_autopilot_catalog_digest.py` — 7 kasus digest definisi payload
  (persistensi, perubahan anchor, penolakan bukti lama, blokir destruktif,
  legacy tanpa digest, anti-penolakan berulang).
- `tests/test_checkpoint_cas.py` — 12 kasus CAS (clobber, takeover, legacy).
- `tests/test_checkpoint_ttl.py` — 15 kasus kebijakan umur + peringatan TTL
  untuk engagement destruktif.
- `tests/test_recon_per_tool_checkpoint.py` — 10 kasus checkpoint per-tool.
- `tests/test_recon_parallel_crash_ordering.py` — 3 kasus guard di jalur
  paralel: konstruksi envelope gagal tanpa mematikan tool, `INFLIGHT` sebelum
  `runner.run`, dan resume setelah crash (COMPLETED dilewati + replay,
  INFLIGHT dijalankan ulang).
- `tests/test_proactive_risk_metadata.py` — 17 kasus metadata risiko.

Gate CI terpisah: `.github/workflows/crash-consistency.yml`.

Hasil aktual dicatat di ADR-014.

