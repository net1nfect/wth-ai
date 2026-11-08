# PLAN — perbaikan WTH-AI

Status: **rencana, belum ada perbaikan kode yang diterapkan.**
Tanggal: 2026-11-05 · basis commit `2cc4c7e` (branch `big-dix`).
Revisi 2: digabung dengan pembacaan graph `codebase-memory-mcp` (8.411 node /
33.451 edge, generasi `2026-11-05T00:54:26Z`, 4 pembaca subsistem + 1 verifier
adversarial) dan 4 keputusan operator di §0.

Detail per temuan:

- `.hermes/plans/2026-11-05-audit-menyeluruh.md` — audit perilaku (25 temuan)
- `.hermes/plans/2026-11-05-inkonsistensi.md` — drift UI/REPL/TUI & dokumen

Keduanya tidak di-commit (`.hermes/` di-gitignore). `PLAN.md` ini yang
di-commit supaya jalannya pekerjaan terlihat dari repo.

---

## 0. Keputusan yang mengikat rencana ini

| # | Pertanyaan | Keputusan |
|---|---|---|
| D1 | `engage start` membuka 3 dimensi sekaligus | **Perbaiki sebagai P0** (P0-4). `--authorized` wajib eksplisit; `/permission unrestricted` otomatis dihapus; profil tidak lagi menurunkan `require_authorized` |
| D2 | `FindingState.VERIFIED` dari verdict LLM | **VERIFIED wajib lewat veracity** (P0-5). Tanpa bukti maksimal `CANDIDATE`; `finding_state_transition_allowed` disambungkan |
| D3 | Wheel tidak fungsional | **Source-only** (P1-8). Hapus `[project.scripts]` dari wheel, hentikan build wheel, installer tetap `pip install -e .` |
| D4 | Pohon legacy di root | **Hapus** (P3-4) beserta 4 test yang mengujinya |
| D5 | Nama perintah `wth` vs `wth-ai` | **`wth-ai` kanonik, `wth` dihapus** (tanpa alias). Operator membalik rekomendasi awal |

---

## 1. Ringkasan

| Severity | Jumlah | Inti |
|---|---|---|
| **P0** | 5 | scope & egress tidak berlaku di jalur recon; bundel menulis otorisasi palsu; `full_lab` → `unrestricted`; `engage start` membuka semua pengaman; `VERIFIED` tanpa bukti |
| **P1** | 9 | secret tidak diredaksi di artefak temuan; status LLM bohong; config rusak → traceback; isolasi `WTH_HOME` bocor; tulis non-atomik; picker crash; parser scope salah; suite tak jalan di instalasi bersih; wheel tak bisa dipakai |
| **P2** | 10 | model/angka/status ganda; TUI; dokumen vs kode; `cli_history` ter-track; `write_env_file`; **P2-10 ketidakkonsistenan `wth` vs `wth-ai`** |
| **P3** | 5 | import tak terpakai; modul 0% coverage; kode mati; legacy tree; naive datetime |

Baseline yang diukur (bukan asumsi):

```
pytest tests (tanpa tests/research)  1197 passed, 31 deselected
pytest tests --cov=wth_ai            66% (0%: bots/*, broker/*, cron/ticker, ssh backend, slash_commands)
pyflakes wth_ai                      114 temuan (98 import tak terpakai)
AST shell=True                       1 call site: wth_ai/tools/installer.py:66
wth --help                           40 perintah (README mencantumkan 19)
```

Diperiksa dan **bersih**: tidak ada `except:` telanjang; exit code CI
(`scan -n --fail-on high` = 2, `kb search` kosong = 1, `verify` tamper = 1);
logika kill switch (SIGTERM → grace 1 s → SIGKILL); Modbus FC 1–4 ditegakkan
keras (`ot/modbus.py`: `build_write_request` selalu menaikkan
`DestructiveBlocked`); veracity tersambung di `classify`/chat/research;
tautan dokumen hidup; installer tanpa sudo.

### Yang berubah dari revisi 1

Review graph menemukan **dua P0 yang tidak ada di audit sebelumnya** dan satu
P1 (redaksi). Ketiganya sudah diverifikasi ke sumber:

- `engage_start` (`wth-main.py:1253-1267`) — `--authorized` default `True`,
  lalu `set_unrestricted(True)` + `/permission unrestricted`; profil
  `bug-bounty`/`ctf-aggressive` (`profiles.py:93,101`) juga
  `require_authorized: False`.
