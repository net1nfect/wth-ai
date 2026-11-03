# PRD-013: Model Provider Abstraction

**Priority**: P1
**ADR terkait**: ADR-003, ADR-006
**Dependencies**: —

## Problem Statement

3 bentuk response berbeda hidup berdampingan (`tuple[str,str]`, dict mentah tool-call, envelope `complete()` yang jarang dipakai). Model capability registry stub (`get_context_length()`/`get_rate_limit_info()` selalu `None`). Error handling tidak konsisten (`normalize_error()` cuma dipakai jalur jarang, jalur utama string-matching manual terduplikasi).

## Goals

`ModelRequest`/`ModelResponse` kanonik via `generate()` baru (strangler-fig). Model capability registry terisi data nyata. Model routing otomatis TETAP DITOLAK (ADR-006 — bukan gap yang perlu ditutup).

## Data Model

Lihat `docs/architecture/DATA-MODEL.md` §6.

## Functional Requirements

1. `LLMProviderRouter.generate(request: ModelRequest) -> ModelResponse` — method inti baru. `chat_for_role`/`chat_with_tools_for_role`/`complete` jadi adapter tipis di atasnya.
2. Pilot migrasi: `ReportBuilderAgent` (satu-satunya agent yang sudah `role=` eksplisit dan bersih — risiko migrasi terendah).
3. `wth_ai/llm/model_catalog.py` — seed YAML (`model_id → {context_length, cost_per_1k_in/out, supports_tools, supports_json}`) untuk model default (`ModelConfig` di `config.py`) + cache live dari `OpenRouterProvider.list_models()` (verifikasi dulu apakah field `context_length`/`pricing` sudah diterima tapi dibuang saat ekstraksi — perlu dicek saat implementasi).
4. `normalize_error()` dipanggil konsisten di `chat_for_role`/`chat_with_tools_for_role`, bukan cuma `complete()` — exception dibungkus `LLMError` yang membawa `exc.kind` ternormalisasi, konsumen (`orchestrator.py`) cek `exc.kind=="timeout"` alih-alih string-matching regex ad-hoc berulang.
5. `wth_ai/openrouter.py::OpenRouterClient` — verifikasi ulang tidak ada import aktif, lalu hapus (dead code, duplikat `OpenRouterProvider`).

## Acceptance Criteria

- `ModelResponse.usage` terisi nyata untuk provider yang expose data itu (OpenRouter) — fondasi `LoopBudget.max_cost_usd` (PRD-008).
- `get_context_length("anthropic/claude-3.5-sonnet")` mengembalikan angka nyata, bukan `None`, untuk model yang ada di seed/cache.
- Tidak ada regresi pada 334 test existing setelah `ReportBuilderAgent` migrasi ke `generate()`.

## Out of Scope

Model routing otomatis berbasis kompleksitas — DITOLAK permanen (ADR-06), tidak ada implementasi untuk ini di PRD manapun.
