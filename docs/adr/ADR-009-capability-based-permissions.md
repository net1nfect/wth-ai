# ADR-009: Capability-Based Permissions

**Status**: Accepted
**Domain**: Security/Platform Architecture

## Decision

`Capability`/`PermissionProfile` granular (per-tool risk, approval_mode, allow_credential_injection) menggantikan `MODE_TOOLS` flat allowlist sebagai unit enforcement. `PermissionMode` (5 nilai existing: `READ_ONLY_RECON`/`STANDARD_TESTING`/`ELEVATED_APPROVAL`/`FULL_LAB`/`UNRESTRICTED`) di-**KEEP** sebagai label ceiling backward-compatible — **TIDAK di-rename** ke vocabulary baru (`OBSERVE`/`RECON`/dst).

## Context

`MODE_TOOLS` (`permission_manager.py`) cuma tahu "tool X ada di set mode Y" — tidak bisa menyatakan rate per-tool, risk per-tool, approval-mode per-tool, atau guard kontekstual (mis. "hydra cuma boleh di lab disposable"). Bug ganda sudah terkonfirmasi: `bash`/`sh`/`zsh` di-skip TOTAL dari allowlist check (interpreter jadi celah universal), dan sisa cek pakai substring match bukan token match.

`PermissionMode` dipakai untuk **serialisasi** (`SessionState.to_dict/from_dict`, `ExecutorTask.from_dict`), slash command (`/permission elevated`), UI (`MODE_DESCRIPTIONS`), dan puluhan test — rename value adalah breaking change format sesi tersimpan.

## Alternatives

1. **Rename total** ke vocabulary baru (`OBSERVE`/`RECON`/`STANDARD`/`FULL AUTHORIZED`/`LAB`) — sesuai saran awal brief produk.
2. **Biarkan flat set**, tambal bug satu-satu.
3. **Capability granular, `PermissionMode` sebagai ceiling label yang tetap ada.**

## Chosen Approach

Opsi 3.

## Why

Opsi 1 blast radius besar (serialisasi, slash command, test) tanpa nilai tambah keamanan — masalah sebenarnya bukan namanya, tapi BAGAIMANA isinya di-resolve. Opsi 2 tidak menyelesaikan akar (tidak ada cara natural menyatakan "risk + approval_mode per tool" di flat set). Capability granular memungkinkan aturan presisi (interpreter passthrough → SELALU HIGH-risk+REQUIRE_HUMAN meski tool lain di mode sama LOW) yang mustahil dengan flat set, sambil `PermissionManager.check_command()` tetap kompatibel via adapter tipis (return-shape dict dipertahankan 1:1, assertion test existing tetap lolos).

Pemetaan konkret dari set existing (isi tools SAMA persis, cuma dibungkus granular): `read_only`→`shell.readonly` (LOW/AUTO), `standard`→`shell.active_recon`+`http.mutate` (MEDIUM/NOTIFY), `elevated`→`shell.active_scan_aggressive` (HIGH/REQUIRE_HUMAN), `full_lab`→`shell.exploit_heavy` (HIGH/REQUIRE_HUMAN + guard `authorization_source.kind`), `unrestricted`→wildcard `PROHIBITED` default kecuali `Engagement.allowed_operations` eksplisit mengizinkan (paling tinggi tetap `REQUIRE_HUMAN`, **tidak pernah AUTO** — fix arsitektural untuk "UNRESTRICTED=auto-approve").

## Trade-offs

Kompleksitas model bertambah (dari satu `dict[PermissionMode, set[str]]` ke beberapa dataclass) — diterima karena kompleksitas itu justru merepresentasikan nuansa risiko yang sebelumnya hilang/salah (bug argv/substring adalah GEJALA dari model yang terlalu sederhana untuk kebutuhan nyata).