- `VERIFIED` dari LLM — `orchestrator.py:1285`, `supervisor_dispatch.py:400`,
  `engagement_loop.py:142`; `models.py:115 finding_state_transition_allowed`
  nol pemanggil.
- Redaksi — `grep -rn redact wth_ai/redteam/ wth_ai/reporting/` → **kosong**;
  `loop.py:73` menambahkan `outcome.error` mentah ke `finding.evidence`, lalu
  `artifacts_write.py`/`bundle.py` menuliskannya apa adanya.

---

## 2. P0 — invarian produk (Fase 0, rilis sendiri)

### [x] P0-1. Recon berjalan tanpa scope card dan tanpa allowlist egress

**Bukti.** `wth recon run example.com` pada `WTH_HOME` kosong menyelesaikan job
(`http_probes: 1`) tanpa `scope_card.json`. `cli/recon_commands.py:48` menulis
`{"authorized": True}` hardcoded; `jobs/handlers.py:290` memanggil
`set_scope(target=…, authorized=True, in_scope=[target])`; `recon/engine.py:78`
default `authorized: bool = True`; `recon/http.py:26 set_egress_guard()` hanya
dipanggil test (`tests/test_recon_egress.py`, `tests/test_recon_proxy.py`),
sehingga `_EGRESS_GUARD` selalu `None`. Jalur agen
(`terminal/bridge.py:250-254`) memang menegakkan keduanya — hanya recon/job
yang tidak.

**Perbaikan.**
- [x] `ReconEngine(..., scope: ScopeManager, guard: EgressGuard)` — wajib, bukan opsional dengan default aman
- [x] `ReconEngine.run()` memanggil `scope.is_in_scope(target)` sebelum langkah plan; di luar scope → `status="blocked"`, exit 2
- [x] `jobs/handlers.py` + `recon_commands.py` membaca `scope_card.json` lewat `ScopeCard.load`; tanpa kartu → job berhenti sebelum engine dibuat
- [x] `rhttp.set_egress_guard(guard)` di awal run, `None` di `finally`
- [x] hapus default `authorized=True` di `ReconEngine`

**Gate.** `tests/test_recon_scope_gate.py` (baru): (a) `WTH_HOME` kosong →
exit 2 dan **nol** panggilan `requests` (monkeypatch); (b) kartu CIDR sempit →
host di luar CIDR diblokir; (c) guard benar-benar terpasang selama run dan
bersih sesudahnya.

### [x] P0-2. Bundel bukti menulis otorisasi yang tidak pernah ada

**Bukti.** `wth-main.py:396-399` menulis `scope_card={"target": "fixture",
"authorized": True, …}` sebagai literal ke `write_bundle`. `grep -n scope_card
wth_ai/redteam/{gate,classify}.py` → kosong. `Finding.from_dict`
(`redteam/models.py`) juga menerima `"state": "confirmed"` dari JSON mana pun
yang dibaca `wth report`/`wth disclosure`.

**Perbaikan.**
- [x] `scan_cmd` membaca kartu sungguhan; tanpa kartu → `authorized:false,
      demo:true`; dengan kartu → salin `target`/`cidr`/`domains`
- [x] `classify` + `gate` menolak `CONFIRMED` tanpa kartu `authorized:true`
- [x] `Finding.from_dict` memvalidasi `state` terhadap `AttemptState`, bukan
      menerima string bebas

**Gate.** `tests/test_bundle_scope_truth.py` (baru): scan tanpa kartu →
bundel `authorized:false` dan nol `CONFIRMED`; dengan kartu → isi bundel sama
dengan kartu, bukan `"fixture"`.

### [x] P0-3. `full_lab` menjadi `unrestricted`, default tidak dipakai

**Bukti (diverifikasi).** `permission_presets.py:82` memetakan
`"full_lab": "yolo"`, dan `Preset("yolo", UNRESTRICTED, unrestricted=True,
auto_approve=True)`. `config.py:111` `SafetyConfig.default_permission="full_lab"`
dan `config.py:452` memakai nilai itu sebagai fallback. `grep -rn
default_permission wth_ai/` → hanya ditulis wizard/profil, tidak pernah dibaca
saat `SessionState()` dibuat (`models.py:550` default `READ_ONLY_RECON`).

**Perbaikan.**
- [x] buat preset `full-lab` (`PermissionMode.FULL_LAB`, `unrestricted=False`,
      `auto_approve=False`), hapus alias `full_lab`/`full-lab` → `yolo`
- [x] satu sumber default: `SessionState` diinisialisasi dari preset config
- [x] default aman: `SafetyConfig.default_permission = "read_only"`
- [x] migrasi sekali-jalan untuk config lama ber-`default_permission: full_lab`

