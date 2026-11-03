# WTH-AI — Master Architecture Document

> Dokumen sentral blueprint arsitektur target. Disusun dari audit keamanan menyeluruh (`AUDIT_REPORT.md`/`SECURITY_AUDIT.md`/`ARCHITECTURE.md`) + riset arsitektur (`docs/planning/00-existing-architecture.md`) + 2 spesialis desain (AI/Agent; Security/Platform) yang saling critique lewat second-pass, direkonsiliasi main agent. Detail lengkap tiap area ada di dokumen satelit yang dirujuk di tiap section — dokumen ini adalah peta, bukan pengganti detailnya.

## 1. Executive Summary

WTH-AI adalah CLI conversational agent otonom untuk authorized security testing — LLM planner/executor yang benar-benar mengeksekusi shell command dan HTTP request nyata terhadap target. Audit keamanan menyeluruh menemukan pola sistemik: guardrail yang didokumentasikan sebagai "hukum mutlak" (`AGENT.md`) punya celah bypass material di banyak lapisan independen, berpusat pada satu anti-pattern arsitektural — `WTH_UNRESTRICTED`, satu boolean yang mematikan 6+ kontrol berbeda sekaligus, default aktif di semua config deployment resmi. Blueprint ini merancang arsitektur target yang secara STRUKTURAL tidak bisa mengulang pola ini: tiga dimensi kontrol (capability, scope-authorization, human-approval) dipisah permanen, Execution Broker jadi chokepoint tunggal, Policy Engine fail-closed by construction (bukan by convention).

Riset arsitektur (di luar temuan keamanan) juga menemukan gap desain nyata: 3 loop eksekusi paralel tanpa state-machine yang benar-benar ditegakkan, 4 mekanisme approval independen, 5 jalur eksekusi subprocess/HTTP independen, model role-wiring yang diam-diam salah untuk 2 dari 5 agent class, dan evidence/finding linkage yang sebagian hilang total (bukan cuma lemah).

## 2. Product Vision

Conversational Autonomous Red Teaming CLI — natural-language interface, context-aware, long-running engagement-aware, realtime tool execution, iterative reasoning, evidence-driven, no hallucinated findings, permission-aware, scope-aware, multi-provider LLM, resumable session. Prinsip desain wajib: **LLM PROPOSES, POLICY DECIDES, EXECUTOR EXECUTES, EVIDENCE PROVES**.

## 3. Design Principles

1. AI bukan source of authority — setiap `ActionIntent` yang diajukan LLM melewati Policy Engine deterministik sebelum eksekusi.
2. Fail-closed by construction, bukan by convention — `evaluate_intent()` tidak punya jalur kode menuju ALLOW tanpa rule eksplisit (ADR-010).
3. Tidak ada satu switch yang mematikan >1 dimensi kontrol (ADR-012).
4. State machine ditegakkan di titik konsumsi, bukan hanya di titik definisi — pelajaran langsung dari `LoopState` yang vestigial (ADR-011).
5. Migrasi inkremental — KEEP komponen yang sudah benar (`shell_runner`, `vault.py`, `BaseLLMProvider`), REFACTOR yang bisa di-adapter, REPLACE hanya yang genuinely broken secara struktural.
6. Jangan overengineer — DAG task, model-routing otomatis, dan container sandbox semua DITOLAK eksplisit karena tidak ada problem nyata yang butuh itu (ADR-002, ADR-006, ADR-013).

## 4. Existing Architecture

Lihat `docs/planning/00-existing-architecture.md` untuk detail penuh dengan evidence file:line. Ringkasan poin kritis: entry point resmi broken (workaround `wth-main.py` tidak terdokumentasi), 4 tingkat dispatch command, 3 loop eksekusi (`AgentToolLoop`/`SupervisorAgent`/`EngagementLoop`), state machine yang ada (`LoopState`) terbukti vestigial (guard tidak pernah dipanggil), bug `AssessmentPhase` (enum salah, silent AttributeError), role-wiring model rusak untuk verifier/executor, `Scope` value object tanpa lifecycle, `WTH_UNRESTRICTED` master-switch, 4 mekanisme approval paralel, `http_probe` tanpa approval gate sama sekali, 5 jalur eksekusi subprocess independen (dikonfirmasi second-pass critique: termasuk `executor/vuln/ssrf.py` yang `requests.get()` langsung).

## 5. Target Architecture

