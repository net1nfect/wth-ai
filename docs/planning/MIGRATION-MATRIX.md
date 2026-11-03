# Migration Matrix

> Gabungan dari 2 spesialis + revisi second-pass critique. Strategy: **KEEP** (tidak berubah), **REFACTOR** (perilaku dipertahankan, implementasi berubah), **REPLACE** (perilaku berubah sengaja), **DEPRECATE** (dihapus/ditinggalkan).

## AI/Agent Domain

| Existing Module | Target Module | Strategy | Risk |
|---|---|---|---|
| `loop/state_machine.py::EngagementStateMachine` | `LoopStateMachine` (rename) — property read-only + `.transition()` mutator tunggal + audit history | REFACTOR | Rendah |
| `loop/engagement_loop.py` (6× `self.sm.state=`) | Panggil `self.sm.transition(...)` | REFACTOR | Rendah-Sedang — `/hunt` harus lulus regresi end-to-end |
| `agents/tool_loop.py::AgentToolLoop` | + `LoopStateMachine`, `LoopBudget`, interrupt-check kill-signal | REFACTOR | Sedang — file bertrafik tinggi |
| `agents/supervisor_dispatch.py` | Fix bug `AssessmentPhase` + mapping tool→state + `ActionIntent` construction | REFACTOR | Rendah (bug fix) → Sedang (ActionIntent) — **WU-001 landing DULU sebelum WU state-machine (dependency edge eksplisit, sama file)** |
| `models.py::AssessmentPhase` | Tidak berubah (fix di caller) | KEEP | Sangat rendah |
| `wth_ai/planning/` (kosong, `__pycache__` saja) | — | DEPRECATE/CLEANUP | Sangat rendah |
| `executor/engine.py` (tidak ada di tree `agentic`) | Referensi desain saja (rate-limit/budget), via branch `cursor/fase6-platform-f3b2` | N/A — bukan migrasi kode | Rendah, JANGAN asumsikan plug-and-play |
| `llm/router.py::LLMProviderRouter` | + `generate(ModelRequest)->ModelResponse` | REFACTOR | Sedang — wajib test parity |
| `llm/providers/base.py` | + dukungan model-catalog | REFACTOR (aditif) | Rendah — default lama (`None`) tetap fallback |
| `agents/__init__.py::BaseLLMAgent._call` | `role` keyword-only wajib | REFACTOR | Rendah setelah audit 6 call-site |
| `agents/verifier.py`, `agents/executor.py` | + `role=` eksplisit | REFACTOR | Sangat rendah |
| `memory/engagement_memory.py` | Absorpsi ke `graph/asset_graph.py` | DEPRECATE (kelas mandiri) | Sedang — migrasi data sesi lama |
| `memory/action_memory.py`, `fts.py`, `learning.py`, `md_context.py` | Tidak berubah struktur, kontrak `context_block()` ditambah | KEEP | Sangat rendah |
| `agents/supervisor.py::_build_context_block` + `orchestrator.py::_assess_loop` | `context/builder.py::ContextBuilder` baru | REFACTOR (ekstraksi) | Sedang — snapshot test konten context |
| `skills/router.py::detect_intent` | + parameter `session_state` opsional | REFACTOR (kecil) | Rendah |
| `slash_commands.py::SlashCommandHandler` | Dihapus | DEPRECATE/REMOVE | Sangat rendah — dead code terkonfirmasi |
| `platform/registry.py::PlatformRegistry` | Single source of truth + test sinkronisasi | REFACTOR | Rendah |
| `cli/slash_router.py` | Serap tier-1 hardcoded meta command | REFACTOR | Rendah-Sedang |
| `models.py::Evidence` + `executor/evidence.py::EvidencePipeline` | Output jadi `list[Claim]`, **turun peran jadi FALLBACK-ONLY**, TIDAK LAGI tulis `EvidenceVault` sendiri | REFACTOR | Sedang — bug linkage id diperbaiki + perubahan peran (revisi critique) |
| `evidence/vault.py::EvidenceVault` | Tetap exhibit store, HANYA broker yang panggil `.store()`, + field `correlation_id` | KEEP struktur + REFACTOR caller | Rendah |
| `agents/grounding.py::GroundingFirewall` | + `validate_claim(Claim)` | REFACTOR (aditif) | Rendah |
| `findings_db.py::update_finding_state` | + transition guard table | REFACTOR | Rendah-Sedang |
| `agents/report_builder.py::build` | Validasi `Claim` sebelum tulis laporan | REFACTOR | Sedang |
| `jobs/model.py::JOB_TYPES` | + `"engagement"` | REFACTOR (aditif) | Sedang, P2 |
| `wth_ai/openrouter.py::OpenRouterClient` | — | DEPRECATE (verifikasi ulang tak ada import aktif) | Rendah |
| `orchestrator.py:781-784,806-808`, `loop/engagement_loop.py:110-112` (`evidence_vault.store_from_executor` call site) | Dihapus — broker auto-store menggantikan | **DEPRECATE (WAJIB, bagian rollout broker — bukan opsional)** | Sedang — kalau tidak dihapus serentak, duplikasi evidence di laporan |
| `executor.py:145`, `executor/vuln/{idor,ssrf,xss}.py` (`request_pairs` producer) | Dihapus — broker auto-capture menggantikan | **DEPRECATE (WAJIB, bagian rollout broker)** | Sedang |

## Security/Platform Domain

