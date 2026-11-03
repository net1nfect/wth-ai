# ADR-012: Memecah `WTH_UNRESTRICTED` jadi Dimensi Independen

**Status**: Accepted
**Domain**: Security/Platform Architecture

## Decision

Hapus satu env var/flag yang mengontrol capability-ceiling + scope-authorization + human-approval sekaligus. Jadikan **tiga dimensi yang secara independen dikonfigurasi dan dicek**: `PermissionProfile` (capability ceiling, ADR-009), `Engagement.status`/`authorization_source` (scope authorization, ADR-011), `Capability.approval_mode`/`ApprovalRegistry` (human approval, PRD-006). **Tidak ada satu switch yang menyentuh ketiganya.**

## Context

Terverifikasi **6 call-site independen** bercabang dari SATU `is_unrestricted()`: `recon/engine.py:99` (skip authorized check), `recon/registry.py:131` (enable semua tool termasuk risk=high), `recon/planner.py:169,178` (skip allowlist/risk-gating), `jobs/policy.py:40` (allow semua job type — walau sudah no-op karena bug ADR-010), `supervisor_dispatch.py:155,202`+`config.py::auto_approve_unrestricted=True` (default!) (auto-approve `run_shell`/`http_probe`/`execute_task` TANPA `/approve` manusia), DAN **`profiles.py` preset `bug-bounty`/`ctf-aggressive`** (baris 93,101) — preset PALING mungkin dipakai user nyata untuk use-case utama produk — membundel `unrestricted=True`+`require_authorized=False`+`rate_limit_rps=0` jadi SATU pilihan `wth profile apply bug-bounty`.

Default `WTH_UNRESTRICTED="1"` di SEMUA file konfigurasi/deployment resmi (`docker-compose.yaml`, `Dockerfile`, `.env.example`, `.env.docker.example`) — siapa pun yang `docker compose up` sesuai README mendapat instance dengan authorization+approval mati sekaligus.

## Alternatives

1. **Ubah default `WTH_UNRESTRICTED` jadi `"0"` saja**, tanpa memecah dimensinya — tambalan cepat (ini yang direkomendasikan `AUDIT_REPORT.md` sebagai P0 tercepat).
2. **Hapus `UNRESTRICTED`/`FULL_LAB` sepenuhnya** — produk secara eksplisit butuh mode paling permisif untuk lab/CTF yang sah.
3. **Tiga dimensi independen**, tidak ada satu switch yang menyentuh ketiganya.

## Chosen Approach

Opsi 3.

## Why

Opsi 1 tidak menyelesaikan akar: siapa pun (atau LLM manapun via tool `permission_set`/`scope_set` — lihat AGENT-01) yang menyalakannya lagi tetap membuka SEMUA pintu sekaligus. Opsi 2 menolak kebutuhan produk yang sah. Memecah dimensi adalah SATU-SATUNYA cara membuat larangan eksplisit pemilik produk ("bahkan mode paling permisif tetap wajib punya explicit engagement + audit trail + resource limit + operator visibility + kill switch") benar secara ARSITEKTURAL, bukan cuma default value yang kebetulan aman hari ini dan bisa berubah besok.

## Trade-offs

Migrasi menyentuh 6+ call-site sekaligus, harus dikoordinasikan sebagai SATU perubahan (bukan tambal satu file lalu lupa yang lain) — risiko regresi lebih tinggi daripada tambal satu file, dimitigasi dengan test matrix eksplisit (kombinasi 3 dimensi × command destruktif representatif) sebelum landing. Ini prioritas P0 tertinggi justru karena cakupannya luas — menunda migrasi ini berarti menunda fix untuk finding CRITICAL paling berdampak di seluruh audit.