```
User/Bot → Intent Router (SkillRouter, session-aware) → Agent Runtime (SupervisorAgent/AgentToolLoop)
  → ActionIntent (LLM PROPOSES) → ExecutionBroker
      → Engagement fetch-fresh → PolicyEngine (6-layer, fail-closed) → ApprovalGate (action-hash)
      → RunningActionRegistry → shell_runner/requests (EXECUTOR EXECUTES)
      → EvidenceVault + ClaimStore + audit_log (EVIDENCE PROVES)
```

Lihat `docs/architecture/FLOWS.md` untuk 12 diagram alur detail, `TRUST-BOUNDARIES.md` untuk zona kepercayaan.

## 6. Component Architecture

Lihat `docs/architecture/COMPONENTS.md` — tabel lengkap Control Plane, Execution Plane, Agent Runtime, Data/Evidence, Interfaces, dengan status KEEP/REFACTOR/REPLACE/DEPRECATE per komponen.

## 7. Control Plane

`Engagement`/`EngagementStore`, `PolicyEngine`, `Capability`/`PermissionProfile`, `ApprovalRegistry` (diperluas), `KillSwitch`. Semua keputusan izin terpusat di sini — Execution Plane TIDAK PERNAH membuat keputusan izin sendiri, hanya menegakkan keputusan yang sudah dibuat Control Plane.

## 8. Agent Runtime

`SupervisorAgent` (entry point single-turn) → `AgentToolLoop` (ReAct, dominan) dengan `LoopBudget`+`LoopStateMachine`+kill-signal check baru. `PlannerAgent` (Hunter→Critic→Feasibility) + `brain/*` (rule-based hypothesis, jalan sebelum LLM). `ExecutorAgent`/`executor/vuln/*` — WAJIB direfactor memanggil broker (temuan P0 baru dari critique: `ssrf.py` bypass total hari ini). `VerifierAgent` — satu-satunya yang boleh menaikkan `Claim`/`Finding` ke `VERIFIED`. `ReportBuilderAgent` — validasi grounding sebelum tulis laporan final (menutup AGENT-03).

## 9. Loop Engine

Lihat `docs/architecture/STATE-MACHINES.md` §1-2,7 dan PRD-008. `LoopStateMachine` (rename dari `EngagementStateMachine`, hindari tabrakan nama dengan entity `Engagement` otorisasi baru) — enforcement-by-construction: `state` read-only property, `.transition()` satu-satunya mutator dengan audit history + `InvalidTransitionError` di mode strict. Dipakai `AgentToolLoop` (bukan cuma `EngagementLoop`/`HuntLoop`) — sekaligus jadi titik fix bug `AssessmentPhase`.

## 10. Planner

`PlannerAgent.plan()` (JSON mode / fallback teks 3-tahap) tetap dipertahankan strukturnya. Tambahan: `ClaimStore.create_claim_from_hypothesis()` dipanggil di sini (saat PLANNING), bukan post-hoc — `claim_id` mengalir ke `ActionIntent.claim_ids` sebelum broker eksekusi (revisi sequencing pasca critique, lihat `DATA-MODEL.md` §5.1).

## 11. Task Graph

**DITOLAK secara eksplisit** (ADR-002) — flat list + `priority:int` + `depends_on:list[str]` advisory sort. Tidak ada konsumen nyata yang butuh percabangan dependency kompleks. `stop_condition`/`success_criteria` (field dead lama) di-WIRE ke `EvidenceVault.validate_schema()` yang sudah ada.

## 12. Execution Plane

`ExecutionBroker.execute(ActionIntent, BrokerContext) -> ExecutionResult` — satu-satunya pemanggil primitive subprocess/HTTP/code-exec. Lihat `docs/architecture/INTERFACES.md` untuk signature lengkap, ADR-008 untuk rasional.

## 13. Permission

`Capability`/`PermissionProfile` granular menggantikan `MODE_TOOLS` flat, `PermissionMode` (5 nilai) di-KEEP sebagai ceiling label. Fix bug ganda `check_command()` (interpreter-skip, substring match) — lihat ADR-009, PRD-005.

## 14. Scope

`Engagement`/`TargetScope` menggantikan `Scope` value object tunggal. Migrasi 3 fase (compat shim → call-site baru → pensiun `Scope`). Lihat ADR-011, PRD-003/PRD-004.

## 15. Approval

Satu `ApprovalRegistry` action-hash (seluruh argv/URL/body) menggantikan 4 mekanisme paralel. Single-use + `StandingApproval` eksplisit sebagai escape-hatch terbatas (tidak pernah untuk risk HIGH/PROHIBITED). Lihat PRD-006.

