# ADR-002: Task Representation — Flat List vs DAG

**Status**: Accepted
**Domain**: AI/Agent Architecture

## Decision

`ExecutorTask` tetap flat dataclass, ditambah `priority:int=5` dan `depends_on:list[str]=[]` yang bersifat **advisory** (dipakai untuk sorting saat mengambil task dari antrian, bukan hard scheduler gate dengan executor terpisah). **DAG data-structure dengan topological scheduler penuh DITOLAK secara eksplisit.**

## Context

`ExecutorTask` (`wth_ai/models.py:223-261`) tidak punya field dependency sama sekali. `SessionState.task_queue: list[dict]` adalah list datar, FIFO/by-id. Analisis SEMUA konsumen task_queue yang nyata:
- `ChainPlanner.chains_to_tasks()` — SATU exploit chain (mis. IDOR = baseline→swap_id→diff) jadi SATU `ExecutorTask`, urutan langkah sudah tertangani internal via `command_plan: list[dict]` DI DALAM satu task.
- `ExecutorPool.run_all_sync()` (`/execute --all`) — jalankan hingga 6 task **paralel independen**, tidak ada model dependency sama sekali di sini.
- `EngagementLoop` — ambil `task_queue[:3]` per iterasi, murni hard-cap "3 per iterasi", bukan karena ada dependency eksplisit.
- `AgentToolLoop` (jalur ReAct dominan) — tidak konsumsi `task_queue` sebagai graph sama sekali; LLM panggil `execute_task` satu per satu, urutan ditentukan reasoning model saat itu (ini SECARA IMPLISIT sudah "adaptive DAG traversal" oleh model, bukan struktur data eksplisit).

**Tidak ada satu pun konsumen nyata yang butuh dependency BERCABANG antar-task.**

## Alternatives

1. **DAG penuh** — struktur data graph + topological scheduler + deteksi siklus.
2. **Flat list apa adanya** (status quo) — tidak menutup gap prioritas/ordering yang genuinely ada.
3. **Flat list + `priority` + `depends_on` advisory** — sorting hint, bukan scheduler.

## Chosen Approach

Opsi 3.

## Why

Membangun DAG data-structure + scheduler adalah overengineering nyata di sini: tidak ada observed problem yang butuh percabangan kompleks (task B nunggu A dan C yang independen, semuanya paralel dengan percabangan), sementara biayanya nyata (scheduler, siklus-deteksi, UI untuk menampilkan graph, test coverage tambahan). Kebutuhan "urutan" yang genuinely ada (baseline sebelum swap_id) SUDAH tertangani oleh desain "satu chain = satu task, sub-langkah di `command_plan`" yang sudah benar untuk skala plan per giliran (belasan task, regenerasi tiap iterasi lewat replan — horizon pendek, bukan project plan multi-hari).

Field `depends_on` advisory menutup 90% nilai DAG (urutan logis antar-task independen yang kadang perlu diprioritaskan) dengan 10% biayanya (satu fungsi sort tambahan, bukan scheduler baru).

Sebagai bagian keputusan yang sama: dua field lama yang sudah ada tapi DEAD (`stop_condition`/`success_criteria` — string bebas, tidak pernah dibaca kode manapun untuk keputusan) di-WIRE ke mekanisme yang sudah ada dan murah: `EvidenceVault.validate_schema()` dipanggil tiap iterasi SEBELUM lanjut sisa `command_plan` suatu task, early-exit kalau sudah terpenuhi. Ini menyambungkan field yang sudah ditulis LLM di setiap plan (buang-buang generation effort kalau tidak dipakai) ke perilaku nyata, tanpa menambah struktur baru.

## Trade-offs

Kalau di masa depan muncul use-case genuinely butuh percabangan (mis. multi-akun paralel dengan titik sinkronisasi eksplisit), `depends_on` advisory tidak cukup — perlu revisit keputusan ini. Untuk skala rencana saat ini (belasan task per giliran, regenerasi tiap replan), risiko itu dinilai rendah dan tidak proporsional untuk investasi di muka.
