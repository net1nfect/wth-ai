# 01 — Gap Analysis

> Existing state per `00-existing-architecture.md` dan `SECURITY_AUDIT.md`. Target per ADR-001..013 (`docs/adr/`). Priority: P0 = fondasional/blocking, P1 = penting, P2 = lanjutan/hardening.

## Tabel Utama

| # | Area | Existing | Target | Gap | Priority |
|---|---|---|---|---|---|
| 1 | Otorisasi scope | `Scope` value object mutable tunggal, tanpa id/expiry/status, overwrite total tiap `set_scope()` | `Engagement` entity dengan FSM (DRAFT→VALIDATING→READY→ACTIVE↔PAUSED→COMPLETED/ABORTED/EXPIRED), enforcement fetch-fresh di broker | Tidak ada lifecycle, tidak ada history, satu target per sesi | **P0/P1** |
| 2 | Command allowlist | `MODE_TOOLS` flat set per `PermissionMode`, substring match, `bash/sh/zsh` di-skip | `Capability`/`PermissionProfile` granular (argv exact-match, risk, approval_mode per tool) | Bypass ganda (AUTHZ-02), tidak ada nuansa risk per-tool | **P0** |
| 3 | Job admission policy | `PolicyEngine.evaluate()` dead-fallthrough, selalu `True` | `evaluate_intent()` 6-layer, tidak ada jalur ke ALLOW tanpa rule eksplisit | Fail-open struktural (AUTHZ-05) | **P0** |
| 4 | Master-switch bypass | `WTH_UNRESTRICTED` 1 env var → 6+ call-site (scope, tool-risk, job-policy, auto-approve, 2 preset profile) | 3 dimensi independen (capability-ceiling, scope-authorization, human-approval) | Satu boolean mematikan >1 kontrol sekaligus (dilarang eksplisit pemilik produk) | **P0** |
| 5 | Approval gate | 4 mekanisme paralel (`approved_tasks` set permanen-per-sesi, `_approved_tasks`, `ApprovalGate`, `ApprovalRegistry`), `http_probe` TANPA gate sama sekali | 1 `ApprovalRegistry` action-hash (seluruh argv/URL/body), single-use, dipanggil KEDUA loop eksekusi | Approval tidak terikat aksi spesifik; http_probe unguarded | **P0** |
| 6 | Eksekusi subprocess/HTTP | 3 jalur independen (`AgentTerminalBridge`, `ExecuteRpcServer`, `ReconEngine`), masing-masing enforcement beda | `ExecutionBroker.execute(ActionIntent)` — satu chokepoint | Drift antar jalur (NET-02, dan `ReconEngine` tidak pernah lewat `PermissionManager`) | **P0/P1** |
| 7 | LLM self-escalation | `scope_set`(`authorized` default True)/`permission_set` LLM-callable tanpa gate | `propose_target` (draft-only), `authorize_engagement` DIHAPUS dari tool LLM, `permission_set`→selalu REQUIRE_APPROVAL | AGENT-01: LLM bisa self-authorize 1 tool-call | **P0** |
| 8 | Indirect prompt injection | Tidak ada penanda trust-level konten target di manapun | `context.tainted` (state loop, broker hard-force eskalasi approval) | Structural isolation belum ada sama sekali | **P0/P1** |
| 9 | Bot access control | Allowlist default kosong=fail-open, Telegram hardcode `role="owner"` | Fail-closed allowlist, role resolution dari config, cap `PermissionProfile` per sesi bot | NET-01 CRITICAL | **P0** |
| 10 | Loop state | 3 loop, hanya 1 (`LoopState`) punya guard formal, dan guard itu **tidak pernah dipanggil** (vestigial) | `EngagementStateMachine` read-only property + `.transition()` sbg satu-satunya mutator, dipaksa via broker fetch-fresh (bukan cuma Python encapsulation) | Enforcement-by-definition terbukti gagal; perlu enforcement-by-consumption | **P0** |
| 11 | `AssessmentPhase` bug | `supervisor_dispatch.py` reference 3 anggota enum yang tidak ada → AttributeError tertelan | Fix pemetaan tool→state yang benar, terintegrasi dengan #10 | Silent failure dengan efek samping sudah terjadi | **P0** |
| 12 | Role-wiring model | `BaseLLMAgent._call()` abaikan param `model`/`backup`; verifier/executor diam-diam pakai role planner | `role` jadi keyword-only wajib, audit 6 call-site | Config `/model set` tidak efektif untuk 2 dari 5 agent class | **P0** |
| 13 | Loop budget | Cuma `max_iters` (iterasi doang), hard-cutoff buang semua kerja saat habis | `LoopBudget` (steps/wall/cost/failures/no-progress), graceful degradation (forced summary call) | Tidak ada cost/no-progress tracking meski sinyalnya sudah dihitung (`cov_before` unused) | **P1** |
| 14 | Task representation | `ExecutorTask` flat, tanpa dependency/priority field | `+priority:int`, `+depends_on:list[str]` (advisory sort, **BUKAN DAG** — ditolak eksplisit) | Field kosmetik (`stop_condition`) tidak pernah dibaca | **P1** |
| 15 | Model request/response | 3 bentuk berbeda (tuple/dict-mentah/envelope) hidup berdampingan | `ModelRequest`/`ModelResponse` kanonik, `generate()` baru, 3 lama jadi adapter | Tidak ada fondasi untuk cost-tracking (usage) | **P1** |
| 16 | Model capability data | `get_context_length()`/`get_rate_limit_info()` selalu `None`, tidak ada model spesifik | `model_catalog.yaml` seed + cache live dari `list_models()` | Stub tanpa data nyata | **P1** |
| 17 | Model routing otomatis | Tidak ada (role-based statis) | **TETAP tidak ada** — keputusan sadar (ADR), bukan gap yang perlu ditutup | N/A — role-based sudah cukup | Ditolak eksplisit |
| 18 | Memory/context | 5 mekanisme independen, 2 context-builder beda kualitas dipanggil frekuensi sama | Kontrak tipis bersama (`context_block()`+`char_budget`), `EngagementMemory`→merge `AssetGraph` | Duplikasi data (EngagementMemory vs AssetGraph), tidak ada budget lintas-sumber | **P1** |
| 19 | Evidence/Claim | 3 struktur terpisah (`Evidence`/`EvidenceVault`/`evidence_schema`), `EvidencePipeline` buang `Evidence.id` | `Claim` schema pemersatu (REUSE `FindingState`), `ClaimStore`, `GroundingFirewall.validate_claim()` | Linkage Finding↔Evidence nol untuk 1 jalur; 5/8 `FindingState` nganggur | **P1** |
| 20 | Report grounding | `ReportBuilderAgent.build()` `return raw` tanpa grounding | Validasi `Claim` sebelum tulis laporan final | AGENT-03: celah defense-in-depth di titik paling visible (laporan ke client) | **P1** |
| 21 | Asset graph coverage | 5/7 `EdgeType` tidak pernah dipakai, graph kedua duplikat di `recon/engine.py` | Aktivasi `REQUIRES_AUTH` dkk, `EngagementMemory` data pindah jadi node attribute | Skema sudah ada, implementasi tidak menutupnya | **P1/P2** |
| 22 | Audit trail | `EventBus` non-durable (ring-buffer 200, mati bareng proses) + transcript per-command + evidence per-finding, TERPISAH | `audit_log` SQLite durable (integrity-hash chain) + `AuditLogSink` subscriber `EventBus` | Tidak ada satu trail terpadu untuk lifecycle event penuh | **P1** |
| 23 | Kill switch | `orchestrator.shutdown()` cooperative-only, tidak sentuh subprocess/thread blocking | `RunningActionRegistry` (`os.killpg`), flag persisten, 3 permukaan (job-thread/subprocess/approval-pending) | Tidak ada true emergency-stop; bug proses-leak-saat-timeout independen tapi terkait | **P1** |
| 24 | Credential ke target | `Scope.test_accounts` informational, tidak pernah di-resolve nyata | Broker resolve `vault:` reference di titik eksekusi, `allow_credential_injection` capability gate | Kapabilitas baru secara fungsional, bukan cuma integrasi | **P1/P2** |
| 25 | Sandbox `run_shell` | OS-level (non-root, `Sandbox` cwd-isolation sebagian, env allowlist) — SUDAH cukup baik | +`resource.setrlimit`, `start_new_session=True` utk process-group kill | **Container/microVM DITOLAK eksplisit** (ADR-013) — bukan gap yang perlu container | **P2** |
| 26 | Recon engine policy | Tidak pernah lewat `PermissionManager`/broker, auth default `True` bila metadata kosong | Bertahap konsumsi `evaluate_intent()`/broker di titik shell-out nyata | Permukaan besar (banyak adapter di `recon/tools/`), tidak boleh blokir P0 | **P1/P2** |
| 27 | SSRF defense-in-depth | Builtin HTTP fetcher (`recon/http.py`) tanpa validasi IP/redirect tujuan | Resolusi DNS eksplisit + cek IP privat/loopback + validasi ulang tiap hop redirect | Gap non-exploitable langsung tapi tanpa guard | **P2** |
| 28 | Plugin loading | `.wth/plugins/` relatif-CWD, `exec_module()` tanpa sandbox/konfirmasi | Hapus default CWD-relative, wajib opt-in eksplisit | DATA-01 CRITICAL (local code execution) | **P0/P1** |
| 29 | Legacy code | ~2000 baris orphaned (`core/`, `agents/` root, dll), masih diuji 4 test file CI | Hapus/archive, hapus test terkait | False-confidence hijau CI untuk kode tidak pernah shipped | **P2** |
| 30 | Entry point resmi | `pyproject.toml` broken, `wth-main.py` jadi workaround tidak terdokumentasi | Perbaiki `wth_ai.cli.entry:main` jadi entry point mandiri berfungsi | Packaging `pip install wth-ai` rusak untuk cara resmi | **P2** |
| 31 | Slash-command registry | 3 sistem paralel (1 dead code) | `PlatformRegistry` jadi single source of truth, hapus `SlashCommandHandler` | Potensi drift command yang diiklankan vs routable | **P2** |
| 32 | Docker/CI hygiene | Tidak ada `.dockerignore`, CI tanpa security scanning | `.dockerignore`, tambah `pip-audit`/`bandit` ke CI | INFRA-01, CI-01 (bukan CRITICAL tapi mudah diperbaiki) | **P2** |
| 33 | Dependency injection kredensial vault→env | `write_env_file()` tulis plaintext tanpa `secure_file()` | Tambah `secure_file()` panggilan yang hilang | DATA-04, fix satu baris | **P2** |

