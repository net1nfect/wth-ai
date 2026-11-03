# State Machines — Target Architecture

## 1. `Engagement` (Authorization Lifecycle)

```
DRAFT ──submit_for_validation──▶ VALIDATING ──validation_failed──▶ DRAFT
                                       │
                                validation_passed
                                       ▼
                                    READY ──activate(human)──▶ ACTIVE
                                                                  │  ▲
                                                          pause() │  │ resume()
                                                                  ▼  │
                                                                PAUSED
                                    ACTIVE/PAUSED ──finalize()──▶ COMPLETED
      (state manapun non-terminal) ──killswitch/abort()──▶ ABORTED   [terminal]
      ACTIVE/PAUSED ──time_window.expires_at terlewati──▶ EXPIRED     [terminal, otomatis]
```

**Guard per transisi**: `DRAFT→VALIDATING`: `len(targets)>=1`. `VALIDATING→READY`: `authorization_source is not None`, `permission_profile` valid, tidak ada konflik `allowed_operations ∩ forbidden_operations`. `READY→ACTIVE`: `approved_by is not None` **dan** bukan actor-agent string. `→ABORTED`: selalu boleh dari state non-terminal (fail-safe: gampang berhenti, susah lanjut).

**Enforcement**: property read-only + `.transition()` sebagai satu-satunya mutator (lapis 1), DAN `ExecutionBroker` fetch fresh dari `EngagementStore` tiap `execute()` — hard-deny kecuali `ACTIVE` (lapis 2, independen dari lapis 1). Lihat ADR-011.

## 2. `LoopStateMachine` (rename dari `EngagementStateMachine` — Workflow Phase)

```
IDLE ──▶ RECON ──▶ PLANNING ──▶ EXECUTING ──▶ VERIFYING ──┬──▶ REPORTING ──▶ DONE
                       ▲                                    │
                       └──────────── REPLANNING ◄───────────┘
(state manapun) ──▶ STOPPED
```

Dipakai `AgentToolLoop` (via mapping tool→state di `dispatch_supervisor_tool`) DAN `EngagementLoop`/`HuntLoop` — bukan cuma satu. `.transition()` emit event + raise `InvalidTransitionError` (mode strict) — event masuk `invalid_state_transition`, TERPISAH dari `execution.failed` generik.

## 3. Job Status (existing, sudah cukup matang — KEEP, tambah adapter policy)

```
queued ──▶ running ──▶ completed
   ▲          │  └────▶ failed
   │          └────▶ waiting_approval ──approved──▶ queued
   │                        └──denied/expired──▶ failed
   └──stop_job()──▶ stopped
   cancelled (API eksternal)
```

`TERMINAL_STATUSES = {completed, failed, stopped, cancelled}` — sudah eksplisit. **Tidak diubah jadi Enum** (string tuple tetap, low-risk) tapi `PolicyEngine` job-admission jadi adapter tipis ke `evaluate_intent()` (ADR-010).

## 4. `Claim.verification_status` (REUSE `FindingState` enum)

```
HYPOTHESIS ──(task terkait dieksekusi)──▶ NEEDS_EVIDENCE ──(evidence_refs terisi)──▶ CANDIDATE
                                                                                          │
                                                                            HANYA VerifierAgent
                                                                                          │
                                                                    ┌─────────────────────┴──────────┐
                                                                    ▼                                 ▼
                                                                VERIFIED                       FALSE_POSITIVE
```

`ACCEPTED_RISK`/`FIXED`/`RETEST_PASSED` (3 nilai enum tersisa) — lifecycle pasca-laporan, dipicu manual operator lewat CLI (bukan agent). Transition guard baru (pola sama `LoopStateMachine.TRANSITIONS`) melindungi `Finding.state` DAN `Claim.verification_status` sekaligus (`FindingsDB.update_finding_state()` saat ini TANPA guard — fix WU terkait).

## 5. Approval Status (`ApprovalRegistry`, diperluas)

```
pending ──approve()──▶ approved ──(1x eksekusi sukses)──▶ consumed
   │                                                          │
   └──deny()/expire──▶ denied/expired                  (approval baru wajib
   │                                                     untuk eksekusi ulang,
   └──killswitch──▶ killed                               kecuali StandingApproval
                                                           eksplisit — capability+
                                                           target pattern, max_uses,
                                                           expires_at, TIDAK PERNAH
                                                           utk risk HIGH/PROHIBITED)
```

## 6. Session (checkpoint/resume, existing — KEEP struktur)

Tidak diubah signifikan — `SessionState` tetap JSON overwrite per save, `engagement_id` field aditif menghubungkan ke `Engagement` FSM di atas (§1) tanpa duplikasi state.

## 7. `AgentToolLoop` — Interrupt Points (BARU, tambahan pasca critique)

Setiap iterasi `for iteration in range(budget.max_steps)` mengecek DUA hal sejajar sebelum lanjut ke LLM call berikutnya:
1. `budget.exhausted()` — steps/wall/cost/consecutive-failures/no-progress.
2. **Kill-signal** — `self._stopped`/subscribe `EventBus` untuk `killswitch.activated` scope ke `engagement_id` sesi ini (temuan critique: `self._stopped` di `SupervisorAgent` di-set tapi TIDAK PERNAH dibaca hari ini — WU-004 wajib menutup gap ini).

Juga: `ExecutionResult.deny_reason` yang typed (`engagement_not_active`/`policy_denied`/`approval_required`/`rate_limited`) memungkinkan loop **stop segera** kalau deny berasal dari kondisi yang tidak akan berubah dalam sisa turn ini (mis. `engagement_not_active` — tidak ada gunanya retry berulang sampai `max_steps` habis).
