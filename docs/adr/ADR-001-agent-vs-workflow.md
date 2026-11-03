# ADR-001: Agent-vs-Workflow Architecture

**Status**: Accepted
**Domain**: AI/Agent Architecture

## Decision

Pertahankan `AgentToolLoop`/`SupervisorAgent` (ReAct tool-calling — LLM memilih tool per giliran secara adaptif) sebagai arsitektur eksekusi **utama**. `EngagementLoop` (`/hunt`, kontrol-alur Python hardcoded: recon→iterate(plan→execute 3 task→verify)→[severity-exit|replan]→report) diperlakukan sebagai **preset terarah di atas mesin yang sama**, bukan pipeline independen kedua yang hidup selamanya.

## Context

Dua filosofi eksekusi coexist di codebase saat ini:
- `AgentToolLoop` (`wth_ai/agents/tool_loop.py`) — LLM memutuskan tool apa dipanggil tiap iterasi berdasarkan reasoning-nya terhadap state saat itu. Dipakai `SupervisorAgent` (setiap giliran "assess"), `ExecutorAgent._execute_with_tools`, `PlannerAgent._probe_phase`.
- `EngagementLoop` (`wth_ai/loop/engagement_loop.py`) — kontrol Python eksplisit, memanggil `orch.planner.plan()`/`orch.executor.execute_module()`/`orch.verifier.verify()` **langsung**, bypass `dispatch_supervisor_tool()` sepenuhnya.

Konsekuensi bypass ini: `/hunt` kehilangan SEMUA instrumentasi yang menempel di `dispatch_supervisor_tool()` — `action_memory` anti-loop check, tool-trace UI callback — sementara jalur ReAct mendapatkannya otomatis. Dua jalur yang secara logis melakukan hal sama (plan→execute→verify→report) punya properti keselamatan berbeda tergantung siapa yang memicu. Sisi baiknya: `EngagementLoop` mengkodekan pengetahuan kebijakan berharga (recon-dulu-baru-plan, severity-critical→stop lebih awal) yang belum ada representasinya di `AgentToolLoop`.

## Alternatives

1. **Hapus `EngagementLoop`** — ganti `/hunt` jadi sekadar prompt sistem + budget besar untuk `AgentToolLoop`.
2. **Biarkan dua-duanya independen selamanya** (status quo).
3. **Konvergensi bertahap** — state-machine/budget dibagi (lihat ADR-011 pola serupa), `EngagementLoop` migrasi jadi thin preset di atas mesin yang sama.

## Chosen Approach

Opsi 3.

## Why

Opsi 1 berisiko tinggi — `/hunt` sudah battle-tested (README secara eksplisit mendokumentasikan perilakunya: max 5 iterasi, severity-based early-exit) dan menghapusnya berarti kehilangan pengetahuan kebijakan yang sudah terbukti berguna, tanpa jaminan LLM bisa mereproduksi disiplin yang sama lewat prompt saja. Opsi 2 melanggengkan duplikasi properti keselamatan yang berbeda — sebagaimana ditemukan konkret: dua jalur logis-sama dengan jaminan berbeda adalah generator bug jangka panjang (dan sudah terbukti — bug `AssessmentPhase` di `supervisor_dispatch.py` HANYA reachable lewat jalur ReAct, `/hunt` tidak pernah kena karena bypass jalur itu; tapi ini keberuntungan, bukan desain).

## Trade-offs

Konvergensi (opsi 3) butuh disiplin urutan migrasi — state machine unifikasi (ADR-011 pola, WU state-machine di `docs/planning/WORK-UNITS.md`) harus landing DULU sebelum penggabungan actuator `EngagementLoop`→`dispatch_supervisor_tool()`, karena menyentuh keduanya sekaligus meningkatkan risiko regresi `/hunt`. Nilai penuh dari konvergensi ini baru terasa di P2 (`WU-013`), bukan instan — ini trade-off yang disengaja demi menjaga `/hunt` tetap stabil selama migrasi P0/P1 berjalan.