**Gate.** `tests/test_permission_presets.py`: tabel alias → (`permission`,
`unrestricted`, `auto_approve`) termasuk `full_lab`; `unrestricted=False`
menolak `PermissionMode.UNRESTRICTED`.

### [x] P0-4. `engage start` membuka ketiga dimensi pengaman sekaligus

**Bukti (diverifikasi).** `wth-main.py:1253-1267`:
`@click.option("--authorized/--no-authorized", default=True)` lalu
`set_unrestricted(True)` dan `orch.run_once("/permission unrestricted")`.
`profiles.py:93,101` menyetel `require_authorized: False` untuk `bug-bounty`
dan `ctf-aggressive`; `engagement/mode.py:81` membaca nilai itu.

**Perbaikan.**
- [x] `--authorized` tanpa default; wajib eksplisit (`--authorized` /
      `--no-authorized`), absen → `UsageError` exit 2
- [x] hapus `set_unrestricted(True)` dan `/permission unrestricted`
      otomatis; operator memintanya sendiri bila perlu
- [x] `require_authorized: False` dihapus dari kedua profil (dimensi 1 hanya
      diturunkan lewat `WTH_REQUIRE_AUTHORIZED` eksplisit)
- [x] permintaan izin ditulis ke `audit.log` (event `engage.started` dengan
      target + tanda tangan flag)

**Gate.** `tests/test_engage_start.py` (baru): tanpa `--authorized` → exit 2,
tidak ada job; dengan `--authorized` → `require_authorized` tetap True;
`--unrestricted` tidak lagi muncul sebagai efek samping.

### [x] P0-5. `FindingState.VERIFIED` di-set dari verdict LLM

**Bukti (diverifikasi).** Tiga penulisan state tanpa gate:
`orchestrator.py:1285`, `supervisor_dispatch.py:400`,
`engagement_loop.py:142`, semuanya dari `v["verdict"] == "verified"` hasil
`VerifierAgent._parse_verification` (`agents/verifier.py:98`, regex atas teks
model). Guard `finding_state_transition_allowed` (`models.py:115`) **nol
pemanggil**; `FindingsDB.update_finding_state` (`findings_db.py:56-67`)
menetapkan `finding.state` tanpa syarat. Grounding firewall tidak menahan
promosi: `verifier.py:28` hanya menggating bila state **sudah** `VERIFIED`.

**Perbaikan.**
- [x] `VerifierAgent.verify` memanggil `veracity.assess_claim` dengan
      `has_request_response`/`reproductions`/`controls` dari evidence vault;
      verdict LLM hanya boleh menurunkan, bukan menaikkan, status
- [x] `FindingsDB.update_finding_state` memanggil
      `finding_state_transition_allowed`; transisi ilegal → `InvalidTransition`
- [x] `orchestrator._verify` + `verify_finding` memakai satu fungsi bersama,
      bukan tiga salinan logika
- [x] `VERIFIED` tanpa bukti → maksimal `CANDIDATE`

**Gate.** `tests/test_verify_gate.py` (baru): verdict `"verified"` tanpa bukti
→ `CANDIDATE`; dengan request/response + reproduksi + kontrol → `VERIFIED`;
`VERIFIED → HYPOTHESIS` ditolak `FindingsDB`.

---

## 3. P1 — kebenaran status, kerahasiaan, ketahanan (Fase 1)

### [x] P1-0. Secret bocor ke artefak temuan dan bundel (baru)

**Bukti.** `grep -rn "redact" wth_ai/redteam/ wth_ai/reporting/` → kosong.
`redteam/loop.py:72-73` menambahkan `outcome.error` mentah ke
`finding.evidence`; `artifacts_write.write_findings` menulis `evidence` +
`attestation` ke `vulnerabilities/*.md|json|csv`; `bundle.write_bundle` menulis
`findings.json` — tanpa lewat `wth_ai.redaction`. Ini melanggar AGENTS.md §3(7).

**Perbaikan.**
- [x] satu chokepoint: `write_findings` dan `write_bundle` melewati redaktor
      yang sama; jangan redaksi per-pemanggil
- [x] `EvidenceVault.store` meredaksi rekursif (saat ini hanya
      `if isinstance(v, str)` di top-level, `evidence/vault.py:64`)
- [x] samakan tiga implementasi lokal (`rpc/execute_server.py:20`,
      `evidence/vault.py:36`, `skills/autowrite.py:42`) ke
      `wth_ai/redaction.py`

