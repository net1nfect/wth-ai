# PRD-003: Engagement Management

**Priority**: P0 (FSM + entity dasar) / P1 (store penuh + integrasi lintas komponen)
**ADR terkait**: ADR-011
**Dependencies**: —

## Problem Statement

Tidak ada entity `Engagement` first-class di codebase saat ini. Otorisasi diwakili `Scope` — value object mutable tunggal per sesi, tanpa `id`/`name`/`expires_at`/`excluded_assets`/`status`. `set_scope()` overwrite total setiap dipanggil (riwayat target lama hilang tanpa jejak). Satu `SessionState` = satu `scope` = satu target aktif; "multi-target" hanya lewat multi-sesi paralel. Tidak ada lifecycle — target bisa "aktif" tanpa proses otorisasi formal apa pun selain flag `authorized: bool` yang bisa di-`setattr` kapan saja, termasuk (sebelum fix PRD-005/006) oleh LLM sendiri lewat tool call.

## Goals

- Entity `Engagement` dengan lifecycle FSM eksplisit (DRAFT→VALIDATING→READY→ACTIVE↔PAUSED→COMPLETED/ABORTED/EXPIRED).
- Transisi ke `ACTIVE` (state yang berarti "boleh testing aktif") HANYA bisa dilakukan actor manusia — never oleh string actor agent (`supervisor`/`planner`/`executor`/`recon`).
- Enforcement DUA lapis: property read-only + `.transition()` (lapis 1, cegah kesalahan tidak sengaja) DAN `ExecutionBroker` fetch-fresh dari storage tiap eksekusi (lapis 2, cegah pelanggaran API yang disengaja/lupa — pelajaran langsung dari `LoopState` yang terbukti vestigial).

## Non-Goals

- Tidak membangun UI manajemen engagement terpisah (interaksi tetap CLI/slash command + bot).
- Tidak mendukung concurrent multi-engagement dalam SATU sesi percakapan (tetap: satu sesi mengaktifkan satu Engagement pada satu waktu — `Engagement.targets` sendiri sudah mendukung banyak target dalam SATU otorisasi).

## Data Model

Lihat `docs/architecture/DATA-MODEL.md` §3 untuk skema `Engagement`/`TargetScope`/`AuthorizationSource`/`RateLimitPolicy`/`ConcurrencyPolicy`/`TimeWindow` lengkap. Lihat `docs/architecture/STATE-MACHINES.md` §1 untuk FSM dan guard per transisi.

## Functional Requirements

1. `EngagementStore` (SQLite, `WTH_HOME/data/engagement.sqlite` atau tabel di db.sqlite existing yang sudah di-provision `workspace.py::init_workspace()` tapi belum dipakai) — CRUD + `fetch(engagement_id) -> Engagement` yang SELALU baca fresh, tidak pernah cache lintas-panggilan broker.
2. Command baru: `/engagement create`, `/engagement authorize <id>` (HANYA reachable manusia — CLI langsung atau command bot ber-role owner-terverifikasi, TIDAK PERNAH sebagai tool LLM-callable), `/engagement pause|resume|abort`.
3. Tool LLM `propose_target` (pengganti `scope_set`) — HANYA menulis field DRAFT (`targets`, `notes`), TIDAK PERNAH menyentuh `status`/`authorization_source`/`approved_by`.
4. Migrasi 3 fase (lihat PRD-004 untuk detail `Scope` compat shim).

## Interaksi dengan Komponen Lain

- `SessionState` dapat field aditif `engagement_id: str` — TIDAK menghapus `scope`/`permission_mode` existing selama masa transisi.
- `ExecutionBroker` (PRD-011) adalah SATU-SATUNYA konsumen yang hard-deny berdasarkan `Engagement.status`.
- Bot (Discord/Telegram, lihat PRD-006 §access-control) — sesi bot dapat `PermissionProfile` yang di-cap TERLEPAS dari `Engagement` mana yang mereka coba aktifkan; `authorize_engagement` tidak reachable dari bot chat sama sekali kecuali command eksplisit ber-role owner.

## Acceptance Criteria

- `Engagement.transition("activate", actor="supervisor")` HARUS gagal (guard menolak actor agent).
- `Engagement.transition("activate", actor="operator:cli")` dengan `approved_by=None` HARUS gagal.
- Setelah `ExecutionBroker` terintegrasi (PRD-011): eksekusi terhadap `Engagement` berstatus `PAUSED`/`EXPIRED`/`ABORTED` HARUS `deny_reason="engagement_not_active"`, walau `Scope`/`SessionState` in-memory lama (kalau masih ada, masa transisi) mengklaim `authorized=True`.
- Test: `time_window.expires_at` terlewati → status otomatis `EXPIRED` tanpa perlu trigger manual (dicek di `EngagementStore.fetch()` atau job cron ringan).

## Out of Scope

Full UI/dashboard untuk kelola banyak engagement historis — cukup daftar/list via CLI, bukan visualisasi.
