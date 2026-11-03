# ADR-005: Event-Driven vs Polling untuk Agent Reasoning Loop

**Status**: Accepted
**Domain**: AI/Agent Architecture

## Decision

Reasoning loop interaktif (`AgentToolLoop`) tetap **synchronous procedural call** (bukan event-driven) untuk jalur foreground/interaktif — user menunggu jawaban, LLM call genuinely butuh menunggu respons sebelum tahu tool apa selanjutnya. TAMBAHKAN jalur job-based (event+queue, memakai infrastruktur `jobs/`+`EventBus` yang **sudah ada dan terbukti bekerja**) sebagai opsi untuk `/hunt` versi long-running/background — job type baru `"engagement"`.

## Context

`AgentToolLoop`/`EngagementLoop` murni blocking `for` loop, memblokir CLI/TUI selama seluruh proses berjalan (dimitigasi persepsi lewat token-streaming callback, tapi tidak bisa detach/resume/survive-restart). Sementara `WorkerEngine`+`JobQueue` (SQLite, `jobs/worker.py`) SUDAH punya model job async yang tahan restart dengan `job.*` event lifecycle lengkap dan approval-resume yang benar-benar bekerja (`_resume_waiting()`) — TAPI tidak ada job type `"engagement"`/`"hunt"` (`JOB_TYPES` cuma `recon|executor|report|provider_test|custom`); `EngagementLoop` yang mengorkestrasi recon→plan→execute→verify→report secara utuh HANYA reachable sinkron dari CLI. `Job.correlation_id` ada di model tapi menganggur, seharusnya bisa mengaitkan sub-job recon/executor/report dalam satu engagement run.

## Alternatives

1. **Seluruh reasoning loop jadi event-driven** — setiap iterasi tool-call publish event, consumer async mendorong lanjutan (arsitektur reaktif penuh).
2. **Biarkan sinkron sepenuhnya** (status quo).
3. **Sinkron untuk interaksi turn-by-turn, TAPI tambah job type `"engagement"`** supaya `/hunt` bisa jalan sebagai job async yang di-tail lewat event.

## Chosen Approach

Opsi 3.

## Why

Opsi 1 mengubah model eksekusi paling mendasar — LLM call yang genuinely butuh menunggu respons sebelum tahu langkah berikutnya tidak natural jadi event-driven tanpa menambah kompleksitas besar untuk manfaat tak jelas pada percakapan turn-by-turn (user tetap harus menunggu jawaban apa pun arsitektur di baliknya). Opsi 2 membiarkan `/hunt` (operasi berpotensi panjang — 5 iterasi × beberapa tool call × LLM round-trip) tidak bisa didetach, hilang kalau terminal/proses mati, padahal infrastruktur job yang tepat SUDAH ADA dan **sudah dipakai sukses** untuk kasus serupa (`wth recon watch` mengandalkan job+event tanpa masalah).

## Trade-offs

Menambah job type baru berarti `EngagementLoop`/state-machine (lihat ADR-011 pola serupa) harus bisa berjalan di dalam `JobContext` (thread worker) — perlu memastikan tidak ada asumsi tersembunyi tentang berjalan di main thread CLI (mis. `_ui_callbacks()` orchestrator yang mengandalkan `self._tool_trace`/`self._ui_bus` yang terikat sesi CLI aktif). Ini P2 (`WU-014`), diaktifkan HANYA setelah unifikasi state-machine (ADR-011) matang — job runner butuh state yang predictable untuk resume yang benar, urutan dependency yang disengaja.
