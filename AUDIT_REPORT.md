# WTH-AI FULL CODEBASE AUDIT REPORT

> Audit read-only, evidence-based, terhadap seluruh codebase `wth-ai` per 2026-09-01. Tidak ada file yang diubah selama audit. Detail temuan keamanan lengkap (format CWE/OWASP/evidence/alur) ada di `SECURITY_AUDIT.md`. Peta arsitektur & diagram ada di `ARCHITECTURE.md`.

## 1. Executive Summary

WTH-AI adalah **CLI agent AI otonom untuk bug bounty/pentest** — bukan aplikasi web. Ia menjalankan planner/executor berbasis LLM yang benar-benar mengeksekusi shell command dan HTTP request nyata terhadap target, dengan sistem guardrail (permission mode, scope authorization, approval gate, TTST evidence) sebagai fitur pembeda utamanya.

Kondisi arsitektur & kualitas kode dasar **cukup solid** untuk banyak aspek: subprocess selalu `shell=False`+argv list, SQL selalu parameterized, secret vault terenkripsi dengan benar, redaksi secret diterapkan berlapis, `yaml.safe_load` dipakai konsisten, dan mekanisme anti-halusinasi (TTST/GroundingFirewall) sungguh-sungguh diimplementasikan dan reachable — bukan sekadar klaim dokumentasi.

**Namun, ditemukan pola berulang yang serius: guardrail-guardrail inti yang didokumentasikan sebagai "hukum mutlak" (`AGENT.md`) secara konsisten memiliki celah bypass yang membuatnya tidak benar-benar menegakkan apa yang diklaim.** Pola ini muncul di banyak lapisan independen (permission manager, job policy engine, recon authorization, approval gate, bot access control) — mengindikasikan gap sistemik antara *desain yang didokumentasikan* dan *implementasi aktual*, bukan sekadar satu-dua bug terisolasi. Yang membuatnya lebih berdampak: kombinasi konfigurasi default di semua jalur deployment resmi (`Dockerfile`, `docker-compose.yaml`, `.env.example`) justru mengaktifkan mode paling permisif secara default.

Distribusi temuan (lihat `SECURITY_AUDIT.md` untuk detail lengkap tiap temuan):

```
CRITICAL : 7
HIGH     : 6
MEDIUM   : 13
LOW      : 6
INFO     : 3
```

Tidak ditemukan indikasi RCE dari internet tanpa langkah pengguna (tidak ada web-facing HTTP API), dan tidak ditemukan SQL injection. Risiko utama berpusat pada: (1) **guardrail otorisasi/eksekusi yang bisa dilewati** — baik oleh operator ceroboh, bot user yang tidak dipercaya, maupun berpotensi oleh target eksternal via indirect prompt injection; dan (2) **higiene repository** — secret dan data engagement nyata ter-commit ke git.

## 2. Scope

Diperiksa: seluruh package aktif `wth_ai/` (242 file, ~26.500 baris), root launcher `wth-main.py` (1401 baris), kode legacy (`core/`, `agents/`/`exploitation/`/`reconnaissance/`/`learning/`/`integration/`/`reporting/`/`utils/` root, ~2000 baris), seluruh `tests/` (39 file, 4310 baris — dijalankan: 334 passed), `packages/wth-cli` (npm installer JS), `Dockerfile`/`docker-compose.yaml`/`docker/entrypoint.sh`, `.github/workflows/*.yml`, `pyproject.toml`/`requirements.txt`, `install.sh`/`scripts/build_binary.sh`, seluruh file konfigurasi (`config/*.yaml`, `.env*`), dan git history penuh repo.

Tidak diperiksa mendalam (lihat §30 Coverage & Limitations): rendering TUI (`cli/tui/*`), isi lengkap `skills/` external/official packs, runtime behavior yang membutuhkan API key LLM aktif.

## 3. Methodology