## Backlog PRD — Structured Spec Ringkas (bukan full PRD, sesuai instruksi "jangan buat 31 dokumen kosong")

Area berikut punya keputusan/skema yang cukup jelas dari riset 2 spesialis untuk dijadikan referensi implementasi, tapi tidak mendapat PRD penuh di P0/P1 — dicatat di sini sebagai spesifikasi ringkas (problem → keputusan → skema inti) untuk dikembangkan jadi PRD penuh saat gilirannya tiba di roadmap.

### Sandbox Runtime
**Problem**: `run_shell` yang lolos policy jalan di proses/host yang sama, batasan cuma OS-level (chmod, allowlist env). **Decision**: OS-level + broker cukup untuk v1 (ADR-013) — TIDAK container. **Skema**: `executor/sandbox.py::Sandbox` (existing) + `resource.setrlimit` (CPU/memory/nofile) + `Popen(start_new_session=True)` untuk process-group kill oleh `RunningActionRegistry`. **Trigger revisit**: multi-tenant hosting atau eksekusi kode LLM-generated tanpa review manusia.

### Network Control
**Problem**: builtin HTTP fetcher tanpa validasi IP privat/redirect (`recon/http.py`), port internal docker-compose bind ke `0.0.0.0`. **Decision**: defense-in-depth tambahan, bukan redesign — resolusi DNS eksplisit sebelum connect + validasi tiap hop. **Skema**: wrapper `safe_fetch()` di atas `requests` yang reject IP privat/link-local/metadata kecuali `Engagement.targets` eksplisit menandai `asset_type="local"`.

