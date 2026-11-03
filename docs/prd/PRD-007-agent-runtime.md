# PRD-007: Agent Runtime

**Priority**: P0
**ADR terkait**: ADR-001
**Dependencies**: —

## Problem Statement

3 loop berbeda coexist (`AgentToolLoop`, `SupervisorAgent.run()` wrapper, `EngagementLoop`/`/hunt`) dengan properti keselamatan berbeda-beda — `EngagementLoop` memanggil `orch.planner.plan()`/`orch.executor.execute_module()`/`orch.verifier.verify()` LANGSUNG, bypass `dispatch_supervisor_tool()` sepenuhnya (kehilangan `action_memory` anti-loop + tool-trace instrumentasi). Bug konkret: `supervisor_dispatch.py:178,206,237` mereferensikan `AssessmentPhase.PLANNING/.EXECUTION/.REPORTING` yang **tidak ada** di enum asli — `AttributeError` tertelan senyap oleh `except Exception` generik, padahal efek samping (task_queue terisi, task tereksekusi, 1 LLM call mahal untuk report) sudah terjadi.

Role-wiring rusak: `BaseLLMAgent._call()` (`agents/__init__.py:67-97`) menerima `model`/`backup` tapi tidak pernah membacanya — `VerifierAgent.verify()` dan `ExecutorAgent._enrich_task_from_llm/_parse_with_llm` diam-diam SELALU pakai role `"planner"`.

## Goals

- `AgentToolLoop`/`SupervisorAgent` (ReAct) dipertahankan sebagai arsitektur eksekusi utama (ADR-001).
- Fix bug `AssessmentPhase` dengan guardrail generik (bukan cuma tambal 3 baris).
- Fix bug role-wiring dengan `role` jadi keyword-only wajib (fail-fast, bukan silent-wrong).
- `EngagementLoop` (`/hunt`) migrasi bertahap jadi preset di atas mesin yang sama (bukan P0 — lihat PRD-008/ADR-001).

## Functional Requirements

### Fix `AssessmentPhase` (WU-001, P0)
`agents/supervisor_dispatch.py:178,206,237` — ganti `AssessmentPhase.PLANNING`→`.HYPOTHESIS`, `.EXECUTION`→`.DYNAMIC`, `.REPORTING`→`.REPORT` (nilai yang SUDAH ada di enum, `models.py` TIDAK diubah). Tambah test yang men-scan repo (regex `AssessmentPhase\.[A-Z_]+`) dan assert setiap nama yang dirujuk ada di `{p.name for p in AssessmentPhase}` — guardrail generik mencegah kelas bug ini terulang.

### Fix Role-Wiring (WU-002, P0)
`agents/verifier.py:67-73` (`role="verifier"`), `agents/executor.py:184-191,207-214` (`role="executor"`), `agents/planner.py:109-117,139-146,150-157` (`role="planner"` eksplisit, defensif). `agents/__init__.py` — hapus default `= "planner"` pada parameter `role`, jadi keyword-only wajib. Test spy pada `LLMProviderRouter.chat_for_role`/`generate` membuktikan `VerifierAgent.verify()` benar-benar memanggil dengan `role="verifier"`.

## Interaksi dengan Komponen Lain

`AgentToolLoop` adalah caller `ExecutionBroker` via `dispatch_tool()` facade (PRD-011). `LoopStateMachine`/`LoopBudget` (PRD-008) dipasang di sini. `ContextBuilder` (PRD-013 area/ADR-004) dipanggil `SupervisorAgent`/`WTHOrchestrator._assess_loop`.

## Acceptance Criteria

- `dispatch_supervisor_tool()` untuk `plan_hunt`/`execute_task`/`generate_report` tidak lagi menghasilkan string `"ERROR: ..."` akibat enum salah.
- `ctx.state.current_phase` menjadi instance `AssessmentPhase` valid setelah tiap pemanggilan tool tersebut (test invoke langsung di `WTH_TEST_MODE`).
- Grep `self._call(` di seluruh `agents/*.py` menghasilkan tepat 8 hasil (bukan 6 seperti draf awal — `chat.py:46` dan `report_builder.py:46` sempat tidak terhitung; lihat catatan implementasi WU-002), semuanya membawa `role=` eksplisit.
- 334 test existing tetap hijau.

## Out of Scope

`LoopBudget`, `LoopStateMachine` penuh (PRD-008). Penggabungan `EngagementLoop` ke `AgentToolLoop` sepenuhnya (P2, ADR-001).