## 16. Model Layer

`ModelRequest`/`ModelResponse` kanonik via `generate()` baru (strangler-fig atas 3 method lama). Fix role-wiring bug (`_call()` mengabaikan `model`/`backup`). Model capability registry diisi data nyata. **Model routing otomatis DITOLAK** (ADR-006). Lihat PRD-013.

## 17. Tool Layer

`ACTOR_TOOLS` (filtering per-actor) di-KEEP. `tool_registry.py::TOOL_DEFINITIONS` direvisi: `authorize_engagement` DIHAPUS dari tool LLM-callable, `scope_set`→`propose_target` (draft-only), `permission_set`→selalu `REQUIRE_APPROVAL`. Lihat PRD-010.

## 18. Memory

5 mekanisme tetap terpisah + kontrak `context_block()`/`char_budget` bersama, `EngagementMemory` merge ke `AssetGraph`. `ContextBuilder` satukan 2 jalur (chat vs `/plan`). Lihat ADR-004.

## 19. Evidence

`Claim` sebagai layer pemersatu sitasi (REUSE `FindingState`). Sequencing: dibuat saat PLANNING, broker APPEND evidence_refs (bukan create baru). **Kritis** (temuan critique): `EvidenceVault.store()` HANYA boleh dipanggil broker pasca-rollout — 7 call-site lama (`request_pairs`/`store_from_executor`) WAJIB dihapus untuk mencegah duplikasi. `EVD-` prefix pensiun. Lihat DATA-MODEL.md §5, PRD-012.

## 20. Findings

`FindingState` (8 nilai, REUSE) dengan transition guard baru (pola sama `LoopStateMachine`). Hanya `VerifierAgent` boleh set `VERIFIED`/`FALSE_POSITIVE`, dengan bobot lebih untuk `Claim.captured_via="broker_execution:*"`.

## 21. Recon

`ReconEngine`/registry ≥200 tool — KEEP struktur (sudah solid: `SafeToolRunner`, builtin fallback). Migrasi bertahap P1/P2 ke broker (permukaan besar, tidak boleh blokir P0). SSRF defense-in-depth (`recon/http.py`) sebagai hardening terpisah, P2.

## 22. Realtime Event Flow

`EventBus` (in-process, KEEP) + `AuditLogSink` baru (durable, SQLite, sinkron untuk risk≥MEDIUM). "Realtime" recon tetap polling berlapis (job queue SQLite yang genuinely durable) — tidak diubah jadi event-driven murni (ADR-005), tapi `/hunt` dapat opsi job type `"engagement"` untuk async/resumable (P2).

## 23. CLI

3 registry slash-command konsolidasi jadi `PlatformRegistry` (single source of truth) + `SlashRouter`. `SkillRouter.detect_intent()` dapat fallback session-aware (bukan classifier baru — perbaikan satu kondisi). TUI tetap alternate renderer, tidak ada perubahan jalur dispatch.

## 24. Data Model

Lihat `docs/architecture/DATA-MODEL.md` — skema lengkap `ActionIntent`, `BrokerContext`, `Engagement`, `Capability`/`PermissionProfile`, `Claim`, `ModelRequest`/`ModelResponse`, `LoopBudget`, `ExecutorTask` (field baru), `ExecutionResult`, `audit_log`, dengan relasi.

## 25. State Machines

Lihat `docs/architecture/STATE-MACHINES.md` — `Engagement` (authorization lifecycle), `LoopStateMachine` (workflow phase), Job status, `Claim.verification_status`, Approval status, plus interrupt-point design untuk kill-signal.

## 26. Security Invariants

14 invariant, tiap satu punya mekanisme penegak + bug yang diperbaiki. Ringkasan (detail penuh di `SECURITY_AUDIT.md` dan transcript spesialis): (1) no execution tanpa Engagement ACTIVE, (2) policy default DENY struktural, (3) tidak ada 1 boolean lintas-dimensi, (4) denylist absolut argv-tokenized sebelum mode manapun, (5) approval terikat action-hash single-use, (6) transisi ke ACTIVE hanya human actor, (7) konten tainted selalu eskalasi approval, (8) kredensial cuma resolve di titik eksekusi broker, (9) subprocess selalu killable sbg process-group, (10) audit durable sinkron utk risk≥MEDIUM, (11) bot/kanal remote di-cap + fail-closed allowlist, (12) plugin tidak auto-exec dari CWD, (13) RPC/IPC validasi payload asli, (14) kill-switch tidak pernah hapus evidence/audit.