Audit dilakukan bertingkat: (1) repository discovery & inventory, (2) pembacaan dokumentasi vs implementasi aktual (documentation drift check), (3) investigasi git history untuk secret/data leakage, (4) audit mendalam trust-boundary inti (permission/scope/approval/shell execution) — dibaca langsung baris-per-baris, (5) lima jalur audit paralel independen untuk cakupan penuh sisa codebase (agent/LLM loop & executor vuln modules; recon engine & tool execution; CLI/jobs/bot-gateway/MCP/RPC; data layer & config; legacy code/dependency/Docker/CI), masing-masing membaca source code penuh area tugasnya dan melaporkan temuan dengan evidence source→sink, (6) verifikasi silang manual untuk menutup pertanyaan terbuka antar-jalur (mis. interaksi bug approval-permanen dengan `PermissionManager.check_command()`, diverifikasi langsung ke `terminal/bridge.py`), (7) deduplikasi & konsolidasi temuan lintas-jalur ke satu daftar tunggal. Tidak menjalankan scanner otomatis (semgrep/bandit) — semua temuan berasal dari pembacaan manual source code dengan evidence konkret.

## 4. Technology Stack

| Layer | Technology |
|---|---|
| Bahasa utama | Python ≥3.10 (target 3.12), sedikit Node.js (installer npm) |
| CLI/UI | Click, Rich, prompt-toolkit, Textual (TUI opsional) |
| LLM Provider | OpenRouter, OpenAI, Anthropic, Google, Groq, Ollama, "9router" lokal |
| Storage | SQLite (memory/FTS), JSON/JSONL file-based (jobs, evidence, reports), tidak ada DB server |
| Secret management | `cryptography` (Fernet/AES-128-CBC+HMAC) — vault custom |
| Bot | discord.py, python-telegram-bot |
| Container | Docker (python:3.12-slim), docker-compose |
| CI/CD | GitHub Actions (test-only + release build multi-platform via PyInstaller + npm publish) |
| Packaging | setuptools (pip), npm (`@wth-ai/cli` installer yang download binary) |
| Testing | pytest (334 test, semua pass saat audit) |
| Recon tooling | 200+ tool eksternal (subfinder, nmap, ffuf, dst) + builtin Python fallback |

Tidak ada: web frontend/dashboard, database server terpisah, ORM, GraphQL, message queue eksternal (Redis/RabbitMQ — job queue adalah SQLite/JSON lokal).

## 5. Repository Architecture

Lihat `ARCHITECTURE.md` untuk diagram lengkap. Ringkasan: repo berisi **dua produk paralel** — `wth_ai/` (aktif, dipanggil `wth-main.py`) dan kode legacy "H1T4M-AI" (`core/`, `agents/` root, dll — dikonfirmasi orphaned, lihat ARCH-01 di `SECURITY_AUDIT.md`). Entry point resmi di `pyproject.toml` (`wth_ai.cli.entry:main`) dikonfirmasi broken oleh komentar maintainer sendiri di `install.sh` (ARCH-02).

## 6. Application Flow

Lihat `ARCHITECTURE.md` §4 untuk lifecycle lengkap (CLI, Bot, Recon Job). Ringkas: `Operator/Bot → Orchestrator → Planner/Executor/Supervisor Agent (LLM) → AgentTerminalBridge (trust boundary utama) → PermissionManager + ScopeManager + ApprovalGate → shell_runner/requests (sink eksekusi nyata) → EvidenceVault/GroundingFirewall → Report`.

## 7. Authentication Architecture

WTH-AI tidak memiliki AuthN aplikasi web klasik. Model kepercayaan CLI: siapa pun dengan akses shell = trusted operator (wajar untuk tool ini). Untuk kanal bot (Discord/Telegram), "autentikasi" seharusnya berupa allowlist channel/chat + role — **ditemukan lemah** (lihat NET-01 di `SECURITY_AUDIT.md`, CRITICAL). API key LLM & bot token disimpan di vault terenkripsi (Fernet), diresolve via referensi `vault:<name>`, tidak pernah plaintext di config aktif.

