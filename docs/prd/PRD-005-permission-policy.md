# PRD-005: Permission & Policy

**Priority**: P0
**ADR terkait**: ADR-009, ADR-010
**Dependencies**: —

## Problem Statement

Dua bug kritis terkonfirmasi: (1) `PermissionManager.check_command()` — `bash`/`sh`/`zsh` di-skip TOTAL dari allowlist check di SEMUA mode termasuk `read_only`; sisa cek pakai substring match (`any(t in cmd_lower for t in allowed_tools)`) bukan token match; mode `UNRESTRICTED` early-return SEBELUM loop `DENYLIST_PATTERNS` dicek. (2) `jobs/policy.py::PolicyEngine.evaluate()` — dead-code fallthrough, SELALU `return True, ""` di baris terakhir apa pun `safe_mode`.

## Goals

- `Capability`/`PermissionProfile` granular menggantikan `MODE_TOOLS` flat set sebagai unit enforcement.
- `evaluate_intent()` — SATU policy engine dipakai KEDUA jalur (interaktif via broker, job admission via adapter) — tidak ada fallthrough implisit ke ALLOW di jalur kode manapun.
- Argv exact-match (bukan substring) untuk resolusi Capability.
- Denylist absolut dicek SEBELUM early-return mode manapun, termasuk `UNRESTRICTED`.

## Data Model

Lihat `docs/architecture/DATA-MODEL.md` §4 (`Capability`/`PermissionProfile`). `PermissionMode` (5 nilai existing) DI-KEEP sebagai ceiling label — TIDAK di-rename (ADR-009 §Why untuk alasan lengkap: breaking serialization tanpa nilai tambah keamanan).

## Functional Requirements

1. `wth_ai/policy/capability.py` — 5 `PermissionProfile` built-in, mapping 1:1 dari `MODE_TOOLS` existing (isi tools SAMA, dibungkus granular), plus 1 capability baru `shell.interpreter_passthrough` untuk `bash/sh/zsh/python/python3/perl/ruby/node` — SELALU `risk=HIGH, approval_mode=REQUIRE_HUMAN`, TIDAK PERNAH di-skip lagi.
2. `wth_ai/policy/engine.py::evaluate_intent()` — 6 layer (Kill-switch→Global-Safety→Environment→Engagement→Agent-Capability→Tool-Policy→Action-Policy), lihat `docs/architecture/FLOWS.md` §5 untuk diagram lengkap.
3. `PermissionManager.check_command()` — REFACTOR jadi adapter tipis ke `evaluate_intent()`, return-shape dict PERSIS sama (`{"allowed","reason","risk","approval_needed",...}`) supaya test existing tidak perlu diubah.
4. `jobs/policy.py::PolicyEngine.evaluate()` — REFACTOR jadi coarse pre-filter yang memanggil `evaluate_intent()` dengan `ActionIntent(tool_name=f"job.{type}", ...)`, BUKAN otoritas final (otoritas final tetap `ExecutionBroker`, lihat PRD-011).
5. `WTH_UNRESTRICTED` — DIPECAH jadi 3 dimensi independen (ADR-012). Env var lama jadi shim kompatibilitas selama transisi (compose dari 3 dimensi baru untuk backward-compat CLI flag), lalu dihapus sebagai runtime switch tunggal.

## Acceptance Criteria

- `check_command("bash -c 'id'")` pada mode `read_only` → `allowed=False`.
- `check_command("rm -rf /", mode=UNRESTRICTED)` → tetap `allowed=False`.
- `check_command("nmap example.com")` pada mode yang tidak mengizinkan `nmap` tapi mengizinkan tool bersubstring (mis. `nc`) → tetap `allowed=False` (substring bypass hilang).
- `PolicyEngine.evaluate(type="executor", safe_mode=True)` untuk task berisiko tinggi → `(False, ...)` kecuali unrestricted/approved eksplisit.
- Test matrix: kombinasi `(permission_profile × dimensi-scope-auth × dimensi-approval)` untuk command destruktif representatif — HARUS blocked kecuali kombinasi yang eksplisit diizinkan.
- `profiles.py` preset `bug-bounty`/`ctf-aggressive` TIDAK LAGI bisa men-set `require_authorized=False` — field itu dihapus dari skema preset yang bisa di-override, hanya `PermissionProfile`/rate saran yang bisa diset preset.

## Out of Scope

`ExecutionBroker` penuh (PRD-011) — PRD ini fokus ke KEPUTUSAN (policy), bukan pipeline eksekusi.

## Architecture Discrepancy (WU-005, implementasi)

FR#2 (6-layer `evaluate_intent()` penuh) dan FR#4 (`jobs/policy.py` integrasi)
di draft di atas TIDAK diimplementasikan utuh saat WU-005 landing — verifikasi
terhadap source membuktikan 4 dari 6 layer ADR-010 (Kill-switch/Environment/
Engagement/Agent-Capability) butuh infrastruktur yang belum ada
(`RunningActionRegistry`, `Engagement` store, `ExecutionBroker`), dan FR#4
sudah eksplisit di-defer ke WU-103 di `WORK-UNITS.md`nya sendiri
(`Depends: WU-008, WU-102`). Implementasi nyata: 3 layer aktif
(Global-Safety/Tool-Policy/Action-Policy) + dokumentasi eksplisit untuk 3
layer deferred (bukan stub kosong). Lihat `docs/planning/WORK-UNITS.md` WU-005
untuk rincian penuh keputusan scope. FR#2/#4 di atas tetap valid sebagai
target akhir, direalisasikan bertahap oleh WU-101/WU-102/WU-103/WU-106/WU-007.
