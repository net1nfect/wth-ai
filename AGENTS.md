# AGENTS.md — konvensi kerja di repo WTH-AI

Dokumen ini untuk agen (dan manusia) yang mengubah kode di repo ini. Isinya
aturan yang sudah terbukti dipakai, bukan aspirasi. Kalau kode dan dokumen ini
bertentangan, kode menang — lalu perbaiki dokumen ini.

Untuk Claude Code, lihat `CLAUDE.md` (pointer ke file ini).

> Catatan: isi lama file ini adalah tempelan prompt Fase 5 (recon platform) yang
> sudah selesai dikerjakan. Prompt itu tidak lagi menjadi sumber kebenaran;
> arsipnya ada di riwayat git, rencana aktif ada di `.hermes/plans/`.
>
> **File ini bukan tempat instruksi sesi.** `prompts/agent/AGENT.md` (profil
> `wth-ai`, dipakai lewat `wth-ai --bypass` atau `/bypass on`) memuat instruksi
> tambahan untuk model. Yang di sini adalah konvensi kerja repo, dan sengaja
> dibaca setiap sesi — jangan campurkan keduanya.

---

## 1. Apa repo ini

WTH-AI adalah CLI red team berbahasa Python untuk **authorized security testing**:
lab milik sendiri, CTF, target dengan izin tertulis, engagement red team dengan
Rules of Engagement.

### 1.1 Batas keras (tidak pernah dilonggarkan)

- Authorized target only. Tanpa `scope_card.json` yang valid (`authorized=true`,
  minimal satu CIDR/domain, atau `kind=source` dengan path) tidak ada aksi.
- Recon non-destruktif: hanya GET/HEAD; Modbus hanya FC 1–4.
- Tidak ada brute force login, bypass OTP/captcha, atau eksfiltrasi data.
- Secret yang ditemukan hanya dicatat sebagai **candidate** yang dimasking;
  jangan divalidasi, jangan dipakai.
- Tidak ada eksekusi shell bebas (`shell=True`), tidak ada descriptor container
  yang menambah kapabilitas di luar `containment.SandboxSpec`.
- Setiap temuan tanpa request/response tertangkap tetap `candidate`. Fitur baru
  tidak boleh menulis status temuan di luar `verify_gate.apply_verification`.

### 1.2 Izin berbatas (dibuka 2026-11-07, default mati)

Enam hal berikut dulu dilarang mutlak. Pemilik repo membukanya untuk mengejar
paritas Strix. Yang dibuka adalah **izin berbatas**, bukan penghapusan kontrol:
masing-masing punya flag, default `false`, dan konsekuensi yang harus diterima.

1. **Dashboard lokal** (`wth-ai view`). Hanya bind `127.0.0.1`, hanya membaca
   bundel yang sudah ditulis, token acak per proses, tanpa rute mutasi, mati
   kecuali flag eksplisit. Tidak ada multi-user, TLS, atau bind `0.0.0.0`.
   Konsekuensi: menambah permukaan HTTP di mesin operator; jangan pernah
   dijalankan di host yang tidak dipercaya.
2. **Auto-fix PR.** Hanya mode `full_lab` + `--destructive`, hanya repo target
   yang tercantum di scope card, patch di worktree terpisah, wajib retest, dan
   **tidak pernah** `git push` otomatis. Konsekuensi: kode target bisa berubah;
   operator bertanggung jawab atas review patch.
3. **Cloud sync.** Opt-in dengan URL eksplisit yang harus lolos `EgressGuard`;
   tidak ada endpoint default; unggahan artefak target butuh flag terpisah.
   Konsekuensi: data engagement bisa keluar mesin — hanya untuk engagement yang
   RoE-nya mengizinkan.
4. **Updater.** Verifikasi tanda tangan + allowlist host sebelum memasang;
   verifikasi gagal berarti tolak, bukan fallback diam-diam. Konsekuensi:
   menambah jalur unduh eksternal; kunci publik harus dipin di repo.
5. **Telemetry.** Default mati; hanya agregat (`runs`, durasi, exit code,
   versi). Tidak pernah host, target, temuan, atau secret. Konsekuensi: tetap
   ada egress ke endpoint telemetry bila dinyalakan.
6. **Worker lokal.** Beberapa proses di satu mesin dengan antrean berbasis file
   dan `fcntl.flock`; tidak ada scheduler remote atau worker lintas jaringan.
   Konsekuensi: paralelisme terbatas pada satu host.

