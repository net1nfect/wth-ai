# ADR-008: Execution Broker sebagai Chokepoint Tunggal

**Status**: Accepted
**Domain**: Security/Platform Architecture

## Decision

Semua subprocess/HTTP/code-exec di WTH-AI wajib melalui `ExecutionBroker.execute(intent: ActionIntent, context: BrokerContext) -> ExecutionResult`. `AgentTerminalBridge.dispatch_tool()`, `ExecuteRpcServer.execute()`, dan (bertahap) `ReconEngine`/tool-adapter recon menjadi caller broker, bukan pemanggil primitive subprocess/HTTP langsung.

## Context

Riset (termasuk second-pass critique) mengonfirmasi ada **LIMA jalur eksekusi independen**, bukan tiga seperti perkiraan awal:
1. `AgentTerminalBridge` (`terminal/bridge.py`) — run_shell/http_probe, permission+scope check ada tapi dengan bug (ADR-009/010).
2. `ExecuteRpcServer.execute()` (`rpc/execute_server.py`) — eksekusi Python arbitrer, validasi cuma string dummy `"execute"`.
3. `ReconEngine`/`recon/tools/*` — tidak pernah lewat `PermissionManager.check_command()` sama sekali.
4. `WTHOrchestrator._execute_next`/`ExecutorPool` (loop dual-agent lama) — approval-check terpisah dari (1).
5. **`executor/vuln/*.py` (idor/ssrf/xss)** — dikonfirmasi second-pass critique: `ssrf.py:45-47` memanggil `requests.get()` **LANGSUNG**, di luar `AgentTerminalBridge`/`ScopeManager`/`PermissionManager` sepenuhnya. `execute_module()` (`agents/executor.py`) mengecek `PermissionMode` **sekali di pintu masuk fungsi**, tapi request nyata terjadi setelahnya tanpa re-check — pola "gate di pintu, bukan di titik tembak" yang persis jadi alasan keputusan ini.

Kelima jalur ini plus 2 loop orchestrasi paralel sudah terbukti drift (temuan AUTHZ-01/02/03/04/05, NET-02, dan bug AGENT-02 yang kini terkonfirmasi live bukan hipotetis).

## Alternatives

1. **Tambal tiap jalur secara independen** — sudah terbukti gagal (ini yang menyebabkan drift sekarang).
2. **Middleware/decorator** di tiap fungsi shell-out — tidak memberi satu titik untuk audit/kill-switch/rate-limit yang konsisten.
3. **Broker tunggal** dengan facade compat di titik-titik existing.

## Chosen Approach

Opsi 3.

## Why

Satu tempat untuk menegakkan pipeline lengkap (schema validation→trust resolution→Engagement fetch-fresh→scope→policy→approval→rate-limit→sandbox→execution→audit→evidence) berarti perbaikan otomatis berlaku di SEMUA jalur sekaligus, dan jalur baru tidak bisa "lupa" menegakkannya karena tidak ada cara lain yang legal memanggil subprocess/HTTP.

## Trade-offs

Migrasi 5 caller existing tanpa regresi butuh disiplin — dimitigasi dengan facade yang mempertahankan signature/return-shape lama SECARA KETAT (`TerminalResult` tidak berubah bentuk). Sedikit latency tambahan per panggilan (registrasi ke `RunningActionRegistry`, audit sinkron untuk risk≥MEDIUM) — diterima karena volume panggilan (tool-call per giliran agent) jauh dari throughput tinggi yang butuh optimasi mikro. **Revisi pasca second-pass critique**: broker yang OTOMATIS menyimpan evidence di titik primitif berarti 7 call-site lama (`request_pairs` relay dari `ExecutorAgent`/3 vuln-executor + 3 titik `evidence_vault.store_from_executor()` di `orchestrator.py`/`engagement_loop.py`) akan **dobel-mencatat** evidence yang sama begitu broker aktif — ketujuh titik ini WAJIB dihapus sebagai bagian rollout broker (dicatat eksplisit di `WORK-UNITS.md`, bukan tersirat), bukan trade-off yang dibiarkan.
