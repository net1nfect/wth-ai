# PRD-011: Execution Broker

**Priority**: P0 (skeleton + `AgentTerminalBridge` facade) / P1 (integrasi penuh 5 jalur eksekusi)
**ADR terkait**: ADR-008
**Dependencies**: PRD-005, PRD-006

## Problem Statement

**5 jalur eksekusi subprocess/HTTP independen** terkonfirmasi (termasuk temuan second-pass critique): `AgentTerminalBridge` (`terminal/bridge.py`), `ExecuteRpcServer.execute()` (validasi string dummy, bukan kode nyata), `ReconEngine`/`recon/tools/*` (tidak pernah lewat `PermissionManager`), `WTHOrchestrator._execute_next`/`ExecutorPool` (loop dual-agent lama), dan **`executor/vuln/*.py`** — `ssrf.py:45-47` memanggil `requests.get()` LANGSUNG, di luar `AgentTerminalBridge`/`ScopeManager`/`PermissionManager` sepenuhnya. `execute_module()` (`agents/executor.py`) cek `PermissionMode` SEKALI di pintu masuk fungsi, tapi request nyata terjadi setelahnya tanpa re-check — pola "gate di pintu, bukan di titik tembak".

## Goals

`ExecutionBroker.execute(ActionIntent, BrokerContext) -> ExecutionResult` sebagai SATU-SATUNYA chokepoint. Semua 5 jalur menjadi caller broker.

## Data Model & Interface

Lihat `docs/architecture/DATA-MODEL.md` §1,2,9 dan `INTERFACES.md`.

## Functional Requirements

### Pipeline (§`docs/architecture/FLOWS.md` diagram 7-8)
Schema validation (`TOOL_DEFINITIONS`) → actor/trust resolution (`context.tainted`, PRD-006) → `Engagement` fetch-fresh (PRD-003) → scope check (REUSE `ScopeManager.is_in_scope()`) → `evaluate_intent()` (PRD-005) → Approval Gate action-hash (PRD-006) → rate-limit/concurrency (REUSE `_throttle()`) → sandbox (`Sandbox`+`resource.setrlimit`) + `RunningActionRegistry.register()` SEBELUM subprocess mulai → **execution** (satu-satunya pemanggil primitive dari titik ini) → audit sinkron (risk≥MEDIUM) → evidence auto-store (PRD-012).

### Prioritas Integrasi P0 (revisi dari second-pass critique)
`AgentTerminalBridge.dispatch_tool()` REFACTOR jadi facade — bangun `ActionIntent` dari signature lama `(name, arguments, actor, approved)`, panggil `broker.execute()`, terjemahkan `ExecutionResult`→`TerminalResult` (field sama persis). Call site (`tool_loop.py`, `supervisor_dispatch.py`) TIDAK berubah.

**Addendum P0 sempit** (temuan critique, urgensi dinaikkan karena AGENT-02 confirmed live bukan hipotetis): `execute_module()`/`executor/vuln/*.py` WAJIB panggil `ScopeManager.is_in_scope()` + `evaluate_intent()` (cek policy saja, bukan full broker/audit/evidence-pipeline) SINKRON sebelum request mentahnya — mendahului integrasi broker penuh untuk modul ini yang tetap P1.

### Prioritas P1
`ExecuteRpcServer.execute()` — bangun `ActionIntent(tool_name="code.execute_python", arguments={"code": req["code"], ...})`, panggil broker yang sama (fix NET-02 struktural). `execute_module()`/`executor/vuln/*.py` — integrasi broker PENUH (widened scope dari draft awal). `WTHOrchestrator._execute_next`/`ExecutorPool` — lewat broker yang sama supaya fix approval (PRD-006) berlaku kedua loop.

### Prioritas P1/P2
`ReconEngine`/`recon/tools/*` — bertahap, permukaan besar, tidak boleh blokir P0.

### Credential Injection
Setelah approval, sebelum eksekusi: broker resolve `vault:` reference di `intent.arguments` via `SecretVault.resolve()` (KEEP, existing), inject plaintext LANGSUNG ke request/env subprocess — plaintext TIDAK PERNAH ditulis balik ke `intent`/conversation/audit (audit simpan referensi asli). Gate baru: `capability.allow_credential_injection` — default `False` untuk `read_only`/`standard`.

## Acceptance Criteria

- Semua panggilan `AgentTerminalBridge.dispatch_tool()` existing tetap berfungsi tanpa perubahan call site.
- `ExecuteRpcServer` dengan payload Python destruktif representatif → diblokir/butuh approval, bukan lolos via string dummy.
- `executor/vuln/ssrf.py` terhadap target di luar `Engagement.targets` → blocked (addendum P0 sempit).
- Subprocess yang di-broker selalu terdaftar `RunningActionRegistry` SEBELUM mulai — verifikasi via test kill-switch (PRD-006).

## Out of Scope

Redesign `shell_runner.py`/`vault.py` itu sendiri (KEEP, sudah benar) — broker mengorkestrasi, tidak menggantikan primitive yang sudah teruji.
