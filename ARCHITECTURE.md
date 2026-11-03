# WTH-AI — Dokumen Arsitektur

> Disusun dari hasil audit codebase menyeluruh (read-only), berdasarkan pembacaan source code aktual — bukan asumsi dari dokumentasi. Lihat `AUDIT_REPORT.md` untuk temuan lengkap dan `SECURITY_AUDIT.md` untuk detail keamanan.

## 1. Apa itu WTH-AI

WTH-AI adalah **CLI agent AI otonom untuk bug bounty/pentest** (bukan aplikasi web tradisional). Ia menggabungkan:

- Multi-provider LLM (OpenRouter, OpenAI, Anthropic, Google, Groq, Ollama, atau router lokal "9router")
- Sebuah **terminal bridge** yang memberi agent LLM akses nyata untuk menjalankan `curl`/`httpx`/`nmap`/shell command dan HTTP request langsung terhadap target
- Asset graph, evidence vault, dan mekanisme "TTST" (Terminal Transcript Source of Truth) untuk mencegah LLM berhalusinasi soal temuan vulnerability
- Bot Discord/Telegram sebagai kanal kontrol jarak jauh
- Recon engine dengan registry >200 tool eksternal + builtin Python fallback

Karena agent-nya benar-benar mengeksekusi command dan request nyata (bukan simulasi), **permission/approval/scope layer adalah komponen paling kritis secara keamanan** di seluruh codebase ini — bukan auth/authz web biasa.

## 2. Dua "Produk" dalam Satu Repo

Ditemukan dua basis kode paralel:

| | Legacy ("H1T4M-AI") | Aktif ("WTH-AI") |
|---|---|---|
| Entry point | `main.py` (root) | `wth-main.py` (root, 1401 baris) |
| Package | `core/`, `agents/` (root), `exploitation/`, `reconnaissance/`, `learning/`, `integration/`, `reporting/` (root), `utils/` (~2000 baris) | `wth_ai/` (242 file, ~26.500 baris) |
| Status | **Tidak dipanggil** oleh Dockerfile/CI/install.sh/entrypoint manapun | Dipanggil langsung oleh `Dockerfile`, `docker/entrypoint.sh`, `install.sh` |
| Dikonfirmasi mati? | Ya — hanya diimpor oleh 4 file test lama, tidak direferensikan jalur produksi manapun | — |

**Catatan penting:** `pyproject.toml` mendeklarasikan entry point resmi `wth-ai = "wth_ai.cli.entry:main"`, tapi `install.sh` memakai `wth-main.py` sebagai launcher dengan komentar eksplisit: *"avoids broken entry imports"* — mengonfirmasi entry point resmi diketahui rusak oleh maintainer sendiri. Ini adalah **documentation/packaging drift** yang signifikan (lihat AUDIT_REPORT.md finding ARCH-02).

Diagram di bawah **hanya membahas jalur aktif (`wth_ai/`)**.

## 3. Peta Direktori (`wth_ai/`)

```
wth_ai/
├── cli/                 CLI commands, REPL, entry point, TUI (4170 baris — terbesar)
├── recon/               Recon engine: registry 200+ tool, planner, runner, builtin modules (4189 baris)
├── agents/              Planner/Executor/Supervisor LLM loop, tool dispatch, grounding firewall (2435 baris)
├── tools/                Tool manifest lama (215 tools) + generic tool registry (1952 baris)
├── llm/                  Provider abstraction (OpenRouter/OpenAI/Anthropic/Google/Groq/Ollama) (1301 baris)
├── jobs/                 Job model/store/queue/worker/manager + PolicyEngine (1105 baris)
├── executor/             Modul uji vuln: SSRF/IDOR/XSS/auth/race (884 baris)
├── gateway/               Daemon RPC (Unix socket) untuk bot & IPC (736 baris)
├── skills/                Skill loader (external + official skill packs)
├── memory/                SQLite FTS conversation/action memory (565 baris)
├── graph/                Asset graph (node/edge domain→subdomain→URL→endpoint→param)
├── brain/                 Hypothesis engine + exploit chain planner
├── terminal/              AgentTerminalBridge — satu-satunya jalur resmi run_shell/http_probe
├── bots/                  Discord & Telegram bot handler
├── mcp/                   MCP client (konsumsi tool eksternal) + MCP server (stdio, 3 tool read-only)
├── rpc/                   ExecuteRpcServer — eksekusi kode Python via Unix socket
├── evidence/              Evidence vault (TTST) dengan redaksi secret
├── permission_manager.py  Gate command per mode (read_only/standard/elevated/full_lab/unrestricted)
├── scope_manager.py       Validasi target in-scope + redaksi secret pada output
├── approval_gate.py / approvals.py   Mekanisme /approve untuk command high-risk
├── shell_runner.py / tool_runner.py  Subprocess wrapper aman (shell=False, argv list)
├── vault.py               Secret vault terenkripsi (Fernet)
├── config.py              Load config user.yaml + .env + profile
├── profiles.py            Preset konfigurasi (default/budget/local/recon-only/bug-bounty/ctf-aggressive)
├── engagement/mode.py     Flag WTH_UNRESTRICTED — "master switch" lintas-modul (lihat AUDIT_REPORT.md)
└── plugins/               Plugin loader (termasuk dari cwd — lihat temuan DATA-... di AUDIT_REPORT.md)
```

