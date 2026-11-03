# Implementation Roadmap

> Dependency chain nyata (dari `WORK-UNITS.md`), bukan Phase 1/2/3 generik. Setiap grup di bawah bisa dikerjakan begitu grup di atasnya (yang ditunjuk panah) selesai — beberapa grup independen bisa paralel.

```
Surgical Bug Fixes (independen satu sama lain, paling aman dikerjakan duluan)
  WU-001 (AssessmentPhase) ─┐
  WU-002 (role-wiring)       │
  WU-004 (check_command)     │  -- semua bisa paralel, tidak saling depend
  WU-009 (bot fail-closed)   │
  WU-013 (plugin CWD)        │
  WU-210 (write_env_file)   ─┘
        │
        ▼
Split Master-Switch + Policy Core
  WU-003 (pecah WTH_UNRESTRICTED)
        │
        ├──▶ WU-008 (fix jobs/policy.py fallthrough)
        │
        ▼
  WU-005 (Capability Matrix + evaluate_intent core)  ◄── depends WU-004
        │
        ├──▶ WU-010 (ExecuteRpcServer content validation)
        ├──▶ WU-012 (guard sinkron executor/vuln/* — P0 sempit, AGENT-02 live)
        │
        ▼
State Machine Foundation
  WU-001 (sudah selesai di atas) ──▶ WU-006 (LoopStateMachine enforcement + AgentToolLoop)
        │
        ▼
Approval Unification
  WU-005 ──▶ WU-007 (unifikasi approval + action-hash + context.tainted)
        │
        ▼
Trust Boundary Tool
  WU-007 ──▶ WU-011 (scope_set→propose_target, permission_set→request_profile_upgrade)
        │
        ▼
Engagement Skeleton
  WU-003 ──▶ WU-014 (Engagement entity + FSM, skeleton)

════════════════ CHECKPOINT: semua P0 di atas = tutup 7 CRITICAL finding + 3 HIGH finding audit ════════════════

        ▼
Execution Broker Full
  WU-014, WU-007 ──▶ WU-101 (EngagementStore SQLite)
  WU-101 ──▶ WU-102 (ExecutionBroker core + RunningActionRegistry + AgentTerminalBridge facade)
        │
        ├──▶ WU-103 (finalisasi job-policy adapter)
        ├──▶ WU-104 (rutekan ExecuteRpcServer final + executor/vuln/* penuh + ReconEngine bertahap)
        ├──▶ WU-105 (audit log durable)
        ├──▶ WU-106 (kill switch) ──▶ WU-108 (fix reliability timeout/leak, berbagi mekanisme)
        └──▶ WU-107 (vault→broker credential injection)

Model Abstraction (independen, bisa paralel dengan Execution Broker Full)
  WU-109 (ModelRequest/Response) ──▶ WU-110 (model capability registry)

Memory/Context (independen, tapi harus sebelum Scope migrasi Fase B)
  WU-111 (ContextBuilder) ──▶ WU-112 Fase B (Scope→Engagement call-site baru)
  WU-014 ──▶ WU-112 Fase A (compat shim, bisa lebih awal)
  WU-113 (merge EngagementMemory→AssetGraph, independen)

Evidence/Claim System
  WU-102 ──▶ WU-114 (Claim schema + ClaimStore + planning-time creation)
  WU-114 ══(WAJIB BERSAMAAN)══▶ WU-115 (hapus 7 call-site duplikasi lama)
  WU-114 ──▶ WU-116 (transition guard FindingState/Claim)

Task Representation (independen)
  WU-117 (ExecutorTask priority/depends_on/stop_condition wiring)

CLI Cleanup (independen, paling akhir karena low-risk/low-urgency)
  WU-118 (konsolidasi slash-registry)
  WU-119 (detect_intent session-aware)

════════════════ CHECKPOINT: P1 selesai = arsitektur target terpasang penuh ════════════════

        ▼
P2 — Hardening & Debt
  WU-201 (EngagementLoop→HuntLoop thin preset)
  WU-202 (job type "engagement" async)
  WU-203 (SSRF guard, redaksi diperluas)
  WU-204 (setrlimit penuh)
  WU-205 (spike container — HANYA jika trigger)
  WU-206 (Standing Approval)
  WU-207 (hapus legacy code)
  WU-208 (fix entry point resmi)
  WU-209 (.dockerignore, CI scanning)
  WU-211 (npm installer checksum)
```

## Rekomendasi Urutan Eksekusi Konkret (bukan cuma dependency graph — urutan yang disarankan)

1. **Sprint 1**: Semua "Surgical Bug Fixes" (6 WU independen) — nilai langsung, risiko rendah, tidak saling blokir.
2. **Sprint 2**: WU-003 → WU-008, WU-005 → WU-010/WU-012. Ini menutup AUTHZ-01/02/03/05, NET-02, dan AGENT-02 (live bug).
3. **Sprint 3**: WU-006 (state machine) → WU-007 (approval unification) → WU-011 (trust boundary tool). Ini menutup AUTHZ-04, sebagian besar AGENT-01.
4. **Sprint 4**: WU-009 (kalau belum di sprint 1) + WU-014 (Engagement skeleton). **Checkpoint P0 selesai** — jalankan test matrix keamanan penuh sebelum lanjut P1.
5. **Sprint 5-7**: Execution Broker (WU-101→108) — ini investasi terbesar P1, sengaja dikerjakan sebagai blok berurutan karena saling depend erat.
6. **Sprint 8** (paralel dengan 5-7 kalau kapasitas ada): Model Abstraction (WU-109/110) + Task Representation (WU-117) — independen total dari Execution Broker.
7. **Sprint 9**: ContextBuilder (WU-111) → Scope migrasi (WU-112) → merge EngagementMemory (WU-113).
8. **Sprint 10**: Claim/Evidence (WU-114+115 BERSAMAAN, lalu WU-116).
9. **Sprint 11**: CLI cleanup (WU-118/119) — low-risk, kapan saja setelah P0.
10. **P2**: backlog, dikerjakan sesuai kapasitas, tidak ada urgensi keamanan.

## Milestone Verifikasi

- **Setelah P0**: jalankan test matrix `(permission_profile × scope-auth-dimensi × approval-dimensi)` untuk command destruktif representatif — SEMUA harus blocked kecuali kombinasi eksplisit diizinkan. 334 test existing tetap hijau.
- **Setelah Execution Broker (WU-102)**: verifikasi SEMUA call site lama (`AgentTerminalBridge.dispatch_tool()`) masih berfungsi identik tanpa perubahan behavior yang diamati.
- **Setelah WU-114+115**: verifikasi tidak ada evidence dobel di PoC export report untuk skenario end-to-end (`/execute` satu task → cek `EvidenceVault` cuma punya 1 entry per eksekusi).
- **Akhir P1**: seluruh 14 Security Invariant (`WTH-AI-MASTER-ARCHITECTURE.md` §26) punya test otomatis yang membuktikannya.