### Recon Engine Integration dengan Broker
**Problem**: `ReconEngine`/`recon/tools/*` adalah jalur eksekusi ketiga, tidak pernah lewat `PermissionManager`/broker. **Decision**: migrasi bertahap (P1/P2) — permukaan besar (`recon/tools/adapters.py`/`builtin.py`), jangan blokir P0. **Skema**: tiap tool-adapter panggil `evaluate_intent()` minimal (bukan full broker dulu) sebelum shell-out.

### Surface/Attack-Surface Knowledge (AssetGraph lanjutan)
**Problem**: 5/7 edge type nganggur, `EngagementMemory` duplikat. **Decision**: sudah dicakup gap #21/#18 di atas — aktivasi bertahap seiring `ContextBuilder` unifikasi.

### Hypothesis Engine
**Status**: `HypothesisEngine` (`brain/hypothesis_engine.py`) sudah cukup baik (rule-based, priority-sorted, jalan sebelum LLM). **Decision**: KEEP apa adanya, tidak ada perubahan struktural direkomendasikan di P0/P1. Nilai tambah: hasilnya jadi `Claim` dengan `source="rule:hypothesis_engine"` (lihat PRD-012).

### Verification Engine
**Status**: `VerifierAgent` (`agents/verifier.py`) sudah reachable, `verify_offline()` fallback rule-based untuk test mode. **Decision**: perluasan lewat `Claim.verification_status` (PRD-012) — `VerifierAgent` jadi satu-satunya yang boleh menaikkan status ke `VERIFIED`/`FALSE_POSITIVE`, bukan komponen baru.