| Existing Module | Target Module | Strategy | Risk |
|---|---|---|---|
| `wth_ai/permission_manager.py` | `policy/capability.py` (baru) + `permission_manager.py` adapter tipis | REFACTOR | Sedang — return-dict dipertahankan |
| `wth_ai/scope_manager.py` + `models.py::Scope` | `engagement/model.py::Engagement/TargetScope` (baru); `Scope` jadi derived view | REFACTOR (3 fase) | Sedang — `SessionState.scope` tetap ada selama transisi |
| `wth_ai/approval_gate.py` | Fungsinya diserap `ApprovalRegistry` | DEPRECATE | Rendah — usage terbatas |
| `wth_ai/approvals.py::ApprovalRegistry` | `policy/approval.py` (extend: action_hash, status CONSUMED/KILLED) | REFACTOR (aditif) | Rendah |
| `SupervisorContext.approved_tasks` / `WTHOrchestrator._approved_tasks` | Dihapus, digantikan lookup `ApprovalRegistry` by action_hash. **`context.tainted` ditambahkan ke `SupervisorContext` yang sama (revisi lokasi, bukan `AgentToolLoop`)** | REPLACE | Sedang — 2 loop eksekusi harus diubah bersamaan |
| `wth_ai/terminal/bridge.py::AgentTerminalBridge` | Facade tipis di atas `execution/broker.py::ExecutionBroker` | REFACTOR | Sedang-Tinggi — hot path, `TerminalResult` dipertahankan persis |
| `wth_ai/jobs/policy.py::PolicyEngine` | Adapter atas core `policy/engine.py::evaluate_intent()` | REFACTOR | Rendah — coarse pre-filter, blast radius sempit |
| `wth_ai/rpc/execute_server.py::ExecuteRpcServer.execute()` | Panggil `ExecutionBroker` dgn `ActionIntent` dari `req["code"]` asli | REFACTOR | Sedang |
| `agents/executor.py::execute_module()`, `executor/vuln/*.py` | **Addendum P0 sempit**: guard `is_in_scope()`+`evaluate_intent()` sinkron dulu; **P1**: integrasi broker penuh (scope diperlebar dari draft awal) | REFACTOR | **Tinggi P0 addendum** (AGENT-02 confirmed live) → Sedang P1 |
| `wth_ai/recon/engine.py`, `registry.py`, `planner.py`, `tools/*` | Konsumsi `evaluate_intent()`/broker di titik shell-out nyata | REFACTOR (bertahap P1/P2) | Tinggi — permukaan besar, JANGAN blokir P0 |
| `wth_ai/engagement/mode.py` (`is_unrestricted`/`WTH_UNRESTRICTED`) | Shim kompatibilitas (compose 3 dimensi baru), lalu dihapus sbg runtime switch | REPLACE (perilaku) | Tinggi — 6+ call-site pindah bersamaan |
| `wth_ai/profiles.py` (`bug-bounty`/`ctf-aggressive`) | Set `PermissionProfile` ceiling + rate saran, TIDAK BOLEH set otorisasi/approval bypass | REFACTOR | Sedang |
| `wth_ai/config.py` (`SafetyConfig.default_permission="full_lab"`, `auto_approve_unrestricted=True`) | Default aman (`read_only`, `False`) | REFACTOR | Rendah — rilis bersamaan dgn ADR-012 |
| `Dockerfile`/`docker-compose.yaml`/`.env.example` (`WTH_UNRESTRICTED=1`) | Default aman per-dimensi | REFACTOR | Rendah (config-only), WAJIB bersamaan dgn kode |
| `wth_ai/bots/auth.py`, `telegram_bot.py`, `discord_bot.py` | Fail-closed allowlist, role resolution konsisten | REFACTOR | Sedang — breaking change keamanan, WAJIB didokumentasikan |
| `wth_ai/plugins/loader.py::discover_plugin_dirs()` | Hapus entry CWD-relative default | REFACTOR | Rendah |
| `wth_ai/shell_runner.py` | KEEP core + `start_new_session=True` + fix kill-on-timeout streaming | REFACTOR (surgical) | Rendah-Sedang |
| `wth_ai/executor/sandbox.py::Sandbox` | KEEP + `resource.setrlimit` | REFACTOR | Rendah |
| `wth_ai/events.py::EventBus` | KEEP + `AuditLogSink` subscriber + `EVENT_TYPES` baru (termasuk `invalid_state_transition`, `tainted` kolom eksplisit) | REFACTOR (aditif) | Rendah |
| — (baru) | `wth_ai/engagement/model.py`, `engagement/store.py` (SQLite) | NEW | Sedang |
| — (baru) | `wth_ai/execution/broker.py`, `execution/registry.py` (RunningActionRegistry) | NEW | Sedang |
| — (baru) | `wth_ai/policy/engine.py`, `capability.py`, `approval.py` | NEW | Sedang |
| — (baru) | `wth_ai/audit/sink.py`, `data/audit.sqlite` | NEW | Rendah |
| — (baru) | `wth_ai/killswitch.py` | NEW | Rendah-Sedang |
| `jobs/worker.py::WorkerEngine._run_job()` | Fix `ThreadPoolExecutor` timeout kosmetik (`shutdown(wait=False, cancel_futures=True)` tanpa `with`) | REFACTOR (surgical) | Rendah |

## Legacy/Infra (tidak kontroversial — TANPA ADR terpisah, lihat `docs/adr/README.md`)

| Existing | Target | Strategy | Risk |
|---|---|---|---|
| `core/`, `agents/` root, `exploitation/`, `reconnaissance/`, `learning/`, `integration/`, `reporting/` root, `utils/`, `main.py` | — | DEPRECATE/DELETE (+ 4 test file terkait) | Rendah — orphaned terkonfirmasi |
| `pyproject.toml` entry point (`wth_ai.cli.entry:main`) | Perbaiki jadi mandiri berfungsi | REFACTOR | Sedang — packaging |
| Tidak ada `.dockerignore` | Tambahkan | NEW | Rendah |
| CI tanpa security scanning | + `pip-audit`/`bandit` | NEW | Rendah |
