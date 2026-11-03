# ADR-003: Model Provider Abstraction — Canonical Envelope

**Status**: Accepted
**Domain**: AI/Agent Architecture

## Decision

Promosikan bentuk envelope (shape yang sudah dipakai `LLMProviderRouter.complete()`) jadi `ModelResponse` kanonik, diakses lewat method baru `LLMProviderRouter.generate(request: ModelRequest) -> ModelResponse`. Tiga method existing (`chat_for_role`, `chat_with_tools_for_role`, `complete`) menjadi **adapter tipis** di atas `generate()` — bentuk pemanggilan lama TIDAK berubah untuk call site yang ada.

## Context

Response LLM saat ini punya **3 bentuk berbeda hidup berdampingan**, diverifikasi langsung:

| Bentuk | Sumber | Shape |
|---|---|---|
| A — tuple | `chat_for_role()` (dipakai mayoritas agent) | `(content: str, model_used: str)` |
| B — raw provider envelope | `chat_with_tools_for_role()` (dipakai tool-loop) | `{"message": {...}, "model": ...}` — bentuk asli OpenAI chat completion, tidak dinormalisasi |
| C — normalized envelope | `complete()` (jarang dipakai, cuma `/model test`) | `{"provider","model","role","status","content","usage","tool_calls","error"}` |

Bentuk C paling "benar" (sudah membawa `usage`, `error` ternormalisasi via `normalize_error()`) justru PALING JARANG dipakai jalur agent nyata. `get_context_length()`/`get_rate_limit_info()` (`llm/providers/base.py:32-38`) dikonfirmasi tidak pernah di-override provider manapun — selalu `None`.

## Alternatives

1. **Big-bang rename** semua call site ke satu bentuk baru sekaligus.
2. **Biarkan 3 bentuk coexist selamanya**.
3. **Strangler-fig** — bentuk baru ditambah, lama jadi adapter, migrasi call-site bertahap per-agent.

## Chosen Approach

Opsi 3. `ReportBuilderAgent` jadi pilot migrasi pertama (satu-satunya agent yang sudah memanggil dengan `role=` eksplisit dan bersih, jadi paling rendah risiko).

## Why

Opsi 1 melanggar constraint no-rewrite dan berisiko tinggi menyentuh jalur LLM call yang dipakai di puluhan tempat sekaligus — kalau ada regresi, blast radius-nya seluruh fitur yang bergantung LLM. Opsi 2 membiarkan `usage`/`cost` tracking (fondasi untuk `LoopBudget.max_cost_usd`, lihat PRD-008) tidak punya rumah yang konsisten — setiap agent yang butuh cost data harus reimplementasi ekstraksi dari bentuk yang berbeda-beda.

## Trade-offs

Selama masa transisi ada DUA API (lama+baru) berdampingan — kompleksitas kognitif sementara bagi siapa pun yang membaca kode. Ini harus disertai target migrasi eksplisit per-call-site (dilacak di `WORK-UNITS.md`), bukan dibiarkan dua-duanya permanen tanpa batas waktu.
