# Konstitusi WTH-AI

Sumber utama: `AGENTS.md`. Dokumen ini merangkum prinsip yang **tidak bisa ditawar**
saat menulis spec, plan, task, atau implementasi. Kalau konstitusi dan kode
bertentangan, kode menang — lalu keduanya diperbaiki.

## Core Principles

### I. Authorized Testing Only (NON-NEGOTIABLE)

Tidak ada aksi aktif tanpa scope yang sah. Scope card wajib `authorized=true`
dan minimal satu CIDR/domain, atau `kind=source` dengan path yang ada. Tanpa itu
tidak ada request, scan, probe, maupun replay.

Penegakannya di mesin, bukan di prompt: `wth_ai/scope_manager.py:20` (`ScopeManager`)
memutuskan in-scope/out-of-scope, `wth_ai/netguard.py:22` (`EgressGuard.check`)
menolak dan mencatat `EGRESS_DENIED` di `audit.log`. Tidak ada flag untuk
mematikan keduanya; mode agen tidak memperluas scope; proxy tidak memperluas
allowlist. Fitur baru yang butuh jaringan wajib lewat `EgressGuard`.

### II. Non-Destruktif Secara Default

Recon hanya GET/HEAD. Modbus hanya FC 1–4; fungsi tulis (5/6/15/16) ditolak.
Tidak ada brute force login, bypass OTP/captcha, atau eksfiltrasi data.

Secret yang ditemukan hanya dicatat sebagai **candidate** yang dimasking: tidak
divalidasi, tidak dipakai, tidak dikirim keluar. Setiap aksi yang mengubah state
target hanya boleh ada di balik flag eksplisit (`--destructive`, mode `full_lab`)
dan wajib tercatat di `audit.log`.

### III. CONFIRMED Hanya Lewat Gate Veracity

Status temuan bukan opini. `wth_ai/veracity.py:75` (`veracity.assess_claim`)
memutuskan; satu-satunya penulis status adalah
`wth_ai/verify_gate.py:98` (`verify_gate.apply_verification`), yang dipakai
`orchestrator._verify`, `supervisor_dispatch.verify_finding`, dan
`loop/engagement_loop`. Jalur red team memakai `wth_ai/redteam/classify.py`
dengan aturan yang sama.

Syarat `CONFIRMED`: request/response tertangkap, minimal satu reproduksi, dan
kontrol negatif yang berperilaku berbeda. Tanpa ketiganya status maksimal
`candidate`. Discovery tidak boleh mengonfirmasi temuannya sendiri. Temuan
statis (SAST) selalu masuk sebagai `candidate` dengan bukti snippet.

### IV. Tidak Ada Shell Bebas, Tidak Ada Secret Plaintext

`shell=True` dilarang di seluruh repo. Eksekusi hanya lewat argv-list:
`wth_ai/shell_runner.py:37` (`run_argv`). Kontainer hanya dibentuk oleh
`containment.SandboxSpec` — tidak ada descriptor yang menambah kapabilitas di
luarnya.

Secret tidak pernah plaintext di log, laporan, hasil, jurnal, atau fixture. Satu
redaktor: `wth_ai/redaction.py:102` (`redact_secrets`) dan `:147`
(`redact_data`), dipanggil di chokepoint `redteam/artifacts_write.write_findings`,
`redteam/bundle.write_bundle`, `evidence/vault.py`, dan `proxy/local.py`.

`KillSwitch.activate` (`wth_ai/killswitch.py:76`) harus tetap ≤2 detik: flag
`$WTH_HOME/runtime/KILL`, SIGTERM ke process group, lalu SIGKILL.

### V. Test-First, Offline, Deterministik

Setiap fitur dan perbaikan dimulai dari test yang **gagal dulu** (RED), lalu
implementasi, lalu GREEN. Test tidak boleh bergantung internet atau LLM nyata:
pakai fixture, server palsu di thread, atau monkeypatch. Jalankan:

```bash
source .venv/bin/activate
pytest tests -q -p no:cacheprovider --timeout=120
```

Test yang butuh jaringan nyata bukan test. Test yang menyentuh disk wajib
menghormati `WTH_HOME` / `WTH_ENGAGEMENT_DIR` / `WTH_LOG_DIR` / `WTH_REPORT_DIR`
supaya `pytest` tidak mengotori repo.

