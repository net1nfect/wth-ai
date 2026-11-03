# PRD-009: Planning & Task Representation

**Priority**: P1
**ADR terkait**: ADR-002
**Dependencies**: PRD-007, PRD-012

## Problem Statement

`ExecutorTask` (`models.py:223-261`) dataclass flat, tanpa field dependency/priority. `SessionState.task_queue: list[dict]` — list datar, FIFO/by-id. `stop_condition`/`success_criteria` (field existing) tidak pernah dibaca kode manapun untuk keputusan — dead field fungsional meski LLM tetap diminta mengisinya di setiap plan (buang generation effort).

## Goals

Task graph DAG **DITOLAK** (ADR-002 — tidak ada konsumen nyata yang butuh percabangan dependency kompleks). Sebagai gantinya: `priority`+`depends_on` advisory + wiring `stop_condition` ke mekanisme yang sudah ada.

## Data Model

Lihat `docs/architecture/DATA-MODEL.md` §8. `priority:int=5`, `depends_on:list[str]=[]` (advisory sort saat mengambil task dari queue, BUKAN hard scheduler), `origin:str="llm_json"` (provenance).

## Functional Requirements

1. `ExecutorTask` field baru (aditif, tidak breaking `from_dict`/`to_dict` — default value backward-compatible).
2. Sorting: task dengan `depends_on` yang belum masuk `completed_tasks` didorong ke belakang antrian — satu fungsi sort, bukan scheduler baru.
3. `stop_condition` di-WIRE ke `EvidenceVault.validate_schema()` — dicek tiap iterasi SEBELUM lanjut sisa `command_plan` suatu task, early-exit kalau sudah terpenuhi.
4. `ClaimStore.create_claim_from_hypothesis()` (PRD-012) dipanggil di titik ini (saat planning), bukan post-hoc — `claim_id` masuk `ActionIntent.claim_ids` sebelum broker eksekusi.
5. Job system (`jobs/orchestrator_bridge.py`) hardcode `tasks_raw[:6]` — TETAP hardcode untuk sekarang (bukan bug, cukup sebagai safety cap), tapi jadikan configurable via `AgentConfig`/`LoopBudget` ke depan, bukan magic number tertanam di satu tempat.

## Acceptance Criteria

- Task dengan `depends_on` diambil setelah dependency-nya di `completed_tasks`, meski masuk antrian lebih dulu.
- `stop_condition` yang match `validate_schema()` menghentikan sisa `command_plan` task itu (bukti field yang sebelumnya dead sekarang punya efek nyata).
- `ExecutorTask.from_dict()` pada data sesi LAMA (tanpa field baru) tetap berhasil load tanpa error (default value).

## Out of Scope

DAG scheduler, topological sort, deteksi siklus — sengaja tidak dibangun (lihat ADR-002 untuk kapan ini perlu direvisit).