### Findings Triage
**Status**: `FindingsDB` fungsional tapi namanya menyesatkan (tidak ada DB sungguhan) dan `update_finding_state()` tanpa transition guard. **Decision**: tambah transition table (pola sama seperti `LoopState.TRANSITIONS`) di P2 — WU tersendiri, dependency ke unifikasi `Claim`/`FindingState` (PRD-012).

### Model Router (otomatis berbasis kompleksitas)
**Decision final: DITOLAK** (lihat ADR-006 AI/Agent domain). Tidak ada spec lanjutan — role-based statis sudah dianggap cukup, alasan lengkap di ADR.

### Vault/Secrets Management
**Status**: `vault.py` sudah solid (Fernet, chmod 600). **Decision**: KEEP sepenuhnya, hanya integrasi baru ke broker untuk kredensial target (gap #24) — lihat PRD-011 (Execution Broker) bagian credential injection.

### Prompt Injection Defense
**Status**: sudah dicakup penuh sebagai bagian PRD-006 (Approval System)/PRD-011 (Execution Broker) via `context.tainted` — tidak perlu PRD terpisah, ini cross-cutting concern yang hidup di titik broker.

### Event System
**Status**: `EventBus` sudah solid untuk pub/sub in-process. **Decision**: perluasan aditif (`AuditLogSink`, `EVENT_TYPES` baru) — dicakup PRD-006/PRD-011 bagian audit, bukan PRD event-system terpisah.

### Observability
**Problem**: belum ada metrics/tracing terpusat di luar `EventBus`+log file. **Decision**: P2/P3, di luar cakupan blueprint ini — catat sebagai technical debt jangka panjang, bukan blocking untuk fix keamanan P0.

### Session/Checkpoint
**Status**: `session_store.py` sudah berfungsi (resume bekerja). **Decision**: perbaikan minor (file-locking, versioning) adalah P2, bukan gap fondasional — cukup dicatat, tidak perlu PRD.

### Reporting
**Status**: `ReportBuilderAgent`/`reporting/report_builder.py` berfungsi. **Decision**: satu-satunya perubahan struktural (grounding via `Claim` sebelum tulis laporan) sudah dicakup PRD-012 — tidak perlu PRD reporting terpisah.

### Operator Controls
**Problem**: kill-switch belum ada (gap #23). **Decision**: dicakup PRD-011 (Execution Broker) — kill-switch adalah kapabilitas broker, bukan subsistem berdiri sendiri.

### Testing / Security Invariants
**Decision**: setiap PRD P0 (§docs/prd/) WAJIB menyertakan test invariant di acceptance criteria sendiri (pola sudah ditetapkan 2 spesialis: "tidak ada `return True` fallthrough", "test matrix permission×unrestricted×approved"). Tidak perlu PRD testing terpisah — testing adalah bagian tiap WU, bukan subsistem.

### Deployment
**Problem**: `WTH_UNRESTRICTED=1` default di semua config Docker resmi (gap #4). **Decision**: perbaikan config mengikuti WU-001 (pecah 3 dimensi) — bukan PRD deployment terpisah, cukup item di `MIGRATION-MATRIX.md`.
