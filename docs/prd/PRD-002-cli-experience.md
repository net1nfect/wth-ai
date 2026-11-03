# PRD-002: CLI Experience

**Priority**: P2
**Dependencies**: PRD-007

## Problem Statement

3 registry slash-command paralel tidak sinkron: `slash_router.py` (aktif, ~39 handler), `slash_commands.py::SlashCommandHandler` (35 command, **dead code terkonfirmasi** — diinstansiasi tapi tidak pernah dipanggil), `platform/registry.py::PlatformRegistry` (40 `PlatformCommand`, dipakai `/help`+autocomplete). Plus tingkat literal if/elif TERPISAH di `orchestrator.handle_input()` untuk `/help`,`/quit`,dll — 4 tingkat dispatch total, bukan 3. `SkillRouter.detect_intent()` fallback SELALU "chat" tidak peduli state sesi — follow-up percakapan security tapi tanpa keyword `assess_patterns` jatuh ke `ChatAgent` (tanpa tool-calling), user kehilangan akses tool tanpa sadar di tengah sesi hunt aktif.

## Goals

`PlatformRegistry` jadi single source of truth. Hapus dead code. Intent fallback jadi session-aware (perbaikan satu kondisi, BUKAN classifier baru — ADR terkait §CLI di master architecture doc menolak router multi-kategori di atas ReAct yang sudah adaptif).

## Functional Requirements

1. Merge tingkat literal if/elif (`/help`,`/quit`,`/stop`,dll) ke `SlashRouter.handlers` sebagai entry biasa — fast-path TETAP dipertahankan HANYA untuk kasus tanpa `/` prefix (`"quit"`/`"exit"`/`"stop"` bare word).
2. Hapus `slash_commands.py::SlashCommandHandler` (dead code terkonfirmasi via grep menyeluruh).
3. Test invariant: `PlatformRegistry.all_slash_names()` vs `SlashRouter.handlers.keys()` — assertion sinkronisasi, gagal di CI kalau drift (bukan "ingat sinkronkan manual").
4. `SkillRouter.detect_intent(user_input, *, session_state=None)` — tambah parameter opsional: `if session_state and session_state.scope.target and session_state.current_phase != AssessmentPhase.INTAKE: return "assess"` (default berbeda kalau sudah di tengah engagement aktif), else fallback lama tetap `"chat"`.

## Acceptance Criteria

- Semua command yang diiklankan `/help`/autocomplete benar-benar routable.
- `slash_commands.py` dihapus, tidak ada import yang patah.
- Follow-up percakapan tanpa keyword eksplisit tapi dalam sesi dengan target+phase aktif → intent `"assess"`, bukan `"chat"`.

## Out of Scope

Redesign TUI (`cli/tui/*`) — tetap alternate renderer, tidak ada perubahan jalur dispatch (sudah dikonfirmasi `WthTuiApp._execute_command()` = `orch.handle_input()` yang sama).