**Gate.** `tests/test_artifact_redaction.py` (baru): finding dengan
`evidence=["Authorization: Bearer sk-…"]` → tidak ada string rahasia di
`vulnerabilities/*`, `findings.json`, atau `bundle/`; header bersarang di
`EvidenceVault.store` juga termask.

### Item lain

| # | Item | Bukti singkat | Gate |
|---|---|---|---|
| [x] P1-1 | `llm online` tanpa provider | `available()` True pada config tanpa server (`llm/router.py:188-199`) | `llm_status()` 4 state; test tanpa key → `blocked` |
| [x] P1-2 | config rusak → traceback | `user.yaml: [unclosed` → `wth doctor` crash (`config.py:309-314`) | pesan `path:line`, exit 2; `doctor` tetap jalan |
| [x] P1-3 | sesi bocor ke repo | 1 run suite = +44 file `data/sessions/`; home baru me-resume sesi repo | dua `WTH_HOME` tidak berbagi `latest.json` |
| [x] P1-4 | tulis non-atomik | `session_store.py:27`, `config.py:605` | tmp+fsync+`os.replace`; test interrupt |
| [x] P1-5 | picker crash | `NameError: name 'window'` (`picker.py:303,308`) | binding PageUp/PageDown diuji, index berubah |
| [x] P1-6 | parser scope salah | `/target host authorized` → target `'host authorized'` | matriks input → target ternormalisasi |
| [x] P1-7 | suite tak jalan bersih | 3 modul TUI butuh `textual`; `test_integration` butuh `aiohttp` | `--collect-only` 0 error di venv minimal |

Catatan P1-7: `aiohttp` ternyata tidak di-import kode mana pun — entri itu
dibuang dari `pyproject.toml` dan `wth_ai/install.py` (lihat P3-4). Tes
instalasi juga tidak lagi menyentuh jaringan: `scripts/install.sh` mendapat
`--skip-deps`, dan tiga tes yang subjeknya blok PATH / perbaikan venv memakainya.
`tests/test_install.py` turun dari timeout 120 dtk menjadi **37 passed in 21,62s**.
| [x] P1-8 | wheel tak fungsional | `wth --version` gagal; wheel tanpa `prompts/`+`config/` | **D3**: source-only — `[project.scripts]` dihapus dari wheel, `pip wheel` tidak dipublikasikan; README menulis "pasang dari sumber" |

Catatan P1-8 (D3): `scripts/install.sh` memasang dengan `pip install -e .` dan
menulis symlink `~/.local/bin/wth → $VENV/bin/wth` (baris 561-580), jadi
instalasi sumber tidak bergantung pada `[project.scripts]`. `entry.py` sudah
gagal dengan pesan yang benar untuk wheel; itu dipertahankan, bukan diperbaiki.

---

## 4. P2 — satu jawaban per pertanyaan (Fase 2)

- [x] P2-1 model dari 3 sumber (`banner.py:51`, `repl.py:273`, TUI tidak ada)
- [x] P2-2 status berulang saat start (`repl.py:684-690` + `status_fn`)
- [x] P2-3 `cov 0%`/`ctx 0` dibaca sebagai pengukuran
- [x] P2-4 empat angka tool (17/346, 12/215, 23 callable, 104 tanpa installer)
- [x] P2-5 TUI: palet hardcoded, `ctrl+k` kill vs skills, tanpa model, 3 glyph bar
- [x] P2-6 README: tabel permission ≠ preset; 19 vs 40 perintah; `wth` vs `wth-ai`
  - Tabel permission sekarang memakai nama preset nyata + kolom konfirmasi;
    `elevated` tidak lagi diklaim butuh `/approve`. Heading tabel perintah
    menyebut 40 top-level / 152 leaf dan 58 slash command. §Jalankan memakai
    satu nama. D5 kemudian ditetapkan: `wth-ai` kanonik, alias `wth` dihapus
    (lihat P2-10).
- [x] P2-7 `data/cli_history` ter-track; `.coverage` tak di-ignore
- [x] P2-8 `write_env_file()` mati tapi menulis secret plaintext (`config.py:610`) — hapus
- [x] P2-9 `shell=True` di `tools/installer.py:66` vs invarian; `doctor` hanya cek satu method (`doctor.py:506-512`) — pisahkan jadi argv, atau dokumentasikan pengecualian + buat `doctor` memindai seluruh `wth_ai/` via AST dan gagal bila ada call site di luar allowlist
  - `normalize_install_command` kini mengembalikan argv untuk semua metode kecuali
    `scoop` (skrip PowerShell di Windows); 437 perintah install di manifest
    dipindai: nol metakarakter shell.
  - `doctor.unallowlisted_shell_sites()` memindai seluruh pohon paket via AST
    dengan `SHELL_ALLOWLIST = {"tools/installer.py:_run_command"}`;
    `check_platform()["no_shell"]` gagal bila ada call site baru di luar itu.