## 8. Authorization Architecture

Tiga lapis nominal: **Permission Mode** (`read_only`→`standard`→`elevated`→`full_lab`→`unrestricted`, dicek `PermissionManager.check_command()`), **Scope Authorization** (`authorized=yes` self-declared per target, dicek `ScopeManager`), **Approval Gate** (`/approve` untuk command high-risk). Ketiganya ditemukan punya celah bypass material — lihat AUTHZ-01 s/d AUTHZ-06 di `SECURITY_AUDIT.md`. Tidak ada model role-based per-identitas (semua "identitas" dalam satu sesi orchestrator setara).

## 9. Data Flow & Trust Boundaries

Lihat `ARCHITECTURE.md` §5. Titik trust-boundary paling signifikan: (a) Bot chat user → orchestrator (tidak ada lapisan tambahan yang konsisten), (b) respons HTTP dari target eksternal → context LLM (masuk mentah, berpotensi prompt injection), (c) `WTH_UNRESTRICTED` env var yang melintasi hampir semua boundary lain sekaligus.

## 10. Attack Surface

```
CLI lokal              — trusted operator (by design)
Bot Discord/Telegram   — INTERNET-FACING, otorisasi lemah (NET-01, CRITICAL)
RPC (gateway + execute)— Unix Domain Socket, chmod 600, TIDAK network-facing (aman dari remote attacker)
MCP server built-in    — stdio, 3 tool read-only
200+ recon tool eksternal — subprocess lokal, argv-only
Webhook                — outbound only, tidak ada receiver masuk yang ditemukan
```
Tidak ada HTTP API publik/GraphQL/WebSocket server yang expose ke internet — permukaan serangan network utama adalah bot chat platform.

## 11. Findings Summary