## 4. Request / Execution Lifecycle

### 4.1 Jalur CLI interaktif (operator lokal)

```
Operator (terminal)
  │
  ▼
wth-main.py / wth_ai.cli.entry
  │
  ▼
WTHOrchestrator (wth_ai/orchestrator.py)
  │  - parse slash command (/scope, /recon, /plan, /execute, /verify, /report)
  │  - kelola SessionState (permission_mode, scope, approved_tasks)
  │
  ▼
PlannerAgent / ExecutorAgent / SupervisorAgent (wth_ai/agents/)
  │  - panggil LLM provider (wth_ai/llm/, openrouter.py)
  │  - LLM balas dengan tool-call: run_shell / http_probe / scope_set / store_evidence / dst
  │
  ▼
AgentTerminalBridge.dispatch_tool()  (wth_ai/terminal/bridge.py)  ◄── TRUST BOUNDARY UTAMA
  │  [1] PermissionManager.mode_sufficient(min_perm_untuk_actor)
  │  [2] ScopeManager.is_in_scope() / _check_scope_urls()
  │  [3] PermissionManager.check_command()  — allowlist + denylist per mode
  │  [4] jika approval_needed: cek flag `approved`
  │
  ▼
ExecutorRunnerV2 → shell_runner.py (subprocess, shell=False, argv list, timeout, redaksi secret)
  atau
requests.request() langsung (http_probe)
  │
  ▼
TerminalResult disimpan sebagai transcript [TX:...] → EvidenceVault (opsional) → GroundingFirewall
  │
  ▼
ReportBuilderAgent → laporan H1-ready (Markdown/JSON)
```

### 4.2 Jalur Bot (Discord/Telegram — operator jarak jauh)

```
User Discord/Telegram (SIAPA PUN yang bisa chat dengan bot)
  │
  ▼
bots/discord_bot.py atau bots/telegram_bot.py
  │  - cek allowlist channel/chat (DEFAULT KOSONG → fail-open, lihat temuan NET-01)
  │
  ▼
BotSessionManager.get_or_create(platform, user_id)
  │  - membuat WTHOrchestrator BARU per user — SAMA PERSIS dengan sesi CLI penuh
  │
  ▼
(jalur sama seperti 4.1 di atas — orchestrator tidak tahu bedanya CLI vs bot)
```

**Catatan arsitektur kritis:** bot Discord/Telegram tidak punya trust-boundary tambahan dibanding CLI — user chat platform diperlakukan setara operator lokal yang menjalankan CLI. Ini valid HANYA jika channel/chat benar-benar dibatasi ke operator terpercaya, yang defaultnya TIDAK demikian (lihat AUDIT_REPORT.md NET-01).

### 4.3 Jalur Recon Engine (job-based)

```
CLI/RPC: `wth recon run/quick/watch <target>`
  │
  ▼
JobManager.create(type="recon", target=..., meta={"authorized": ...})
  │
  ▼
PolicyEngine.evaluate()  ◄── secara desain harus reject job out-of-policy
  │  (TEMUAN: fungsi ini secara logika SELALU return True — lihat AUDIT_REPORT.md AUTHZ-05)
  │
  ▼
JobWorker → jobs/handlers.py::handler_recon
  │  authorized = meta.get("authorized", True)  ◄── default True bila field tidak diisi (TEMUAN)
  │
  ▼
ReconEngine.run()  (wth_ai/recon/engine.py)
  │  if not authorized and not is_unrestricted(): BLOCK
  │  (TEMUAN: is_unrestricted() default True di semua config Docker resmi)
  │
  ▼
ReconPlanner → ToolRegistry (200+ ToolSpec) → pilih tool sesuai profile+risk_level+allowlist
  │
  ▼
SafeToolRunner (subprocess argv-only, timeout, redaksi secret)  ATAU  Builtin Python module
  │
  ▼
Normalizer → AssetGraph + Evidence → Scoring → recon_report.md
```

## 5. Trust Boundary

```
                         INTERNET
                            │
      ┌─────────────────────┼─────────────────────┐
      │                     │                     │
 Discord/Telegram      Target eksternal      Provider LLM
 (user chat)           (recon/probe/exploit)  (OpenRouter dkk)
      │                     │                     │
      ▼                     ▼                     ▼
┌──────────────────────────────────────────────────────────┐
│                      WTH-AI PROCESS                        │
│                                                              │
│  bots/*  ──► orchestrator ──► agents/* ◄── respons LLM      │
│                    │              │   (berpotensi berisi     │
│                    ▼              │    prompt injection dari  │
│           SessionState/Scope      │    respons HTTP target)   │
│                    │              ▼                          │
│                    └──────► terminal/bridge.py               │
│                                    │                          │
│                     permission_manager / scope_manager /      │
│                          approval_gate  (GATE UTAMA)          │
│                                    │                          │
│                                    ▼                          │
│                     shell_runner / requests (SINK EKSEKUSI)   │
│                                    │                          │
│              vault.py (secret, Fernet-encrypted, chmod 600)   │
│              evidence/ (TTST, redaksi regex)                  │
│              memory/, graph/, jobs/ (SQLite/JSON lokal)        │
└──────────────────────────────────────────────────────────┘
                            │
                            ▼
                   Filesystem lokal (~/.wth-ai/, wth-results/)
```