Gate: `scripts/gen_modes_docs.py --check`,
`tests/test_docs_generated.py`, `tests/test_readme_permissions.py`,
`tests/test_tui_consistency.py` (tidak ada hex literal di luar `theme.py`).

`gen_cli_docs.py` dibatalkan: tabel perintah di README adalah sampel terkurasi
(lihat P2-6), bukan proyeksi 40/152 perintah, jadi tidak ada sumber tunggal
untuk di-generate. Yang dikunci `tests/test_readme_permissions.py` adalah
jumlah dan tabel permission, bukan isi tabel perintah.

### [x] P2-10. `wth` dan `wth-ai` tidak konsisten di seluruh permukaan

**Bukti (diverifikasi ke sumber).** Keduanya menunjuk entry point yang sama
(`pyproject.toml [project.scripts]`: `wth` dan `wth-ai` → `wth_ai.cli.entry:main`),
jadi **bukan bug perilaku perintah** — yang tidak konsisten adalah siapa nama
resminya di tiap permukaan:

| Permukaan | Nama yang dipakai | Bukti |
|---|---|---|
| `pyproject.toml` | `wth` **dan** `wth-ai` | `[project.scripts]` dua entri |
| installer (venv) | `wth` (sumber), keduanya di-symlink | `scripts/install.sh:561`, loop `for name in wth wth-ai` baris 574-581 |
| README (§Instalasi, §Jalankan) | `wth` untuk setup, **`wth-ai`** untuk run | `README.md:238`, `324-327`, `348` |
| README (§Struktur, §Perintah) | `wth` | `README.md:475-493`, `552` |
| AGENTS.md | `wth` saja | `AGENTS.md:96-115` |
| Dockerfile | `wth-ai` | `Dockerfile` `CMD ["wth-ai", "worker", ...]` |
| docker-compose | `wth-ai` | `docker-compose.yaml:4`, `19` |
| docs/DOCKER.md | `wth-ai` | `docs/DOCKER.md:9,18` |
| npm `@wth-ai/cli` | bin hanya **`wth`** | `packages/wth-cli/package.json` `"bin": {"wth": ...}` |
| `--version` (npm) | mencetak `wth-cli v1.0.0` | `packages/wth-cli/bin/wth.js:11-14` |
| `--version` (Python) | mencetak `WTH-AI v1.0.0` | `wth-main.py:77` |

Inkonsistensi yang berdampak nyata (bukan kosmetik):

1. **Jalur npm tidak punya `wth-ai`.** `npm install -g @wth-ai/cli` hanya
   memasang `wth`; semua dokumentasi Docker memakai `wth-ai`, jadi operator
   yang mengikuti README bagian npm lalu `docker compose exec wth-cli wth-ai`
   mendapat `command not found`.
2. **`--version` dua wajah.** npm mencegat `--version` sendiri dan mencetak
   `wth-cli v1.0.0` (string hardcoded di `lib/installer.js:11`), sementara
   `wth --version` dari Python mencetak `WTH-AI v1.0.0` dari
   `wth_ai.__version__`. `--version` juga bukan `click.version_option` —
   ditangani manual di callback (`wth-main.py:64-77`), jadi nama program tidak
   pernah muncul dan dua sumber versi bisa menyimpang.
3. **README memakai dua nama untuk hal yang sama.** `wth setup` (baris 238) vs
   `wth-ai run` (baris 325); kolom "Command" di tabel perintah memakai `wth`
   untuk perintah yang di bagian Jalankan ditulis `wth-ai`.
4. **Installer mendiamkan kegagalan.** Loop `for name in wth wth-ai` melakukan
   `[[ -n "$source_path" ]] || continue` — bila `$VENV/bin/wth-ai` tidak ada,
   symlink dilewati tanpa peringatan, lalu baris `ok "$BIN_DIR/wth -> ..."`
   tetap mencetak sukses.

**Keputusan operator (D5).** **`wth-ai` kanonik; `wth` dihapus**, bukan
disisakan sebagai alias. Rekomendasi awal (`wth` kanonik) dibalik operator.
Konsekuensi yang diterima: skrip pengguna lama yang memanggil `wth` akan
`command not found` dan harus diganti; dicatat di README §Jalankan.