| ID | Severity | Temuan | Komponen | Confidence |
|---|---|---|---|---|
| AUTHZ-01 | CRITICAL | `WTH_UNRESTRICTED` default aktif — master switch lintas 5+ guardrail | engagement/mode.py + 6 caller | HIGH |
| AUTHZ-02 | CRITICAL | `PermissionManager.check_command()` — bash/sh/zsh skip + substring match bypass | permission_manager.py | HIGH |
| AUTHZ-03 | CRITICAL | Mode UNRESTRICTED skip hard denylist destruktif | permission_manager.py | HIGH |
| AUTHZ-04 | CRITICAL | Approval `/approve` permanen sepanjang sesi, bukan per-command | agents/supervisor*.py | HIGH |
| NET-01 | CRITICAL | Broken access control bot Discord/Telegram | bots/*.py | HIGH |
| DATA-01 | CRITICAL | Plugin autoload dari cwd → arbitrary code execution | plugins/loader.py | HIGH |
| AGENT-01 | CRITICAL | `scope_set` default authorized=True + celah indirect prompt injection | agents/supervisor_dispatch.py, tool_loop.py | HIGH/MEDIUM |
| AUTHZ-05 | HIGH | `PolicyEngine.evaluate()` dead-code fallthrough — selalu True | jobs/policy.py | HIGH |
| NET-02 | HIGH | `ExecuteRpcServer` validasi string dummy, bukan kode aktual | rpc/execute_server.py | HIGH |
| DATA-02 | HIGH | `data/cli_history` ter-commit git, berisi secret + target real | git history | HIGH |
| INFRA-01 | HIGH | Tidak ada `.dockerignore` — `.git`/`.env` berisiko ter-bake ke image | Dockerfile | HIGH |
| SECRET-01 | HIGH* | API key OpenRouter bocor di git history (*sudah dimitigasi user) | git history | HIGH |
| DATA-03 | HIGH | Data engagement real ter-commit git (recon/reports/logs/wth-results) | git history | HIGH |
| CFG-01 | MEDIUM | `default_permission` fallback tidak konsisten, jatuh ke `full_lab` | config.py | MEDIUM |
| CFG-02 | MEDIUM | `handler_recon` default `authorized=True` bila metadata kosong | jobs/handlers.py | HIGH |
| DATA-04 | MEDIUM | `write_env_file()` tulis secret tanpa chmod 600 | config.py | HIGH |
| PROF-01 | MEDIUM | Profile `bug-bounty`/`ctf-aggressive` nonaktifkan auth by design | profiles.py | HIGH |
| RECON-01 | MEDIUM | Builtin HTTP fetcher tanpa validasi IP/redirect (SSRF defense-in-depth) | recon/http.py | HIGH |
| AGENT-02 | MEDIUM | Executor vuln modules tanpa scope re-check di titik eksekusi | executor/vuln/*.py | HIGH |
| AGENT-03 | MEDIUM | GroundingFirewall tidak diterapkan ke output ReportBuilder | agents/report_builder.py | HIGH |
| AUTHZ-06 | MEDIUM | Actor `recon` dapat `run_shell` di permission level terendah | terminal/bridge.py | HIGH |
| INFRA-02 | MEDIUM | npm installer tanpa verifikasi checksum binary | packages/wth-cli | HIGH |
| INFRA-03 | MEDIUM | docker-compose port internal bind ke 0.0.0.0 | docker-compose.yaml | MEDIUM |
| CI-01 | MEDIUM | CI tanpa security scanning (pip-audit/bandit/semgrep) | .github/workflows | HIGH |
| ARCH-01 | MEDIUM | Kode legacy orphaned ~2000 baris, masih diuji CI | core/ dkk | HIGH |
| ARCH-02 | MEDIUM | Entry point resmi pyproject.toml diketahui broken | pyproject.toml | HIGH |
| RECON-02 | LOW | Argument injection potensial pada tool adapter | recon/tools/adapters.py | MEDIUM |
| DATA-05 | LOW | Redaksi EvidenceVault regex tidak lengkap | evidence/vault.py | MEDIUM |
| INFRA-04 | LOW | `WTH_GITHUB_TOKEN` bisa ikut ke host redirect pihak ketiga | packages/wth-cli/installer.js | MEDIUM |
| RECON-03 | LOW | `job_id` eksplisit tidak disanitasi ulang (belum reachable) | recon/engine.py | LOW |
| INFRA-05 | LOW | Docker base image floating tag | Dockerfile | HIGH |
| ARCH-03 | LOW | Dua sistem tool-registry paralel berpotensi drift | tools/ vs recon/tools/ | MEDIUM |
| INFO-01/02/03 | INFO | Documentation drift, TUI belum diaudit, artefak pycache stray | berbagai | — |

## 12. Critical Findings

Lihat `SECURITY_AUDIT.md` bagian **CRITICAL** untuk detail penuh (evidence, alur source→sink, rekomendasi, verification) untuk: AUTHZ-01, AUTHZ-02, AUTHZ-03, AUTHZ-04, NET-01, DATA-01, AGENT-01.

## 13. High Findings

Lihat `SECURITY_AUDIT.md` bagian **HIGH**: AUTHZ-05, NET-02, DATA-02, INFRA-01, SECRET-01, DATA-03.

## 14. Medium Findings

Lihat `SECURITY_AUDIT.md` bagian **MEDIUM**: CFG-01, CFG-02, DATA-04, PROF-01, RECON-01, AGENT-02, AGENT-03, AUTHZ-06, INFRA-02, INFRA-03, CI-01, ARCH-01, ARCH-02.

## 15. Low Findings

Lihat `SECURITY_AUDIT.md` bagian **LOW**: RECON-02, DATA-05, INFRA-04, RECON-03, INFRA-05, ARCH-03.

## 16. Informational Findings

Lihat `SECURITY_AUDIT.md` bagian **INFORMATIONAL**: INFO-01 (documentation drift `engagement/mode.py`), INFO-02 (TUI belum diaudit), INFO-03 (artefak pycache stray, bukan bagian codebase yang di-ship).

## 17. Architecture Findings

- **Dua produk paralel dalam satu repo** (ARCH-01) — legacy "H1T4M-AI" (~2000 baris) tidak pernah dieksekusi jalur produksi tapi masih diuji CI, membengkakkan permukaan audit tanpa nilai nyata.
- **Entry point resmi broken** (ARCH-02) — `pip install wth-ai` mengikuti packaging standar akan gagal, workaround (`wth-main.py`) menjadi de-facto entry point tanpa didokumentasikan sebagai demikian di `pyproject.toml`.
- **Dua sistem tool-registry paralel** (ARCH-03) — `tools/manifest.yaml` (lama, 215 tools) vs `recon/tools/catalog.py` (baru, Fase 5) berpotensi drift.
- **Trust boundary bot tidak dipisah dari trust boundary CLI** — orchestrator yang sama dipakai persis untuk kedua kanal tanpa lapisan tambahan (lihat NET-01) — flaw desain, bukan sekadar bug implementasi.

## 18. Code Quality Findings

- Kode aktif (`wth_ai/`) menunjukkan disiplin cukup baik: type hints konsisten, dataclass untuk model, pemisahan modul yang jelas per tanggung jawab (agents/executor/recon/terminal/dll terpisah rapi).
- Pola *dead-code fallthrough* yang ditemukan di `PolicyEngine.evaluate()` (AUTHZ-05) adalah contoh klasik "kode yang terlihat benar tapi secara logika salah" — layak jadi pengingat untuk linting tambahan (unreachable-branch detection) di CI.
- Tidak ditemukan god-class/circular dependency signifikan dalam pembacaan area yang diaudit.
- TODO/FIXME/HACK tidak secara sistematis di-grep menyeluruh di sesi ini — lihat §30 Limitations.

## 19. Performance Findings

Di luar prioritas utama audit ini (keamanan didahulukan sesuai instruksi), namun dicatat dari temuan sampingan: `RaceExecutor` menjalankan concurrency hardcoded (`max_workers=20`) tanpa menghormati rate limit scope (lihat AGENT-02) — berpotensi memberi beban tak terduga ke target selain isu keamanan. `SafeToolRunner` sudah membatasi output 200KB dan menerapkan timeout wajib — mitigasi resource-exhaustion yang baik untuk proses recon.

## 20. Reliability Findings

- `ApprovalRegistry` mendukung TTL/expiry — baik.
- Tidak ditemukan penggunaan transaction/locking eksplisit di layer job queue (SQLite/JSON-based) — race condition pada job admission concurrent belum diverifikasi mendalam (di luar cakupan penuh audit ini, catat sebagai limitation).
- CI hijau (334 test pass) memberi false confidence karena mencakup kode legacy orphaned (ARCH-01) dan tidak mencakup `PolicyEngine` sama sekali (nol test).

## 21. Dependency Findings

Dependency inti (`cryptography`, `pyyaml`, `requests`, `discord.py`, `python-telegram-bot`) dipakai dengan pola aman yang terverifikasi (Fernet benar, `yaml.safe_load` konsisten, tidak ada `pickle`). Tidak ditemukan versi yang secara eksplisit diketahui vulnerable dalam audit manual ini (tidak dijalankan `pip-audit` otomatis — lihat CI-01, rekomendasi tambahkan scanning otomatis ke pipeline). Base image Docker floating tag (INFRA-05, LOW).

## 22. DevOps / Infrastructure Findings

Lihat INFRA-01 s/d INFRA-05 di `SECURITY_AUDIT.md`. Ringkas: container non-root (baik), tidak ada privileged mode (baik), tapi tidak ada `.dockerignore` (HIGH), npm installer tanpa checksum verification (MEDIUM), port internal bind ke semua interface (MEDIUM), CI tanpa security scanning (MEDIUM).

## 23. Testing Coverage Assessment

`pytest tests/ -v` → **334 passed**, 0 failed, saat audit dijalankan. Modul guardrail inti (`permission_manager`, `approval_gate`, `scope_manager`, `shell_runner`, `tool_runner`, `WTH_UNRESTRICTED`/`is_unrestricted`) **punya test coverage**. Namun `PolicyEngine` (jobs/policy.py) — modul yang justru mengandung bug fail-open paling jelas (AUTHZ-05) — **nol test coverage**, menjelaskan mengapa bug tersebut lolos tanpa terdeteksi. Tidak ditemukan test untuk skenario end-to-end "bot Discord non-owner mencoba `/approve`" atau "target eksternal membalas dengan instruksi prompt injection" — wajar karena keduanya butuh integration/runtime test, bukan unit test.

## 24. Security Strengths

Lihat bagian "Security Strengths" lengkap di `SECURITY_AUDIT.md`. Highlight: subprocess selalu `shell=False`+argv, secret vault Fernet yang benar, redaksi secret berlapis konsisten, `yaml.safe_load` tanpa pengecualian, TTST/GroundingFirewall benar-benar reachable (bukan cuma dokumentasi), RPC hanya lewat Unix socket chmod 600 (tidak network-facing), isolasi data per-session_id, dan desain SSRF-probe yang sudah non-destruktif.

## 25. Technical Debt

**Immediate (P0-adjacent):**
- Dua produk paralel dalam satu repo (legacy vs aktif) — sumber kebingungan maintainer & false-confidence CI.
- Data engagement real + secret tercampur dengan source code di git history.

**Short term:**
- Entry point resmi broken tapi dibiarkan dengan workaround tidak terdokumentasi.
- Nol test coverage untuk `PolicyEngine` — modul keamanan inti tanpa jaring pengaman regresi.
- Dua sistem tool-registry paralel berpotensi drift.

**Long term:**
- Tidak ada security scanning otomatis di CI (SAST/dependency audit).
- Model otorisasi "self-declared authorized=yes" secara arsitektural bergantung penuh pada kejujuran operator/LLM — cocok untuk single-operator CLI, tapi rapuh begitu diperluas ke kanal multi-user (bot) atau dipengaruhi konten eksternal (prompt injection). Perlu rethinking arsitektural jika bot/multi-user tetap jadi fitur resmi.

## 26. Prioritized Remediation Plan

### P0 — Immediate (sebelum instance ini dipakai terhadap target nyata lagi, terutama via Docker/bot)
1. Ubah default `WTH_UNRESTRICTED` ke `"0"` di `Dockerfile`, `docker-compose.yaml`, `.env*.example` (AUTHZ-01).
2. Perbaiki `PermissionManager.check_command()`: hapus bypass `bash/sh/zsh`, ganti substring match jadi token match, pastikan denylist absolut dicek sebelum early-return mode manapun (AUTHZ-02, AUTHZ-03).
3. Perbaiki approval gate agar per-command, bukan `bool(set)` global per sesi (AUTHZ-04).
4. Kunci akses bot Discord/Telegram: allowlist fail-closed, role-check konsisten di semua command, hapus hardcode `role="owner"` Telegram (NET-01).
5. Hapus/batasi plugin autoload dari current working directory (DATA-01).
6. Bersihkan `data/cli_history`, `recon/`, `reports/`, `logs/engagement_logs/`, `wth-results/` dari git tracking + tambahkan ke `.gitignore` (DATA-02, DATA-03).
7. Tambahkan `.dockerignore` (INFRA-01).

### P1 — High Priority
- Perbaiki `PolicyEngine.evaluate()` agar `safe_mode` benar-benar menegakkan sesuatu (AUTHZ-05).
- Tambahkan content-based validation pada `ExecuteRpcServer` (NET-02).
- Default `scope_set.authorized` ke `False` + wajibkan field; tambahkan instruksi anti-prompt-injection eksplisit di semua system prompt (AGENT-01).
- Purge git history untuk secret yang pernah ter-commit (SECRET-01 — sudah dimitigasi sebagian per konfirmasi user, pastikan history juga di-purge bila belum).

### P2 — Medium Priority
- Satukan default `default_permission` (CFG-01), perbaiki default `authorized` di `handler_recon` (CFG-02).
- `write_env_file()` tambahkan `secure_file()` (DATA-04).
- Preset `bug-bounty`/`ctf-aggressive` jangan nonaktifkan `require_authorized` (PROF-01).
- Tambahkan validasi IP/redirect di builtin HTTP fetcher (RECON-01); tambahkan scope re-check di executor vuln modules (AGENT-02); terapkan GroundingFirewall ke ReportBuilder (AGENT-03).
- Tambahkan checksum verification di npm installer (INFRA-02); bind port internal ke localhost (INFRA-03); tambahkan security scanning ke CI (CI-01).
- Hapus kode legacy orphaned (ARCH-01); perbaiki entry point resmi (ARCH-02).

### P3 — Improvement
- Konsolidasi dua sistem tool-registry (ARCH-03).
- Perluas coverage redaksi secret di EvidenceVault (DATA-05); perbaiki header leak pada redirect di npm installer (INFRA-04); pin Docker base image ke digest (INFRA-05); validasi target di tool adapters (RECON-02).

## 27. Recommended Architecture Improvements

1. **Pisahkan "capability level" dari "human approval" dan dari "scope authorization"** — saat ini `WTH_UNRESTRICTED` menjadi satu flag yang mengontrol ketiganya sekaligus (AUTHZ-01), yang secara arsitektural salah: ketiga konsep ini punya threat model berbeda dan seharusnya independen.
2. **Bot gateway butuh trust-boundary sendiri**, bukan berbagi orchestrator identik dengan CLI. Idealnya: bot session mendapat permission mode yang di-cap (mis. maksimum `read_only` kecuali eksplisit di-upgrade oleh owner terverifikasi), terlepas dari apa yang diminta user chat.
3. **Tool-result dari target eksternal perlu ditandai sebagai untrusted data secara struktural** (bukan hanya lewat instruksi prompt), misalnya dengan memisahkan channel "observation" dari channel "instruction" di level format pesan API LLM (jika provider mendukung), atau minimal wrapping tegas + validasi tambahan sebelum tool-call sensitif (`scope_set`, `run_shell` dengan argumen baru) dieksekusi berdasarkan turn yang baru saja menerima tool-result dari luar.
4. **Satu entry point resmi** — hapus dualisme `wth-main.py` vs `wth_ai.cli.entry:main`.

## 28. Recommended Security Controls

- Centralized "safety kill switch" test suite yang dijalankan di CI: setiap kombinasi (permission_mode × unrestricted × approved) untuk command destruktif representatif HARUS terverifikasi blocked kecuali kombinasi yang eksplisit diizinkan — akan menangkap kelas bug AUTHZ-02/03/05 secara otomatis di masa depan.
- Security scanning di CI: `pip-audit` (dependency), `bandit`/`semgrep` custom rule (pola `shell=True`, string-formatted subprocess, substring-based allowlist).
- `.dockerignore` + review build-context sebelum setiap release image.
- Git history purge + pre-commit hook (`detect-secrets`/`gitleaks`) untuk mencegah repetisi kelas insiden `.env`/`cli_history`.
- Rate limiting & concurrency cap yang benar-benar dihormati di semua executor (termasuk `RaceExecutor`), bukan hardcoded.

## 29. Files Reviewed

Dibaca penuh (bukan sampling): `README.md`, `AGENTS.md`, `AGENT.md`, `pyproject.toml`, `Dockerfile`, `docker-compose.yaml`, `docker/entrypoint.sh`, `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.env`/`.env.example`/`.env.docker.example`, `.gitignore`, seluruh `wth_ai/permission_manager.py`, `approval_gate.py`, `approvals.py`, `scope_manager.py`, `scope_intent.py`, `shell_runner.py`, `tool_runner.py`, `vault.py`, `workspace.py`, `config.py`, `profiles.py`, `engagement/mode.py`, `wth_ai/terminal/*`, `wth_ai/agents/*` (2435 baris), `wth_ai/llm/*`, `openrouter.py`, `orchestrator.py`, `wth_ai/brain/*`, `wth_ai/executor/*` (884 baris), `wth_ai/loop/*`, `wth_ai/recon/*` (4189 baris — seluruh file), `wth_ai/tools/*`, `wth_ai/probe/*`, `wth_ai/http/*`, `wth_ai/cli/*` (4170 baris), `wth_ai/jobs/*`, `wth_ai/gateway/*`, `wth_ai/bots/*`, `wth_ai/mcp/*`, `wth_ai/rpc/*`, `wth_ai/cron/*`, `wth_ai/integrations/*`, `wth-main.py` (1401 baris), `wth_ai/graph/*`, `wth_ai/memory/*`, `wth_ai/evidence/*`, `findings_db.py`, `session_store.py`, `models.py`, `plugins/*`, `wth_ai/skills/*`, seluruh kode legacy (`core/`, `agents/` root, `exploitation/`, `reconnaissance/`, `learning/`, `integration/`, `reporting/` root, `utils/`, `main.py`), `config/*.yaml`, `packages/wth-cli/*`, `install.sh`, `scripts/build_binary.sh`, git history penuh (`git log`, `git show` untuk commit signifikan), struktur & sebagian isi `reports/`, `logs/engagement_logs/`, `recon/`, `wth-results/`, `data/`.

Dibaca sebagian/grep-only: `wth_ai/cli/tui/*` (rendering-only), `wth_ai/skills/external`/`official/*` (skill pack konten), isi lengkap 39 file `tests/` (dibaca nama+struktur, tidak semua baris).

## 30. Coverage & Limitations

**Diperiksa penuh:** trust-boundary inti (permission/scope/approval/shell), agent LLM loop & executor vuln modules, recon engine & tool registry, CLI/jobs/bot-gateway/MCP/RPC, data layer (graph/memory/evidence/DB), legacy code, dependency management, Docker/CI, git history.

**Diperiksa sebagian:** TUI rendering code (risiko rendah, tidak dianggap prioritas), isi skill packs eksternal (`skills/external`, `skills/official`) — struktur diperiksa, konten SKILL.md tidak diaudit baris-per-baris untuk kemungkinan instruksi berbahaya bila dimuat sebagai bagian prompt.

**Belum diverifikasi (butuh runtime test):**
- Efektivitas nyata mitigasi indirect prompt injection (AGENT-01) — butuh simulasi red-team dengan LLM aktif.
- Reachability langsung `ExecuteRpcServer.execute()` dari tool-calling LLM (NET-02).
- Interaksi penuh `default_permission` (CFG-01) dengan `cli/setup_wizard.py` dari fresh-install sampai sesi pertama.
- Race condition pada job admission concurrent di level SQLite/JSON job store — tidak ditelusuri mendalam.
- Visibilitas repo GitHub (publik/privat) tidak terverifikasi dalam audit ini (`gh` tidak ter-autentikasi saat audit dijalankan) — relevan untuk menilai urgensi DATA-03/SECRET-01.

**Tidak dilakukan (di luar scope read-only static audit):** dynamic/runtime testing, penetration testing terhadap instance WTH-AI yang berjalan, fuzzing, load testing.

Kesimpulan yang bertanggung jawab untuk diambil dari audit ini: **tidak ditemukan vulnerability kritis pada area yang diperiksa yang sifatnya remote-unauthenticated-RCE dari internet** (karena tidak ada permukaan HTTP publik) — namun ditemukan banyak celah **broken access control pada guardrail inti yang menjadi model keamanan utama produk ini**, yang berdampak signifikan begitu tool dijalankan sesuai jalur deployment resmi (Docker) atau diekspos lewat bot. Ini BUKAN pernyataan "seluruh codebase aman".
