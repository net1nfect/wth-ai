# ADR-014 — Context Budget Berprioritas & Enforcement Scope di Transport

| Field | Nilai |
|---|---|
| Status | Accepted |
| Domain | AI/Agent + Security/Platform |
| Pengganti | — |

## Konteks

Dua celah konkret ditemukan saat audit (bukan asumsi):

1. **Context budget buta.** `ContextBuilder.build(char_budget=N)` hanya
   melakukan `text[:N]`. Preamble invarian (scope / authorized / permission /
   phase) kebetulan berada di awal sehingga tidak terpotong, tetapi blok
   sumber besar dipotong buta di ekor tanpa marker dan tanpa mempertimbangkan
   prioritas. Model bisa kehilangan error/summary di ekor, dan operator tidak
   bisa membedakan "konteks penuh" dari "konteks terpotong".

2. **Enforcement scope tidak di transport.** `ScopeManager.is_in_scope()`
   dicek di `ExecutorAgent.execute_module()` (satu titik konvergensi untuk
   vuln executor), tetapi `ProbeSession` — satu-satunya transport HTTP
   pipeline — tidak memeriksa scope sama sekali. `supervisor_dispatch`
   membuat `ProbeSession` langsung, sehingga path itu bisa mengirim request
   ke host out-of-scope tanpa melewati gate.

## Keputusan

### 1. `BudgetedContextBuilder` (additive)

- Invarian keamanan (target, authorized, permission, phase, queued tasks,
  peringatan scope) dibangun oleh `invariant_block()` dan **tidak pernah
  dipotong**, berapa pun budget.
- Sumber lain dialokasikan berdasarkan `SOURCE_PRIORITY`; prioritas rendah
  dipotong lebih dulu.
- Section besar memakai **head + tail** dengan marker truncation eksplisit
  (`…[truncated N chars]…`), sehingga ekor (biasanya error/summary) tidak
  hilang diam-diam.
- `budget<=0` = unbounded (kompatibel dengan perilaku lama).
- `ContextBuilder.build` lama **tidak diubah**.

### 2. `scope_guard` di `ProbeSession`

- `ProbeSession(scope_guard=...)` menolak request `BLOCKED_BY_SCOPE` sebelum
  throttle/koneksi, untuk **kedua** transport (injected sender dan requests).
- Guard menggunakan `ScopeManager` yang di-seed dari `state.scope` lewat
  `scope_guard_from()`, sehingga aturan matching tidak diduplikasi.
- Fail-closed: URL kosong, guard error → deny.
- Blocked request tetap menghasilkan `Observation` dengan
  `transcript_id` stabil (`TX-BLOCK-<sha>`), jadi keputusan blokir tercatat
  sebagai bukti, bukan hilang.
- Guard **tidak aktif** bila scope policy sama sekali tidak dikonfigurasi
  (komponen diuji terisolasi). Ini tidak melemahkan runtime: jalur runtime
  selalu punya target/in_scope/authorized, dan `execute_module()` tetap
  menolak modul vuln tanpa scope.

### 3. Job idempotency & checkpoint (additive)

- `create_job(..., idempotency_key="k")`: key sama + intent sama →
  mengembalikan job yang sama; key sama + intent berbeda → job baru
  berstatus `failed` dengan alasan `idempotency conflict` (fail-closed:
  request nyata tidak boleh hilang diam-diam).
- `JobStore.save_checkpoint()/load_checkpoint()`: persist atomik
  (tmp + `os.replace` + fsync), `load` fail-closed (`None` bila korup).
- `retry_job` tidak menghapus checkpoint, sehingga resume aman.

## Alternatif yang ditolak

- **Menaikkan budget default / membiarkan `[:N]`** — tidak menyelesaikan
  kehilangan invarian/ekor dan tetap tidak teramati.
- **Guard hanya di `execute_module()`** — satu call site baru (dispatch)
  cukup untuk bypass; enforcement harus di titik eksekusi.
- **Guard default-allow saat scope kosong** — ditolak; yang dipakai adalah
  "tidak ada policy = tidak ada yang ditegakkan", bukan "policy kosong =
  izinkan semua". Perbedaan ini penting dan dijaga oleh `execute_module()`.
- **Schema migration untuk idempotency** — tidak perlu; key disimpan di
  `metadata` (JSON) dengan fallback scan bila JSON1 tidak tersedia.

## Konsekuensi

- +24 regression test (context budget, idempotency/checkpoint, scope
  enforcement unit + integrasi). Full suite: 1647 passed.
- `ProbeSession.blocked_count` tersedia untuk observabilitas.
- Menambah section baru ke budget: cukup daftarkan prioritasnya di
  `SOURCE_PRIORITY`.