## 27. Reliability

Fix bug proses-leak-saat-timeout (`shell_runner.py` streaming path — tidak pernah `kill()` child saat `TimeoutExpired`), fix `WorkerEngine._run_job()` timeout kosmetik (`ThreadPoolExecutor` `with`-block yang block sampai thread selesai sendiri). `RunningActionRegistry` + `Popen(start_new_session=True)` menutup keduanya sekaligus lewat mekanisme yang sama (kill-switch, §PRD-006).

## 28. Observability

`audit_log` SQLite (integrity-hash chain) sebagai audit trail terpusat pertama di sistem ini — bukan cuma `EventBus` ring-buffer non-durable. Metrics/tracing lebih luas (Prometheus dll) di luar cakupan blueprint ini, dicatat sebagai technical debt P2/P3 (`01-gap-analysis.md`).

## 29. Deployment

`Dockerfile`/`docker-compose.yaml`/`.env*.example` — default `WTH_UNRESTRICTED` dihapus sebagai runtime switch, diganti default aman per 3 dimensi (ADR-012). `.dockerignore` ditambahkan (INFRA-01). Tidak ada perubahan topologi deployment (tetap single-container, tidak butuh orchestration baru untuk broker/policy — semuanya in-process).

## 30. Migration

Lihat `docs/planning/MIGRATION-MATRIX.md` — tabel lengkap Existing→Target→Strategy→Risk untuk setiap modul, gabungan dari kedua spesialis + revisi second-pass critique.

## 31. Roadmap

Lihat `docs/planning/IMPLEMENTATION-ROADMAP.md` dan `WORK-UNITS.md` — dependency chain nyata (bukan Phase 1/2/3 generik), P0 diprioritaskan ke fix CRITICAL findings audit + gap struktural yang jadi fondasi (state-machine enforcement, ActionIntent/Claim schema, broker skeleton).

## 32. Risks

- **Migrasi menyentuh file bertrafik tinggi sekaligus** (`supervisor_dispatch.py` disentuh ≥2 WU berbeda — dependency ordering eksplisit diperlukan, lihat `WORK-UNITS.md`).
- **Broker auto-evidence-store vs 7 call-site lama** — kalau tidak dihapus serentak, duplikasi evidence di laporan (temuan critique, mitigasi: WU eksplisit menghapus `request_pairs`/`store_from_executor`).
- **Rename `EngagementStateMachine`→`LoopStateMachine`** berpotensi konflik istilah dengan dokumentasi lama — mitigasi: index/glossary eksplisit (lihat `01-gap-analysis.md`).
- **`/hunt` (`EngagementLoop`) sebagai entry point utama yang selama ini bypass semua guardrail baru** — WU broker-integration untuk `executor/vuln/*` naik prioritas ke P0 sempit (bukan P1 penuh) karena AGENT-02 terkonfirmasi live.
- **Prompt system files** (`prompts/supervisor_system.txt` dll) mungkin mereferensikan nama tool lama (`scope_set`) secara tekstual — belum diverifikasi isinya, jadi WU rename tool wajib menyertakan audit prompt sebagai acceptance criteria eksplisit (bukan asumsi otomatis benar).

## 33. Open Questions

- Apakah `wth_ai/executor/engine.py` (branch `cursor/fase6-platform-f3b2`, tidak pernah di-merge) sengaja ditinggalkan karena keterbatasan HTTP-only, atau alasan lain yang tidak tercatat git? Perlu konfirmasi pemilik produk sebelum WU manapun mengklaim "menghidupkan kembali" konsepnya secara langsung (lihat catatan di `docs/planning/00-existing-architecture.md`).
- Isi `prompts/*.txt` belum diaudit baris-per-baris untuk referensi tool-name lama — perlu pembacaan langsung sebelum WU rename tool (`scope_set`→`propose_target`) dieksekusi.
- Visibilitas repo GitHub (publik/privat) belum terverifikasi dalam audit — relevan untuk urgensi remediasi git-history (`SECURITY_AUDIT.md` DATA-02/DATA-03/SECRET-01).
- Skala container/microVM sandbox (ADR-013) sengaja ditunda — perlu keputusan eksplisit pemilik produk kalau roadmap produk berubah ke arah multi-tenant hosting.