**Observasi trust boundary paling penting:**
1. **Bot Discord/Telegram → orchestrator**: TIDAK ADA lapisan otorisasi tambahan yang konsisten (NET-01). Chat user = operator penuh.
2. **Respons HTTP target eksternal → context LLM**: dikirim mentah sebagai pesan `role: tool`, tanpa penanda "data observasi, bukan instruksi" di system prompt manapun (AGENT-02 di AUDIT_REPORT.md) — celah *indirect prompt injection*.
3. **`WTH_UNRESTRICTED` env var**: melintasi HAMPIR SEMUA boundary di atas sekaligus (recon authorization, tool risk gate, job policy, human-approval gate) — lihat AUTHZ-01.
4. **`approved` boolean di terminal bridge**: gate final sebelum eksekusi nyata, tapi terbukti bisa jadi "permanent global flag" per sesi (AUTHZ-04), bukan per-command.

## 6. Autentikasi & Otorisasi — Model yang Sebenarnya Berjalan

WTH-AI **tidak** punya model AuthN/AuthZ web klasik (tidak ada user login ke aplikasi web). Model kepercayaannya:

- **CLI**: siapa pun yang punya akses shell ke mesin operator = trusted (wajar untuk CLI tool single-operator).
- **Permission Mode** (`read_only` → `standard` → `elevated` → `full_lab` → `unrestricted`): bukan identity-based, tapi *capability level* yang di-set operator sendiri lewat `/permission`. Tidak ada pemisahan "siapa" yang boleh menaikkan mode — siapa pun yang bisa kirim command ke orchestrator (termasuk lewat bot, lihat NET-01) bisa menaikkan mode sendiri.
- **Scope authorization** (`authorized=yes` per target): **self-declared** oleh siapa pun yang mengontrol sesi — tidak ada verifikasi independen (mis. DNS TXT record, file `.well-known/` di target) bahwa target benar-benar mengizinkan testing. Ini "honor system" yang secara desain bergantung penuh pada operator jujur — valid untuk tool CLI-untuk-diri-sendiri, tapi berbahaya begitu diekspos ke bot multi-user (NET-01) atau bisa dipengaruhi prompt injection (AGENT-02).
- **Approval Gate** (`/approve` untuk command high-risk): dimaksudkan sebagai human-in-the-loop terakhir, tapi terbukti *sticky* untuk seluruh sesi, bukan per-command (AUTHZ-04).

Detail lengkap lihat `SECURITY_AUDIT.md`.

## 7. Integrasi Pihak Ketiga

| Integrasi | Arah | Autentikasi | Catatan |
|---|---|---|---|
| LLM Provider (OpenRouter/OpenAI/Anthropic/Google/Groq/Ollama/9router) | Outbound | API key dari vault | Key disimpan terenkripsi (Fernet) |
| Discord Bot | Inbound (gateway websocket) | Bot token dari vault | Otorisasi user lemah (NET-01) |
| Telegram Bot | Inbound (long-poll/webhook) | Bot token dari vault | Otorisasi user lemah, role hardcoded "owner" |
| MCP client | Outbound (subprocess stdio ke MCP server eksternal) | Tergantung server MCP eksternal | WTH-AI sebagai consumer |
| MCP server (built-in) | Inbound (stdio, dipanggil parent process) | Implisit (proses lokal) | Hanya 3 tool read-only |
| Webhook notifier | Outbound only | N/A | Tidak ditemukan webhook receiver masuk |
| 200+ recon tools eksternal | Local subprocess | N/A | `SafeToolRunner`, argv-only, env disanitasi |

## 8. Yang TIDAK Ada (dan Sengaja Tidak Diaudit Mendalam)

- **Tidak ada web frontend/dashboard** — ini murni CLI + TUI (Textual) + bot chat. `reporting/dashboard.py` di folder legacy tidak dipakai jalur aktif.
- **Tidak ada database server** (Postgres/MySQL) — semua storage lokal: SQLite (`memory/`, `data/h1t4m.db` legacy), file JSON/JSONL (jobs, evidence, reports), file terenkripsi (vault).
- TUI (`wth_ai/cli/tui`) tidak dibaca baris-per-baris secara mendalam (hanya di-grep untuk pola berbahaya) — dicatat sebagai limitation di `AUDIT_REPORT.md` §30.

---
*Dokumen ini adalah hasil audit statis (pembacaan source code), bukan hasil pengujian runtime. Lihat "Coverage & Limitations" di AUDIT_REPORT.md.*