## Tindak lanjut yang sudah dikerjakan dan DIVERIFIKASI

### A. Context budget — validasi, invarian eksplisit, telemetri

Gap yang ditemukan lewat reproduksi (bukan dari ringkasan):

| Gap | Reproduksi | Perbaikan |
|---|---|---|
| `budget` negatif diam-diam = unbounded | `budget=-5` -> seluruh konten | `normalize_budget()` menormalkan eksplisit; `report.normalized_from` merekam input asli |
| `budget` non-int -> `TypeError` mentah | `budget="abc"` | `normalize_budget()` -> `ValueError` |
| `render_section` melebihi budget | budget=39 -> output 42 | marker adaptif; `len(out) <= budget` dijamin |
| Invarian > budget dilewati tanpa sinyal | body 3000 vs budget 50 | `BudgetExceededError` (default) atau `overflow="keep_invariant"` + `report.invariant_overflow` |
| **Body bocor setelah budget habis** | skill 20000 char tembus dengan budget 4000 | `allocation<=0` -> section di-drop; `0` tidak pernah diteruskan ke `render_section` (di mana `0` = unbounded) |
| Docstring `build()` kontradiktif | dokumen "TOTAL", kode "BODY" | docstring dikoreksi; kontrak eksplisit |
| Dua mekanisme invarian (argumen `invariant=` vs `pinned` section) | — | `ContextSection(pinned=True)` digabung ke blok invarian; planner memakai `pinned` saja |

Kontrak final yang berlaku:

- `budget` adalah **character budget untuk BODY**. Tidak ada konversi implisit
  ke token model; pemetaan token ada di pemanggil.
- Invarian (pinned + `invariant=`) tidak pernah dipotong. Jika
  `len(invariant) >= budget`: default `raise` (atau `keep_invariant`),
  invarian memakan seluruh anggaran dan body di-drop — dilaporkan.
- `budget<=0` (termasuk `None`, negatif, string "0") = unbounded, sesuai
  perilaku lama. Input non-numerik = `ValueError`.
- `ContextBudgetReport` merekam `budget`, `invariant_chars`, `body_chars`,
  `total_chars`, `truncated_sections`, `dropped_sections`, `section_chars` —
  tidak mengandung isi konteks, aman dilog.
- Prioritas sama -> urutan deterministik berdasarkan nama (diuji).

Planner: `_build_context` memakai `pinned=True` untuk blok kebijakan dan
`overflow="keep_invariant"` sehingga turn tidak pernah gagal karena
aritmetika budget; satu aturan budget (delegasi penuh ke builder, tanpa
cabang `<=0` lokal).

### B. Checkpoint & recovery

Kontrak sesungguhnya sekarang `CheckpointEnvelope` (lihat ADR-015), bukan
`AutopilotCheckpoint`. Yang terbukti diperbaiki:

| Gap | Reproduksi | Perbaikan |
|---|---|---|
| Tanpa versi skema | `to_dict()` tak punya versi | `schema_version=2`; versi asing/hilang -> `CheckpointValidationError` |
| `cycles_done` negatif diterima | `-3` roundtrip | `cursor` wajib int >= 0 |
| `from_dict(list)` -> `AttributeError` | — | `CheckpointValidationError` |
| Hanya "attempted" | step `ran=False` dianggap selesai | status eksplisit: `COMPLETED`/`FAILED`/`INFLIGHT`/`PENDING`/`UNKNOWN` |
| Resume "attempted" dianggap sukses | — | hanya `COMPLETED` **dan** `verified` yang dilewati |
| `cycles_done > max_cycles` | cycles 5/3 | cursor dipangkas ke `max_cycles`, alasan dilaporkan |
| Kegagalan checkpoint tidak terlihat | — | `report.checkpoint_error` (terpisah dari `stopped_reason`) |
| Resume ditolak vs tidak ada checkpoint tak terbedakan | — | `report.resume_rejected_reason` |
| Dua operasi beda dengan method+URL sama dianggap satu | `BL-001`/`BL-002` sama-sama `POST /api/cart`; resume melewati sibling | `operation_id` (payload id / digest request) + `StepRecord.operation_id`; dedupe/resume per identitas |
| Kandidat duplikat dieksekusi dua kali | `BL-001, BL-002, BL-001` | dedupe per identitas dalam satu run |
| Crash setelah request tanpa jejak | checkpoint hanya ditulis di akhir cycle | snapshot `INFLIGHT` dipersistensikan **sebelum** langkah dikirim |
| Checkpoint gagal ditulis tetapi langkah destruktif tetap jalan | — | langkah destruktif diblokir; run `stop_incomplete`; error terpisah |
| Respons destruktif dianggap `COMPLETED+verified` | status 200 tanpa verifier | outcome `UNKNOWN`; tidak pernah di-retry otomatis |
| Transisi status ilegal diterima | `COMPLETED -> INFLIGHT` | tabel transisi + `reopen(reason=...)` eksplisit |
| Perubahan resource budget tak terlihat | resume dengan limit berbeda | `report.resource_budget_changed` |
| Timeout worker meninggalkan thread orphan yang terus bekerja | job `failed`, handler thread tetap probe | `JobContext.request_stop()` + `should_stop` diteruskan ke engine; handler berhenti di batas berikutnya |