**Perbaikan terpasang.**
- [x] `wth-main.py`: `@click.group(name="wth-ai")`,
      `@click.version_option(version=__version__, prog_name="wth-ai",
      message="wth-ai %(version)s")`, dan `cli(prog_name="wth-ai")` di blok
      `__main__` — Click menurunkan `Usage:` dari `sys.argv[0]`, jadi nama harus
      dipasok eksplisit. Blok `--version` manual lama dihapus: satu sumber versi.
- [x] `scripts/install.sh`: launcher ditulis ke `$VENV/bin/wth-ai`, satu
      symlink `$BIN_DIR/wth-ai`; loop `for name in wth wth-ai` dihapus (ini
      sekaligus menutup poin 4 di atas — tidak ada lagi `continue` senyap di
      belakang baris `ok` yang selalu menyebut `wth`). Alias lama dibersihkan
      hanya bila `[[ -L "$BIN_DIR/wth" ]]`, di jalur install dan uninstall.
- [x] npm `package.json`: `"bin": {"wth-ai": "bin/wth.js"}` — satu key.
- [x] npm `bin/wth.js`: intercept `--version` dihapus; argumen diteruskan ke
      Python. `VERSION` tetap diekspor `lib/installer.js` karena masih dipakai
      cek manifest runtime (`isInstalled`, `writeManifest`) — hanya tidak lagi
      dicetak sebagai versi CLI.
- [x] Dokumen: `wth ` → `wth-ai ` di README, AGENTS.md, llms.txt, docs/DEMO,
      AIRGAP, EVIDENCE, COMPARISON, MODES, DOCKER. Pernyataan alias di README
      §Jalankan dihapus; dua penyebutan symlink ganda disederhanakan.
      Dockerfile dan docker-compose memang sudah `wth-ai` dan tidak disentuh;
      `docs/DOCKER.md` punya satu baris contoh `wth engage` yang ikut diganti.
- [x] String di kode yang menyuruh operator menjalankan perintah (~41
      kemunculan, 24 berkas) → `wth-ai`: `wth_ai/install.py`, `orchestrator.py`,
      `tools/doctor.py`, `llm/providers/base.py`, `jobs/*`, `redteam/*`,
      `reporting/*`, `cli/tui/*`, `cli/ui/*`, `config.py`, `killswitch.py`, dst.
      Nilai yang **bukan** nama perintah tidak diubah pada tahap itu:
      `BUILTIN_PROFILE = "wth"` di `agents/bypass.py`, grup slash Discord
      `name="wth"`, dan label sumber skills `("wth", ~/.wth-ai/skills)`.
      Ketiganya **ikut diganti** di P2-11 di bawah.

**Gate.** `tests/test_cli_identity.py`: `--version` memuat `wth_ai.__version__`
dan bukan `wth-cli`; `--help` baris pertama `Usage: wth-ai `; `bin` npm tepat
`{"wth-ai": "bin/wth-ai.js"}`; `wth-ai.js` tidak lagi memuat `wth-cli v${`.
`tests/test_docs_command_names.py`: menolak `wth` telanjang di blok kode
README/AGENTS/llms.txt/docs/DOCKER (token yang sah tetap lolos: `.wth-ai`,
`wth_ai`, `wth_ai_config`, `wth-ai-results`, `wth-ai-cli`, `wth-ai-worker`,
`wth-ai`).
`tests/test_install.py`: setelah install hanya `bin/wth-ai` yang ada; symlink
lama `bin/wth` dihapus; berkas **bukan**-symlink bernama `wth` dipertahankan.
`tests/test_readme_permissions.py`: README tidak lagi menyatakan alias.

**Catatan:** ini murni penamaan dan dokumentasi; tidak ada perubahan protokol,
format artefak, atau perilaku perintah. Jangan digabung ke commit P0.

---

### [x] P2-11 — `wth` → `wth-ai` di nama berkas, nilai tersimpan, dan container

Melanjutkan P2-10 ke tiga lapisan yang dulu dikecualikan. Keputusan operator:
**rename keras** (tanpa alias berkas) + **migrasi sekali** yang idempoten.

**Nama berkas & direktori** (`git mv`, riwayat terjaga):
`wth-main.py` → `wth-ai-main.py`; `packages/wth-cli/` → `packages/wth-ai-cli/`;
`bin/wth.js` → `bin/wth-ai.js`; `prompts/wth_system.txt` →
`prompts/wth_ai_system.txt`; `config/wth_config.yaml` → `config/wth_ai_config.yaml`;
`wth-results/` → `wth-ai-results/` (6 berkas report terlacak ikut). `.gitignore`
memakai pola baru.

