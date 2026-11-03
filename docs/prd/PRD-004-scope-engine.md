# PRD-004: Scope Engine

**Priority**: P0 (compat shim) / P1 (migrasi penuh)
**ADR terkait**: ADR-011
**Dependencies**: PRD-003

## Problem Statement

`ScopeManager`/`Scope` (`scope_manager.py`, `models.py:92-117`) adalah value object mutable tunggal — logic `is_in_scope()`/`redact_secrets()` sudah BENAR dan battle-tested (dipakai puluhan test), tapi data model di baliknya (`Scope`) tidak punya lifecycle. `scope_intent.py` (regex heuristik parsing NL command) juga tetap berguna sebagai UX layer, TIDAK diganti.

## Goals

Migrasi `Scope` dari source-of-truth jadi VIEW turunan dari `Engagement` (PRD-003), TANPA mengubah API publik `ScopeManager` yang sudah dipakai luas — call site existing (`is_in_scope()`, `.scope.target`, `.scope.authorized`, dll) tetap berfungsi selama masa transisi.

## Functional Requirements — Migrasi 3 Fase

### Fase A (P0) — Compat shim
`ScopeManager` disimpan referensi internal ke `Engagement` aktif. `ScopeManager.scope` (property) DIDERIVE dari `Engagement.targets`/`excluded_assets`/`rate_limits`/`authorization_source` setiap diakses — bentuk `Scope` dataclass lama tetap dikembalikan (tidak breaking untuk pembaca). `ScopeManager.set_scope()` jadi wrapper tipis ke `EngagementManager.update_draft()` — **HANYA berhasil kalau `Engagement.status == DRAFT`**; dipanggil pada engagement `ACTIVE` GAGAL dengan pesan eksplisit ("gunakan amendment flow via /engagement"), bukan overwrite diam-diam (fix bug hilangnya riwayat target lama).

### Fase B (P1) — Call site baru baca `Engagement` langsung
`ExecutionBroker`, `PolicyEngine` baca `Engagement` langsung, tidak lewat `Scope` view. **Catatan sequencing dari second-pass critique**: context-builder call site (`planner.py`, `supervisor.py`, `verifier.py`) semuanya baca `state.scope.to_dict()`/`.target` LANGSUNG (bukan lewat satu API) — sebaiknya fase B disekuens SETELAH/bersamaan `ContextBuilder` unifikasi (PRD-008/ADR-004) supaya migrasi tinggal sentuh 1 file (`context/builder.py`), bukan 3+ file tersebar.

### Fase C (P2, technical debt tercatat) — Pensiunkan `Scope`
Setelah semua call site pindah.

## Non-Goals

Tidak mengubah logic `is_in_scope()`/`redact_secrets()`/`_host_matches_pattern()` — sudah benar, hanya sumber datanya yang berubah.

## Acceptance Criteria

- Semua test existing yang bergantung `ScopeManager`/`Scope` API tetap hijau tanpa modifikasi setelah Fase A.
- `set_scope()` pada `Engagement` status `ACTIVE` menghasilkan error eksplisit, bukan silent overwrite.
- `TargetScope.pattern` syntax kompatibel dengan `_host_matches_pattern()` existing (REFACTOR bukan REPLACE — tidak perlu re-tulis parser pattern).

## Out of Scope

Redesign syntax pattern matching itu sendiri — sudah cukup baik, di luar cakupan PRD ini.