Migrasi v1 -> v2 konservatif: `attempted` lama dipetakan ke `UNKNOWN`
(`verified=False`) sehingga **tidak** dilewati otomatis.

## Batas yang tersisa (dinyatakan, tidak diklaim)

- **`ReconEngine` tidak resumable per-tool.** `run()` adalah satu pass tanpa
  state per-langkah; tidak ada checkpoint yang dipasang. Resume recon yang
  tersedia hanyalah *baseline/diff* (`_load_baseline`) untuk run berikutnya,
  yang merupakan fitur berbeda dari pemulihan per-tool.
- **`WorkerEngine` belum melakukan auto-resume.** Worker hanya mengelola
  transisi status/approval/terminal. Checkpoint ber-engine `worker` bahkan
  ditolak oleh kontrak v2 karena belum ada adapter/validator; resume otomatis
  akan mengulang aksi tanpa verifikasi, jadi sengaja tidak ditambahkan.
- Resume autopilot mengulang langkah **non-destruktif** yang tidak
  `COMPLETED+verified`. Langkah destruktif diverifikasi dengan anchor read-only
  (lihat ADR-015); tanpa anchor deklaratif ia tetap `UNKNOWN` dan tidak
  dijalankan ulang otomatis.
- **Verifier efek bersifat deklaratif.** Hanya payload yang punya
  `verify_probe` di katalog yang bisa menjadi `COMPLETED` otomatis; payload
  hasil generasi (`GEN-*`) tetap `UNKNOWN` sampai anchor ditambahkan. Anchor
  dibangun ulang dari katalog saat resume, bukan disimpan di checkpoint.
- **TTL checkpoint opt-in.** `checkpoint_max_age_seconds` menentukan batas;
  default tanpa batas. Checkpoint tanpa `saved_at` tidak pernah dianggap basi.
- **CAS melindungi checkpoint, bukan proses.** `save_checkpoint_cas` menolak
  snapshot basi; thread handler yang sudah timeout masih bisa menyelesaikan
  satu request in-flight. Claim job atomik tetap mencegah double-dequeue.
- **Thread handler tidak bisa dibunuh.** Timeout bersifat kooperatif; engine
  berhenti di batas cycle/step berikutnya.
- **Recon resumable per-tool**, tetapi tahap non-tool (scoring/report) selalu
  dihitung ulang. `WorkerEngine` tetap tanpa auto-resume.
- **Adaptive bypass tidak di-checkpoint per langkah.** Bypass mewarisi sifat
  destruktif induk dan tersimpan pada snapshot berikutnya; crash di tengah
  retry adaptif kembali ke `INFLIGHT` induk.
- Checkpoint tidak menyimpan body respons, header, kredensial, atau token;
  hanya key/identitas langkah, status, referensi evidence, dan `engine_state`
  milik engine.

## Hasil verifikasi aktual (dijalankan, bukan diasumsikan)

- Full suite: **1882 passed** (148s) pada run final setelah rekomendasi 1–6.
- Targeted: **222 passed** (13 file context/checkpoint/autopilot/recon/jobs).
- Fault-injection in-memory: **21 passed** (`test_crash_consistency_fault_injection.py`).
- Simulasi crash end-to-end lewat `JobStore` nyata: **9 passed**
  (`test_crash_simulation_e2e.py`).
- Verifier destruktif: **14 passed** (`test_destructive_verify.py`).
- CAS checkpoint: **12 passed** (`test_checkpoint_cas.py`).
- Kebijakan TTL: **11 passed** (`test_checkpoint_ttl.py`).
- Checkpoint per-tool recon: **10 passed** (`test_recon_per_tool_checkpoint.py`).
- Metadata risiko proaktif: **17 passed** (`test_proactive_risk_metadata.py`).
- Property/parametrik budget: **52 passed** (`test_context_budget_properties.py`).
- Baseline fase sebelumnya: 1809 passed; tidak ada tes yang dilemahkan.
- `py_compile`: OK pada 9 modul yang diubah.
- mypy (`--follow-imports=silent --ignore-missing-imports`): **no issues**
  pada 9 modul yang diubah.
- Gate CI terpisah: `.github/workflows/crash-consistency.yml`.