Aturan yang mengikat keenam izin ini: mati secara default, tercatat di
`audit.log` saat dipakai, dan tidak boleh menambah jalan pintas untuk status
temuan, scope, atau egress allowlist.

---

## 2. Peta modul (yang dipakai sekarang)

```
wth-ai-main.py              entry point CLI (Click); semua perintah di sini
wth_ai/
  modes.py                  7 mode agen; `auto` = superset, lainnya pecahan
  scope_manager.py          CIDR/host matcher (dipakai scope + egress)
  scope_card.py             rekaman izin yang ikut ke bundel bukti
  netguard.py               EgressGuard: allowlist keluar + audit EGRESS_DENIED
  containment.py            SandboxSpec -> argv docker (cap-drop, read-only)
  killswitch.py             SIGTERM/SIGKILL + flag $WTH_HOME/runtime/KILL
  redaction.py              redaksi secret kanonik (semua permukaan memakai ini)
  audit.py                  audit.log append-only
  redteam/
    models.py queue.py loop.py runner.py   mesin temuan
    classify.py                            gate veracity -> CONFIRMED
    bundle.py                              manifest sha256 + audit ber-rantai
    gate.py run_meta.py scan_modes.py artifacts_write.py
    diffscope.py                           scope khusus file PR
    disclosure.py                          advisory 90 hari
    novel/                                 labels, artifacts, prior_art,
                                           diffauth, impact
  kb/                       SQLite FTS5: db, build, cite (gagal keras), score
  ot/                       modbus.py (FC 1-4 + tolak write), client.py (TCP stdlib)
  targets/spec.py           OpenAPI/Postman sebagai target
  reporting/                html.py, id_report.py, pdf.py, sarif.py
  recon/                    http.py (fetch + EgressGuard + proxy), visual.py
                            (dhash PNG), browser.py (Playwright CLI)
  cli/tui/redteam/          TUI mode agen (app, banner, theme, skills/mcp view)
  skills/                   registry.py, select.py (auto-pilih <=5)
  tools/doctor.py           health check (termasuk lapisan red team)
```

Daftar di atas adalah peta **inti**, bukan daftar lengkap. Paket lain yang juga
jalur produksi (per 2026-11-07): `agents/` (planner, supervisor, subagent,
tool_loop), `llm/` (router, model_catalog, budget), `hunt/`, `jobs/`,
`gateway/` (daemon, background, rpc), `bots/`, `computer_use/`, `memory/`,
`eval/`, `research/`, `plugins/`, `cron/`, `graph/`, `policy/`, `rpc/`,
`mcp/`, `platform/`, `broker/`, `brain/`, `probe/`, `ctf/`, `intel/`,
`evidence/`, `terminal/`, `orchestrator.py`.

Kalau menambah paket baru, tambahkan satu baris di sini dengan alasan satu
kalimat — peta yang basi lebih berbahaya daripada tidak ada peta.

Pohon legacy di root (`core/`, `agents/`, `exploitation/`, `reconnaissance/`,
`learning/`, `integration/`, `reporting/`, `utils/`, `main.py`) sudah dihapus
(P3-4). `wth_ai/agents/*` dan `wth_ai/executor/vuln/*` adalah jalur produksi,
bukan turunannya.

---

## 3. Invarian yang tidak boleh rusak

1. **Scope ditegakkan mesin.** `ScopeManager` (`wth_ai/scope_manager.py:20`) +
   `EgressGuard` (`wth_ai/netguard.py:22`; dipasang di `wth_ai/recon/engine.py:99`
   dan `wth_ai/terminal/bridge.py:250`). Tidak ada flag untuk mematikannya; mode
   tidak memperluas scope.
2. **Kill switch ≤2 detik.** `KillSwitch.activate` (`wth_ai/killswitch.py:76`)
   menulis flag lalu SIGTERM ke process group, SIGKILL setelahnya.
3. **CONFIRMED hanya lewat `veracity`.** `veracity.assess_claim`
   (`wth_ai/veracity.py:75`) memutuskan; satu-satunya penulis status adalah
   `verify_gate.apply_verification` (`wth_ai/verify_gate.py:82`), yang dipakai
   `orchestrator._verify`, `supervisor_dispatch.verify_finding`, dan
   `loop/engagement_loop`. Butuh request/response tertangkap, minimal satu
   reproduksi, dan kontrol negatif yang berperilaku berbeda. Tanpa itu status
   maksimal `candidate`.
