# ADR-011: Engagement Lifecycle State Machine

**Status**: Accepted
**Domain**: Security/Platform Architecture

## Decision

`Engagement.status` adalah `@property` read-only; satu-satunya mutator adalah `Engagement.transition(event, actor)`. TAPI penegakan SEBENARNYA tidak bergantung pada Python encapsulation saja — `ExecutionBroker.execute()` **fetch `Engagement` FRESH dari `EngagementStore` (SQLite) di SETIAP panggilan**, hard-deny kecuali `status == ACTIVE`. State machine (`loop/state_machine.py::EngagementStateMachine`, dipakai konsep table-driven-nya) direname `LoopStateMachine` untuk menghindari tabrakan penamaan (lihat §Naming di bawah) dan tetap dipakai murni untuk `AgentToolLoop`/`EngagementLoop` (workflow-phase, in-process, single-instance) — beda risk profile dari `Engagement` (authorization-lifecycle, lintas-proses, security-critical).

## Context

`EngagementStateMachine` (existing, `loop/state_machine.py`) sudah membuktikan pola "definisikan guard, harap semua orang pakai method-nya" **gagal**: `TRANSITIONS` dict-nya benar, tapi `engagement_loop.py` melakukan `self.sm.state = LoopState.X` LANGSUNG di 6 titik, melewati guard `.transition()` sepenuhnya — dan `self.sm.state` juga tidak pernah dibaca di tempat lain (write-only/vestigial).

Second-pass critique mengonfirmasi keputusan ini tepat: `AgentToolLoop.run()` sendiri butuh interrupt-check untuk kill-signal (`self._stopped` di `SupervisorAgent` di-set tapi tidak pernah dibaca) — sinyal ini murni in-process, cocok dengan property-guard biasa; sedangkan `Engagement.status` yang menentukan boleh-tidaknya eksekusi nyata (lintas sesi, lintas proses via job worker) butuh sumber kebenaran yang tidak bisa dipoke langsung dari memory manapun.

## Alternatives

1. **Hanya mengandalkan encapsulation Python** (private field + property) — Python tidak benar-benar mencegah akses `_status` langsung, dan codebase ini sudah terbukti melakukan pelanggaran semacam itu (`LoopState`).
2. **Fetch-fresh dari storage di broker sebagai lapis KEDUA**, encapsulation sebagai lapis pertama.

## Chosen Approach

Opsi 2 — defense-in-depth dua lapis.

## Why

Encapsulation SEBAGAI lapis pertama mencegah kesalahan tidak sengaja (typo, refactor ceroboh). Independent runtime check di broker SEBAGAI lapis kedua mencegah bahkan pelanggaran API yang disengaja/lupa — karena bukti konkret di codebase yang SAMA menunjukkan pelanggaran semacam itu benar-benar terjadi, bukan hipotetis.

**State machine `Engagement`**: `DRAFT → VALIDATING → READY → ACTIVE ↔ PAUSED → COMPLETED`, plus `(state manapun non-terminal) → ABORTED` (kill switch, lihat PRD-006) dan `ACTIVE/PAUSED → EXPIRED` (otomatis, `time_window.expires_at` terlewati). Guard per transisi bukan cuma existence-check: `READY→ACTIVE` mensyaratkan `approved_by is not None` **dan** `approved_by` BUKAN string actor agent (`"supervisor"/"planner"/"executor"/"recon"` ditolak eksplisit) — transisi ke status yang berarti "boleh testing" hanya bisa diatribusikan ke manusia.

## Trade-offs

Broker jadi harus selalu query storage (bukan trust in-memory reference) — sedikit overhead I/O per eksekusi, diterima karena korektnesnya lebih penting daripada mikro-optimasi di jalur yang toh sudah melakukan I/O (subprocess/HTTP).

### Naming Collision (temuan second-pass critique)

Codebase existing SUDAH punya 4 kelas berprefix `Engagement` yang semuanya tentang *hunt workflow*, BUKAN otorisasi: `EngagementLoop`, `EngagementMemory`, `EngagementStateMachine`, `EngagementMode`. Entity BARU (`Engagement`, authorization-lifecycle) berisiko tabrakan istilah nyata begitu dipakai meluas di dokumen. Keputusan: rename `EngagementStateMachine`→`LoopStateMachine` sekarang juga (murah, WU yang menyentuhnya sudah dijadwalkan — lihat `WORK-UNITS.md`); `EngagementLoop`→`HuntLoop` dan rename `EngagementMemory` **ditunda** ke WU yang sudah dijadwalkan menyentuh file itu (WU-013/WU-008 di ADR-001/ADR-004) — tidak perlu WU rename terpisah untuk keduanya.
