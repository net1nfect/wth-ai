# ADR-006: Automatic Model Routing — Ditolak

**Status**: Accepted (sebagai keputusan untuk TIDAK membangun sesuatu)
**Domain**: AI/Agent Architecture

## Decision

**Tidak membangun automatic model routing berbasis kompleksitas task.** Role-based static dispatch (7 role kanonik: planner/recon/executor/code/verifier/reporter/fallback, masing-masing hardcoded per agent class) sudah cukup sebagai mekanisme pemilihan model. Sebagai gantinya, dua langkah murah: (1) `ModelResponse.usage`/`cost_usd` (lihat ADR-003) jadi basis telemetry pasif — kalau data nanti menunjukkan pola pemborosan nyata, otomasi jadi keputusan terukur, bukan tebakan; (2) mekanisme **advisory** non-otomatis (`/model suggest`) yang membaca sinyal deterministik yang sudah ada (beberapa `grounding` violation beruntun, atau verifier berulang kali `needs_more_evidence`) dan MENYARANKAN upgrade model ke user, tidak pernah mengganti otomatis.

## Context

Tidak ada dynamic/automatic model routing berbasis task hari ini — murni role-based static dispatch dari config (`/model`, `/provider`, `/profile` — 7 preset builtin yang menukar seluruh mapping role→provider/model sekaligus).

## Alternatives

1. **Bangun classifier kompleksitas** (heuristik atau model kecil) yang memilih model per-task secara otomatis.
2. **Tetap role-based statis** (status quo, tanpa penambahan).
3. **Role-based statis + telemetry pasif + saran manual (non-otomatis)**.

## Chosen Approach

Opsi 3.

## Why

(a) 7 role kanonik SUDAH menyegmentasi berdasarkan FUNGSI (planner vs executor vs verifier), yang merupakan driver dominan untuk pemilihan model yang tepat (reasoning-berat vs tool-execution vs judgment) — ini menutup sebagian besar kebutuhan "model yang tepat untuk pekerjaan yang tepat" tanpa komponen baru. (b) Classifier kompleksitas yang andal & murah itu sendiri sulit dibangun — heuristik panjang-prompt/keyword rapuh, sementara model call tambahan untuk mengklasifikasi kompleksitas menambah biaya+latensi yang justru ingin dihindari. (c) Mis-routing (task sulit terkirim ke model lemah) adalah failure mode SENYAP yang menurunkan kualitas tanpa error yang terlihat — bertentangan dengan tema berulang di seluruh blueprint ini ("jangan menelan kegagalan secara diam-diam", lihat ADR-010).

Keputusan mengganti model yang mempengaruhi biaya sebaiknya tetap terlihat/operator-facing (opsi 3), konsisten dengan prinsip "POLICY DECIDES" — bukan heuristik tersembunyi lain yang ditumpuk di atas heuristik lain.

## Trade-offs

Operator harus manual mengelola trade-off biaya/kualitas per role lewat `/model`/`/profile` — tidak ada optimasi otomatis. Ini trade-off yang diterima sadar: transparansi dan predictability lebih diprioritaskan daripada optimasi biaya otomatis yang belum terbukti nilainya.
