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
> `wth`, dipakai lewat `wth --bypass` atau `/bypass on`) memuat instruksi
> tambahan untuk model. Yang di sini adalah konvensi kerja repo, dan sengaja
> dibaca setiap sesi — jangan campurkan keduanya.

---

## 1. Apa repo ini

WTH-AI adalah CLI red team berbahasa Python untuk **authorized security testing**:
lab milik sendiri, CTF, target dengan izin tertulis, engagement red team dengan
Rules of Engagement.

Batas keras yang tidak boleh dilonggarkan oleh kode baru:

- Authorized target only. Tanpa `scope_card.json` yang valid (`authorized=true`,
  minimal satu CIDR/domain) tidak ada aksi.
- Recon non-destruktif: hanya GET/HEAD; Modbus hanya FC 1–4.
- Tidak ada brute force login, bypass OTP/captcha, atau eksfiltrasi data.
- Secret yang ditemukan hanya dicatat sebagai **candidate** yang dimasking;
  jangan divalidasi, jangan dipakai.
- Tidak ada dashboard/server, updater, license server, atau cloud agent.
- Tidak ada auto-fix PR.

---

## 2. Peta modul (yang dipakai sekarang)

```
wth-main.py                 entry point CLI (Click); semua perintah di sini
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
core/, reporting/ (root), executor/, agents/   legacy — jangan digabung ke wth_ai/
```

---

## 3. Invarian yang tidak boleh rusak

1. **Scope ditegakkan mesin.** `ScopeManager` + `EgressGuard`. Tidak ada flag
   untuk mematikannya; mode tidak memperluas scope.
2. **Kill switch ≤2 detik.** `wth kill` / `ctrl+k` menulis flag dan mengirim
   SIGTERM lalu SIGKILL ke process group.
3. **CONFIRMED hanya lewat `veracity`.** Butuh request/response tertangkap,
   minimal satu reproduksi, dan kontrol negatif yang berperilaku berbeda.
   Tanpa itu status maksimal `candidate`.
4. **Bundel bukti bisa diverifikasi ulang.** `wth verify` menghitung sha256 dari
   isi; mengubah satu byte harus membuat verifikasi gagal.
5. **Semua koneksi keluar lewat allowlist** dan tercatat (`audit.log`,
   `EGRESS_DENIED`). Proxy (`WTH_PROXY`) tidak memperluas scope.
6. **Tidak ada eksekusi shell.** `shell_runner`/argv-list saja; jangan pernah
   `shell=True`.
7. **Secret tidak pernah plaintext** di log, laporan, hasil, atau fixture.

---

## 4. Perintah yang harus tetap jalan

```bash
source .venv/bin/activate              # atau venv lain
pytest tests -q -p no:cacheprovider --timeout=120

wth scope --target H --cidr C --authorized [--spec openapi.json]
wth scan --offline-demo --out out       # deterministik, tanpa jaringan
wth scan -n --fail-on high --out out    # CI: exit 0/1/2
wth scan --scope-mode diff --diff-base origin/main
wth verify out/bundle
wth report --bundle out/bundle --format html|sarif|json|id|pdf
wth disclosure F-001 --bundle out/bundle
wth kb build --source DIR ; wth kb status ; wth kb search Q
wth ot plan --host H --block holding:0:10
wth ot read --host H --cidr C --confirm
wth visual cluster shots/ --out out/clusters.json
wth browser capture URL --out shots/
wth sandbox plan ... ; wth sandbox run --yes -- CMD
wth interactive --redteam --mode plan
wth kill
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

Sengaja **tidak** dibangun: dashboard/web server, auto-fix PR, cloud platform,
distributed worker, updater/license. Modbus RTU/serial belum ada. Token-level
agent resume belum ada (pakai `wth resume` / `wth job retry`).

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
| `prompts/agent/AGENT.md` | profil bypass bawaan (`wth --bypass`), bukan konvensi repo |
| `docs/archive/` | arsip perencanaan lama (bukan rujukan kerja) |

---

## 9. Bypass LLM (`--bypass` / `/bypass`)

Instruksi sesi untuk model tinggal di `prompts/agent/AGENT.md` dan/atau
`$WTH_HOME/bypass/<nama>.md` (override direktori: `WTH_BYPASS_DIR`). Semantik
yang harus dipertahankan:

- **Mati secara default.** `wth --bypass` (tanpa nilai) memakai profil `wth`;
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