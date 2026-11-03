# PRD-010: Tool System

**Priority**: P0
**Dependencies**: PRD-005, PRD-006

## Problem Statement

`tool_registry.py::TOOL_DEFINITIONS`/`ACTOR_TOOLS` sudah punya filtering per-actor yang cukup baik (planner tidak dapat `run_shell`, dst) — TAPI `scope_set`/`permission_set` adalah tool LLM-callable tanpa gate approval sama sekali (AGENT-01), dan `recon` actor diberi akses `run_shell` di permission level TERENDAH (`READ_ONLY_RECON`).

## Goals

- Pengetatan skema tool sensitif tanpa mengubah struktur `ACTOR_TOOLS`/`tools_for_actor()` yang sudah bekerja baik.
- `authorize_engagement` dihapus total dari tool LLM. `scope_set`→`propose_target` (draft-only). `permission_set`→`request_profile_upgrade` (selalu approval).
- Naikkan syarat minimum `run_shell` untuk actor `recon` ke `STANDARD_TESTING`.

## Functional Requirements

1. `TOOL_DEFINITIONS` — skema `propose_target`: field `target` required (existing), TIDAK ADA field `authorized` sama sekali di skema (bukan opsional dengan default — dihapus, supaya secara struktural LLM tidak bisa mengklaim authorization apa pun lewat tool ini).
2. `ACTOR_MIN_PERM["recon"]` (`terminal/bridge.py:28-32`) — ubah dari `READ_ONLY_RECON` ke `STANDARD_TESTING`.
3. **Audit `prompts/*.txt`** (temuan second-pass critique — belum diverifikasi isinya) — pastikan tidak ada instruksi tekstual yang menyebut nama tool lama (`scope_set`, `authorize_engagement`) yang akan membuat model terus mencoba memanggil tool yang sudah tidak ada.
4. `supervisor.py:169::_offline_fallback()` — update hardcode string `"scope_set"`→`"propose_target"`.

## Acceptance Criteria

- Skema `propose_target` yang dikirim ke LLM (function-calling schema) tidak punya field `authorized` sama sekali.
- Upaya memanggil `authorize_engagement` via tool-call menghasilkan "unknown tool", bukan dieksekusi.
- Grep `prompts/*.txt` untuk `scope_set\b`/`authorize_engagement\b` — nol hasil setelah migrasi (atau diupdate ke nama baru).
- `run_shell` oleh actor `recon` pada mode `read_only` → blocked (naik ke `standard` minimal).

## Out of Scope

Redesign `ACTOR_TOOLS` filtering itu sendiri (sudah cukup baik, KEEP struktur).