4. **Bundel bukti bisa diverifikasi ulang.** `bundle.verify_bundle`
   (`wth_ai/redteam/bundle.py:86`) menghitung sha256 dari isi; mengubah satu
   byte harus membuat verifikasi gagal.
5. **Semua koneksi keluar lewat allowlist** dan tercatat (`audit.log`,
   `EGRESS_DENIED`). Ditegakkan `EgressGuard.check` (`wth_ai/netguard.py`);
   proxy (`WTH_PROXY`) tidak memperluas scope.
6. **Tidak ada eksekusi shell.** `shell_runner.run_argv`
   (`wth_ai/shell_runner.py:37`) hanya menerima argv-list; jangan pernah
   `shell=True`.
7. **Secret tidak pernah plaintext** di log, laporan, hasil, atau fixture.
   Satu redaktor: `redaction.redact_secrets` / `redact_data`
   (`wth_ai/redaction.py:102,129`), dipanggil di chokepoint
   `redteam/artifacts_write.write_findings`, `redteam/bundle.write_bundle`, dan
   `evidence/vault.py`.

---

## 4. Perintah yang harus tetap jalan

```bash
source .venv/bin/activate              # atau venv lain
pytest tests -q -p no:cacheprovider --timeout=120

wth-ai scope --target H --cidr C --authorized [--spec openapi.json]
wth-ai scan --offline-demo --out out       # deterministik, tanpa jaringan
wth-ai scan -n --fail-on high --out out    # CI: exit 0/1/2
wth-ai scan --scope-mode diff --diff-base origin/main
wth-ai verify out/bundle
wth-ai report --bundle out/bundle --format html|sarif|json|id|pdf
wth-ai disclosure F-001 --bundle out/bundle
wth-ai kb build --source DIR ; wth-ai kb status ; wth-ai kb search Q
wth-ai ot plan --host H --block holding:0:10
wth-ai ot read --host H --cidr C --confirm
wth-ai visual cluster shots/ --out out/clusters.json
wth-ai browser capture URL --out shots/
wth-ai sandbox plan ... ; wth-ai sandbox run --yes -- CMD
wth-ai interactive --redteam --mode plan
wth-ai kill
```

Artefak scan: `out/{run.json,findings.sarif,vulnerabilities/,report.*,bundle/}`.
`bundle/` berisi `manifest.json`, `audit.jsonl`, `findings.json`,
`scope_card.json`, `root_hash.txt`.

---

## 5. Konvensi kode

- Komentar dan pesan pengguna: **Bahasa Indonesia**; identifier tetap Inggris.
- Komentar menjelaskan **mengapa**, bukan mengulang apa yang baris itu lakukan.
  Jangan menambah komentar dekoratif ("# import module").
- Setiap fitur baru disertai test yang **gagal sebelum** implementasi. Test
  tidak boleh bergantung internet: pakai fixture, server palsu, atau monkeypatch.
- Jangan menulis "sudah berfungsi" tanpa output eksekusi. Sertakan perintah dan
  hasilnya di ringkasan.
- Path dari operator (`--out`, `--bundle`, `--target-list`) harus lewat
  `_user_path()` — entry point melakukan `os.chdir(repo)`.
- Penulis file baru (log, engagement, report) harus menghormati env
  `WTH_HOME` / `WTH_ENGAGEMENT_DIR` / `WTH_LOG_DIR` / `WTH_REPORT_DIR`
  supaya `pytest` tidak mengotori repo.
- Dependency baru harus dipertimbangkan keras: PDF, hash gambar, dan Modbus
  sengaja ditulis dengan stdlib. Kalau butuh dependency, tambahkan ke
  `requirements.txt` **dan** jelaskan alasannya di commit.
- Jangan menyentuh `data/engagements/`, `logs/`, `reports/` yang dilacak git.

---

## 6. Alur kerja agen (konstitusi)

### 6.1 Scope law
Tidak ada pengujian aktif tanpa `authorized=yes` di scope. Jangan sentuh host di
luar in-scope. Kalau izin tidak jelas: berhenti dan tanya.

