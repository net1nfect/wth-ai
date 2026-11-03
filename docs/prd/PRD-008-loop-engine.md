# PRD-008: Loop Engine

**Priority**: P0 (state-machine enforcement) / P1 (budget penuh)
**ADR terkait**: ADR-001, ADR-005, ADR-011
**Dependencies**: PRD-007

## Problem Statement

`LoopState`/`EngagementStateMachine` (`loop/state_machine.py`) punya `TRANSITIONS` table + guard `.transition()` yang BENAR secara desain, tapi **tidak pernah dipanggil** — `engagement_loop.py` melakukan `self.sm.state = LoopState.X` LANGSUNG di 6 titik, bypass total. Terbukti write-only/vestigial. Budget hari ini cuma `max_iters` (default class 8, nilai efektif runtime SELALU 50) — hard-cutoff yang membuang semua observasi terkumpul saat habis, tanpa partial-salvage. `EngagementLoop` sudah menghitung sinyal progress (`cov_before`) tapi tidak pernah membandingkannya untuk deteksi stagnasi.

## Goals

- State machine yang BENAR-BENAR ditegakkan, bukan cuma didefinisikan — `state` read-only property, `.transition()` satu-satunya mutator, dipakai `AgentToolLoop` (bukan cuma `EngagementLoop`).
- `LoopBudget` — steps/wall-time/cost/consecutive-failures/no-progress, dengan graceful degradation (bukan hard-cutoff buang kerja).
- Rename `EngagementStateMachine`→`LoopStateMachine` untuk hindari tabrakan nama dengan entity `Engagement` otorisasi baru (PRD-003).

## Data Model

Lihat `docs/architecture/DATA-MODEL.md` §7 (`LoopBudget`), `docs/architecture/STATE-MACHINES.md` §2,7.

## Functional Requirements

### `LoopStateMachine` (rename + enforcement-by-construction)
```
IDLE→RECON→PLANNING→EXECUTING→VERIFYING→{REPORTING|REPLANNING→PLANNING}→DONE
(state manapun)→STOPPED
```
`transition()` emit event ke `EventBus` + raise `InvalidTransitionError` di `WTH_TEST_MODE`/`WTH_STRICT_SM=1`; di luar itu, clamp (jangan korup state, jangan pura-pura sukses). Dipakai `dispatch_supervisor_tool()` (mapping tool→state: `recon_run_tool`→RECON, `plan_hunt`→PLANNING, `execute_task`→EXECUTING, `verify_finding`→VERIFYING, `generate_report`→REPORTING) — sekaligus jadi titik integrasi fix `AssessmentPhase` (PRD-007).

### `LoopBudget`
`max_steps` (ganti nama `max_iters`, default sama 50 — tidak breaking), `max_wall_seconds`, `max_cost_usd` (None-able, nunggu `ModelResponse.usage` dari PRD-013), `max_consecutive_failures` (REUSE `ActionMemory.should_block()`, bukan pencatat baru), `no_progress_window` (REUSE delta `asset_graph.coverage_pct` yang sudah dihitung `engagement_loop.py:70,152` tapi tidak pernah dibandingkan). Saat habis: **graceful degradation** — SATU LLM call terakhir tanpa tools untuk merangkum apa yang sudah terbukti dari transcript terkumpul, dijalankan tetap lewat `GroundingFirewall`.

### Interrupt Points (temuan second-pass critique)
Setiap iterasi `AgentToolLoop` mengecek `budget.exhausted()` DAN kill-signal (`self._stopped`/`EventBus.killswitch.*`) SEJAJAR — `self._stopped` di `SupervisorAgent` di-set tapi TIDAK PERNAH dibaca hari ini, gap nyata yang harus ditutup. `ExecutionResult.deny_reason` typed memungkinkan loop STOP SEGERA kalau deny berasal dari kondisi yang tidak akan berubah dalam turn ini (mis. `engagement_not_active`), bukan menghabiskan `max_steps` retry ke deny yang sama.

## Interaksi dengan Komponen Lain

`ExecutionBroker` (PRD-011) mengembalikan `deny_reason` yang dikonsumsi loop ini. `Engagement.status` (PRD-003) TIDAK dikontrol state machine ini — beda risk profile (in-process/single-instance vs lintas-proses/security-critical, lihat ADR-011).

## Acceptance Criteria

- `self.sm.state = X` di mana pun menghasilkan `AttributeError` (bukti enforcement struktural, bukan konvensi).
- Suite regresi `/hunt` end-to-end tetap identik secara observable (report tetap terbit, early-exit severity tetap jalan).
- Test skenario tool-call terskrip (`WTH_TEST_MODE`) — assert urutan state RECON→PLANNING→EXECUTING→VERIFYING sesuai transkrip.
- Transisi ilegal yang sengaja dipaksakan di test terdeteksi (raise atau event `invalid_state_transition` termonitor), tidak diam-diam diterima.
- Budget habis → forced summary call terjadi, BUKAN return string kanned kosong.
- Kill-signal terpicu di tengah loop → iterasi berikutnya TIDAK dimulai (dicek sebelum LLM call berikutnya, bukan setelah).

## Out of Scope

Penggabungan penuh `EngagementLoop`→`AgentToolLoop` (WU-013, P2, ADR-001). Job type `"engagement"` async (ADR-005, P2).