Rujukan yang ikut: `wth_ai/cli/entry.py` (satu-satunya loader dinamis),
`packages/wth-ai-cli/lib/installer.js` (`findWthMainPy()` — tiga kandidat path),
`Dockerfile`, `docker/entrypoint.sh`, `action.yml`, `.github/workflows/ci.yml`
dan `release.yml`, `pyproject.toml`, README/AGENTS/llms/docs, dan 77 default
`--config` di `wth-ai-main.py` + `WTHConfig.load()`.

**Direktori hasil** — satu konstanta `RESULTS_DIR_NAME` di
`wth_ai/workspace.py`, dipakai `JobStore`, `ReconEngine`, doctor, dan default
`WTHConfig`. `entry.py:_resolve_home()` kini memanggil `init_workspace()`
alih-alih menyimpan daftar direktori kedua.

**Nilai runtime tersimpan:**
- `BUILTIN_PROFILE = "wth-ai"` + `LEGACY_BUILTIN = "wth"`; `resolve_name()` dan
  `load()` menerima ejaan lama, supaya `agent.bypass: wth` yang sudah tersimpan
  di `user.yaml` tidak diam-diam berjalan tanpa bypass.
- Discord `app_commands.Group(name="wth-ai")` — `/wth …` hilang setelah
  `tree.sync()`, kontrak eksternal yang diterima.
- Label sumber skill `("wth-ai", …)`, prefiks log npm `[wth-ai]`, lampiran
  `wth-ai-output.txt`.

**Container:** `Dockerfile` user `wth-ai` (UID 1000 tetap); compose service
`wth-ai-worker` / `wth-ai-cli`, volume `wth-ai-home`; service `wth-ai` di
`docker-compose.offline.yaml`; `docs/DOCKER.md` ikut.

**Migrasi.** `wth_ai/workspace.py:_migrate_legacy_names()`, dipanggil
`init_workspace()` **sebelum** direktori dibuat: rename `$WTH_HOME/wth-results`
→ `wth-ai-results` dan `config/wth_config.yaml` → `wth_ai_config.yaml` hanya
bila sumber ada dan tujuan belum ada. Aman diulang; tidak menimpa;
`data/engagements/`, `logs/`, `reports/` tidak disentuh (AGENTS.md §5).

**Gate.** `tests/test_workspace_migration.py` (baru, 5 kasus: rename,
idempoten, tidak menimpa, rename config, direktori terlacak utuh);
`tests/test_bypass.py::test_legacy_profile_name_still_resolves`;
`tests/test_cli_identity.py` (path paket + `bin/wth-ai.js`);
`tests/test_ci_offline.py` (nama service); `tests/test_jobs.py`
(`wth-ai-results`).

**Risiko diterima:** `/wth` Discord hilang; volume `wth-home` lama
ditinggalkan oleh `wth-ai-home`; skrip Docker operator harus diganti.

---

## 5. P3 — higiene (Fase 3)

- [x] P3-1 bersihkan import tak terpakai
  - Tidak ada linter di venv (`pyflakes`/`ruff`/`flake8`/`pylint` semuanya
    `No module named`), jadi klaim "pyflakes → 0" dicabut. Penggantinya pemindai
    AST stdlib: import yang namanya tidak muncul sebagai `Name`/`Attribute`/
    string annotation dan tidak ditandai `# noqa`.
  - Hasil akhir: **0** nama tak terpakai. Penghapusan terakhir yang lolos
    pemindai awal adalah import di dalam fungsi —
    `slash_router.apply_target_from_text`, `doctor.ReconPlanner`,
    `ot.client.MAX_COUNT`, `tool_runner.{Any,PermissionMode}`.
  - Sengaja **tidak** dihapus: probe importabilitas ber-`# noqa: F401` di
    `wth_ai/tools/doctor.py` dan re-export di `wth_ai/cli/tui/command_class.py` —
    keduanya dipakai sebagai efek samping, bukan sebagai nama.
  - Bukti: pemindai → `TOTAL 0`; `importlib` walk seluruh paket → `import
    failures: 0`; `pytest tests` → `1328 passed in 269.65s`.