### 6.2 Evidence law (TTST)
Klaim kerentanan wajib punya rujukan bukti (`[TX:...]` / request-response di
bundel). Tanpa bukti, tulis `HIPOTESIS (belum dibuktikan):`, bukan VERIFIED /
FOUND / CRITICAL / EXPLOITED.

### 6.3 Graph law
Jangan mengarang URL/API yang belum pernah di-recon atau diprobe. Endpoint yang
baru ditemukan ditandai `DISCOVERED_TX`.

### 6.4 Permission law
Hormati mode (`read_only` | `standard` | `elevated` | `full_lab`), dan
`Cap.MUTATE` tetap butuh `--destructive` di level proses. Jangan bypass
`PermissionManager` atau gating mode.

### 6.5 7-question gate sebelum menulis laporan
1. In scope? 2. Permukaan nyata? 3. Dampak bisa ditunjukkan? 4. Reproducible?
5. Bukan self-inflicted? 6. Bukan teori? 7. Triager akan menerima?

Prioritas kelas: auth bypass > privesc > IDOR/BOLA > SSRF > XSS > misconfig >
info leak.

---

## 7. Status & hal yang sengaja tidak dibangun

Sudah jalan (dengan bukti test): mode agen + TUI, bundel bukti + verify, gate
veracity, SARIF/HTML/ID/PDF, diff-scope, spec OpenAPI/Postman, KB CVE dengan
validator sitasi, novel-finding labels + prior-art, disclosure pack, Modbus
read-only (TCP), Playwright adapter, proxy, sandbox plan/run, visual clustering.

Sengaja **tidak** dibangun (masih): Modbus RTU/serial, token-level agent
resume. Token-level resume diganti `wth-ai resume` / `wth-ai job retry`.

Dibuka sebagai izin berbatas (§1.2) dan **belum diimplementasikan** per
2026-11-07: dashboard lokal read-only, auto-fix PR, cloud sync, updater,
telemetry, worker lokal. Rencana port ada di
`docs/superpowers/plans/2026-11-07-port-strix.md`. Jangan anggap izin sebagai
fitur yang sudah ada.

Rencana kerja & status per item: `.hermes/plans/` (bukan bagian produk).

---

## 8. Di mana dokumennya

| Dokumen | Isi |
|---|---|
| `README.md` | gambaran produk, instalasi, referensi cepat |
| `llms.txt` | ringkasan mesin-baca: perintah, artefak, exit code |
| `docs/EVIDENCE.md` | cara membaca & memverifikasi bundel bukti |
| `docs/DEMO.md` | urutan demo 5 menit (offline, deterministik) |
| `docs/MODES.md` | matriks mode × kapabilitas (di-generate dari kode) |
| `docs/COMPARISON.md` | perbandingan jujur dengan Strix (hanya yang punya bukti) |
| `docs/AIRGAP.md` | pemasangan tertutup, LLM lokal, KB offline |
| `prompts/agent/AGENT.md` | profil bypass bawaan (`wth-ai --bypass`), bukan konvensi repo |
| `docs/archive/` | arsip perencanaan lama (bukan rujukan kerja) |

---

## 9. Bypass LLM (`--bypass` / `/bypass`)

Instruksi sesi untuk model tinggal di `prompts/agent/AGENT.md` dan/atau
`$WTH_HOME/bypass/<nama>.md` (override direktori: `WTH_BYPASS_DIR`). Semantik
yang harus dipertahankan:

- **Mati secara default.** `wth-ai --bypass` (tanpa nilai) memakai profil `wth-ai`;
  `--bypass <nama>` memakai profil bernama; tanpa flag tidak ada blok apa pun.
- **Label di meta.** Balasan chat/supervisor menambahkan `· bypass: <profil>`.
  Jangan hapus: transkrip harus jujur soal konteks sesi.
- **Tidak menimpa invarian.** Prompt bypass boleh mengubah gaya dan kelengkapan,
  tidak boleh melonggarkan scope, egress allowlist, kill switch, atau gate
  bukti. Test `tests/test_bypass.py` menjaga empat kata kunci itu tetap ada.
- **Terpisah dari AGENTS.md.** Jika satu sesi butuh bypass, itu pilihan
  operator; kalau ia ditempel ke konstitusi repo, semua sesi mendapatkannya
  tanpa diminta.
- Status bukan preferensi: `agent.bypass` yang kosong saat disimpan tidak
  menghidupkan kembali nilai lama di disk (lihat `save_user_config`).