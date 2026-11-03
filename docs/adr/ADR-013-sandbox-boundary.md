# ADR-013: Sandbox Boundary — OS-Level, Bukan Container

**Status**: Accepted
**Domain**: Security/Platform Architecture

## Decision

Isolasi level-OS (non-root Docker user existing, cwd isolation dari `executor/sandbox.py::Sandbox` existing, env allowlist dari `shell_runner.py` existing, ditambah `resource.setrlimit` CPU/memory/nofile baru dan process-group kill via `Popen(start_new_session=True)`+`RunningActionRegistry`) + Execution Broker sebagai chokepoint kebijakan (ADR-008) **cukup** untuk v1. Container/microVM per-eksekusi **TIDAK direkomendasikan sekarang**.

## Context

Semua temuan CRITICAL/HIGH di audit keamanan adalah bug **kebijakan/otorisasi** (siapa boleh menyuruh apa) — bukan satu pun bug **sandbox-escape** (proses yang sudah lolos policy lalu kabur dari batasnya). `shell_runner.py` sendiri sudah benar secara teknis (`shell=False`, argv list, env allowlist) — masalahnya adalah command destruktif *lolos sampai ke situ* (via bug allowlist bypass, lihat ADR-009), bukan `shell_runner` gagal membatasi command yang sudah sah lolos.

Threat model produk saat ini: operator tunggal, engagement terotorisasi, tool dijalankan atas nama operator itu sendiri terhadap target yang mereka punya izin — bukan multi-tenant/untrusted request dari pihak lain yang tidak dipercaya.

## Alternatives

1. **Container-per-execution** (Docker-in-Docker/gVisor/Firecracker microVM) — isolasi kernel-level lebih kuat.
2. **nsjail/bubblewrap** tanpa container penuh — lebih ringan dari (1) tapi tetap kompleksitas tambahan.
3. **OS-level + broker.**

## Chosen Approach

Opsi 3.

## Why

Container (opsi 1) memberi isolasi kernel-level lebih kuat, tapi biaya rekayasa besar (image/network/volume policy per call), latensi signifikan untuk tool CLI interaktif yang mengharapkan respons tool-call cepat, dan — krusial — **tidak menutup satu pun dari kelas bug yang benar-benar ditemukan** di audit. Menambah container sekarang adalah overengineering relatif terhadap risiko yang benar-benar terverifikasi ada. nsjail/bubblewrap (opsi 2) sama-sama menambah kompleksitas untuk threat yang belum terbukti jadi vektor nyata.

## Trade-offs

**Trigger untuk revisit ADR ini** (harus ditinjau ulang, bukan dipertahankan selamanya): kalau WTH-AI suatu saat menjadi (a) **layanan hosted multi-tenant** (banyak operator tak saling percaya berbagi satu backend), atau (b) mulai mengeksekusi **kode exploit yang di-generate LLM tanpa review manusia** (bukan cuma memilih dari allowlist tool dikenal) — kedua kondisi itu mengubah threat model dari "batasi operator yang authorized" jadi "batasi kode tak dipercaya dari pihak ketiga", di mana isolasi kernel-level jadi proporsional. Dicatat sebagai backlog spike (`WU-205`, `docs/planning/01-gap-analysis.md`), bukan pekerjaan aktif.