- [x] P3-2 coverage minimal untuk `bots/` (auth + handler) — permukaan yang mengeksekusi perintah dari chat
  - `tests/test_bot_auth.py` (10 tes). Coverage ini menemukan dua bug nyata:
    allowlist Discord/Telegram **fail-open** (id yang absen melewati cek, jadi DM
    lolos dari allowlist), dan `group.command()`/`CommandHandler()` menerima
    `lambda` yang bukan coroutine function → `WTHDiscordBot` gagal dibuat.
- [x] P3-3 hapus `wth_ai/slash_commands.py`, `cli/status_bar.py` (tidak dipakai)
  - Keduanya nol importer di luar tes; `WTHRepl._build_toolbar` (repl.py:391,
    tidak pernah dipanggil — yang hidup `_toolbar`) ikut dihapus.
- [x] P3-5 `vault.py:241` naive `datetime.now()` → `datetime.now(timezone.utc)`
  - `vault.py` sekarang memakai `models.utc_now()`; tidak ada `datetime` tersisa.
- [x] P3-4 **D4**: hapus pohon legacy (`core/`, root `agents/`, `exploitation/`, `reconnaissance/`, `learning/`, `integration/`, root `reporting/`, `utils/`, `main.py`) + 4 test-nya. Hilangkan komentar "legacy" di AGENTS.md §2 untuk `wth_ai/agents/*` dan `wth_ai/executor/vuln/*` yang justru jalur produksi
  - 68 path dihapus dari git (62 berkas pohon + `main.py` + 4 test lama +
    `tests/test_integration.py`). AGENTS.md §2 kini menyatakan pohon itu sudah
    hilang dan `wth_ai/agents/*` + `wth_ai/executor/vuln/*` adalah jalur produksi.
  - Koreksi catatan lama: `tests/test_integration.py` **bukan** satu-satunya
    pengguna `aiohttp` — tidak ada kode yang meng-importnya sama sekali.
    Entri `aiohttp` dibuang dari `pyproject.toml [full]` dan
    `wth_ai/install.py` (THE_PACKAGES + `EXTRAS_FOR_FEATURES`).

---

## 6. Urutan kerja & gate

| Fase | Isi | Selesai bila |
|---|---|---|
| **0** | P0-1 … P0-5 | lima gate P0 hijau; `grep -rn "authorized.*True" wth_ai/ wth-main.py` hanya menyisakan tempat yang membaca kartu; `grep -rn "set_unrestricted(True)\|/permission unrestricted" wth-main.py` kosong |
| **1** | P1-0 … P1-8 | suite bersih di venv minimal; `doctor` selamat dari config rusak; dua `WTH_HOME` terisolasi; nol secret di artefak |
| **2** | P2 + generator dokumen | test konsistensi TUI & docs-check hijau; `wth`/`wth-ai` identik di semua permukaan (P2-10) |
| **3** | P3 | pyflakes 0; tidak ada modul mati |

Aturan yang berlaku untuk setiap item:

1. Test ditulis lebih dulu dan **gagal** sebelum implementasi (AGENTS.md §5).
2. Klaim di `AGENTS.md`/`README` yang tidak diuji dianggap **belum terverifikasi** —
   P0-1, P0-2, P0-4, dan P0-5 lahir dari klaim yang tidak pernah diuji pada
   jalurnya.
3. Tidak ada dependency baru; PDF/hash/Modbus sengaja stdlib.
4. Setiap commit menyebut perintah verifikasi dan hasilnya.
5. Setelah Fase 0, perbarui `AGENTS.md` §3 agar invarian yang ditegakkan
   menyebut jalurnya (recon, agent, redteam) — bukan klaim menyeluruh.

---

## 7. Catatan kejujuran

- Satu-satunya host yang dihubungi saat audit adalah `example.com` (domain
  contoh IANA), sekali, untuk membuktikan P0-1. Tidak ada host lain.
- `tests/test_research.py` (3 test) gagal karena butuh jaringan — kondisi yang
  sama sebelum audit ini; tidak dihitung sebagai regresi.
- Angka coverage 66% berasal dari venv audit yang tidak memasang ekstraks
  `full`; modul opsional akan tampak lebih rendah dari kenyataan.
- Klaim "nol pemanggil" di rencana ini berbasis `grep` atas `wth_ai/` +
  `trace_path` graph, bukan bukti ekshaustif seluruh repo. Index sengaja
  melewati 1.733 file gitignored.
- Yang **belum** diperiksa dan tidak jadi dasar klaim: isi
  `recon/tools/{adapters,api_adapters}.py`, `mcp/*`, `gateway/*`,
  `redteam/executor.py`, dan badan penuh `orchestrator.py`.