### VI. Tidak Ada Klaim Tanpa Output Eksekusi

Dilarang menulis "sudah berfungsi", "sudah aman", atau "sudah selesai" tanpa
perintah dan keluarannya. Ringkasan wajib memuat perintah yang dijalankan dan
hasilnya (jumlah test, exit code, baris output kunci). Klaim bahwa sesuatu
"sengaja tidak dibangun" juga harus jujur: sebut apa yang belum ada, bukan
menyamarkannya sebagai fitur.

Klaim kerentanan mengikuti Evidence Law: wajib rujukan bukti (`[TX:...]` atau
request-response di bundel). Tanpa bukti, tulis `HIPOTESIS (belum dibuktikan):`.

### VII. Batas Permukaan: Izin Berbatas, Default Mati

Enam permukaan berikut **ada** sebagai izin berbatas (AGENTS.md §1.2) dan
selalu default `false`: dashboard lokal (`wth_ai/view/`), auto-fix PR
(`wth_ai/autofix/`), cloud sync, updater (`wth_ai/update/`), telemetry
(`wth_ai/telemetry/`), worker lokal (`wth_ai/jobs/local_queue.py`). Masing-masing
punya flag, menulis `audit.log` saat dipakai, dan tidak boleh menambah jalan
pintas untuk scope, status temuan, atau egress allowlist.

Yang tetap **bukan** goal, dalam bentuk apa pun: bind selain `127.0.0.1` untuk
`view`, `git push`/commit otomatis dari `autofix`, endpoint telemetry default,
worker lintas host, Modbus RTU/serial, dan auto-download pada updater. Menambah
salah satunya adalah perubahan konstitusi, bukan task biasa.

## Additional Constraints

- **Dependency**: stdlib diutamakan. Dependency baru harus dipertimbangkan keras,
  ditambahkan ke `requirements.txt` **dan** `pyproject.toml`, serta dijelaskan
  alasannya di commit.
- **Path operator**: `--out`, `--bundle`, `--target-list` wajib lewat
  `_user_path()` karena entry point melakukan `os.chdir(repo)`.
- **Dua launcher**: `wth-ai-main.py` (repo) dan `wth_ai/cli/wth-ai-main.py`
  (paket) harus mengekspos perintah yang sama; ada test paritas untuk itu.
- **Bahasa**: komentar dan pesan pengguna Bahasa Indonesia; identifier Inggris.
  Komentar menjelaskan **mengapa**, bukan mengulang baris.
- **Peta modul** di AGENTS.md §2 harus diperbarui setiap menambah paket baru —
  peta basi lebih berbahaya daripada tidak ada peta.

## Development Workflow

1. **Spec** dulu (`/speckit.specify`): masalah, batasan, kriteria penerimaan.
2. **Plan** (`/speckit.plan`): modul tersentuh, interface, risiko, rollback.
3. **Tasks** (`/speckit.tasks`): satu task = satu work unit yang bisa direview.
4. **Implement** test-first, commit per work unit dengan path eksplisit
   (`git add <path>`, bukan `git add -A`, karena working tree bisa membawa WIP
   yang bukan milik sesi itu).
5. **Review** per slice (~400 baris authored per slice); check invarian §I–§IV
   sebelum menyatakan selesai.

Gate wajib sebelum menulis laporan temuan: in scope? permukaan nyata? dampak bisa
ditunjukkan? reproducible? bukan self-inflicted? bukan teori? triager akan
menerima?

## Governance

`AGENTS.md` adalah sumber utama konstitusi ini; konstitusi tidak boleh
melonggarkannya. Amandemen wajib menyebut prinsip yang berubah, alasannya, dan
dampaknya ke test yang menjaga prinsip tersebut (`tests/test_bypass.py`,
`tests/test_scope_*.py`, `tests/test_verify_gate.py`, `tests/test_artifact_redaction.py`).
Melonggarkan prinsip I, III, atau IV bukan amandemen — itu penggantian produk.

Setiap PR/review wajib memverifikasi kepatuhan: tidak ada `shell=True`, tidak ada
jalur jaringan di luar `EgressGuard`, tidak ada penulis status di luar
`apply_verification`, tidak ada secret plaintext, dan setiap klaim disertai
output eksekusi.

**Version**: 1.0.0 | **Ratified**: 2026-11-08 | **Last Amended**: 2026-11-08
