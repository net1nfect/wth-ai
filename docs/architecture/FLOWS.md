# Flows — Target Architecture (12 Diagram)

## 1. CLI Request Flow

```mermaid
flowchart TD
    U[Operator/Bot User] --> ENT[wth-main.py / cli.entry:main]
    ENT --> ORCH[WTHOrchestrator.handle_input]
    ORCH --> LIT{Literal command?}
    LIT -->|"/help /quit /stop"| DIRECT[Dispatch langsung]
    LIT -->|tidak| SLASH{starts with /}
    SLASH -->|ya| ROUTER[SlashRouter.route - PlatformRegistry single source]
    SLASH -->|tidak| SCOPE{scope_intent match}
    SCOPE -->|ya| PROPOSE[propose_target draft-only]
    SCOPE -->|tidak| INTENT[SkillRouter.detect_intent + session-aware fallback]
    INTENT -->|chat| CHAT[ChatAgent.respond]
    INTENT -->|assess| SUP[SupervisorAgent.run]
```

## 2. General Chat Flow

```mermaid
flowchart TD
    U[User message] --> CTX[ContextBuilder.build sources=action_memory,md_context,engagement_graph]
    CTX --> LLM[LLMProviderRouter.generate role=planner]
    LLM --> RESP[ModelResponse]
    RESP --> GROUND[GroundingFirewall.validate_claims]
    GROUND --> OUT[Balasan ke user]
```

## 3. Recon Flow

```mermaid
flowchart TD
    CMD["/recon run target"] --> JOB[JobManager.create type=recon]
    JOB --> POLICY[PolicyEngine.evaluate coarse pre-filter]
    POLICY -->|deny| REJECT[Job rejected + audit]
    POLICY -->|allow| Q[JobQueue SQLite]
    Q --> WORKER[WorkerEngine.run_job]
    WORKER --> ENGINE[ReconEngine.run]
    ENGINE --> INTENT2[ActionIntent per tool-adapter]
    INTENT2 --> BROKER[ExecutionBroker.execute]
    BROKER --> RUNNER[SafeToolRunner / builtin module]
    RUNNER --> NORM[Normalizer]
    NORM --> GRAPH[AssetGraph.merge]
    GRAPH --> DIFF[diff vs baseline]
    DIFF --> EVENT["EventBus.emit recon.change_detected"]
    EVENT --> REPORT[recon_report.md]
```

## 4. Autonomous Hunting Loop (/hunt)

```mermaid
flowchart TD
    START["/hunt goal"] --> LSM1[LoopStateMachine.transition RECON]
    LSM1 --> RECONP[Recon phase]
    RECONP --> LSM2[transition PLANNING]
    LSM2 --> PLAN[PlannerAgent.plan + ClaimStore.create_claim_from_hypothesis]
    PLAN --> LSM3[transition EXECUTING]
    LSM3 --> EXEC["execute_module via ExecutionBroker top-3 task"]
    EXEC --> LSM4[transition VERIFYING]
    LSM4 --> VERIFY[VerifierAgent.verify]
    VERIFY --> SEV{severity critical/high verified?}
    SEV -->|ya| LSM5[transition REPORTING]
    SEV -->|tidak| BUDGET{LoopBudget exhausted or kill-signal?}
    BUDGET -->|tidak| LSM6[transition REPLANNING] --> LSM2
    BUDGET -->|ya| LSM5
    LSM5 --> REPORT2[ReportBuilderAgent.build + validate Claim]
    REPORT2 --> LSM7[transition DONE]
```

## 5. Permission Flow

```mermaid
flowchart TD
    INTENT3[ActionIntent] --> L0{Kill-switch aktif?}
    L0 -->|ya| DENY0[DENY kill_switch_active]
    L0 -->|tidak| L1{Global denylist match?}
    L1 -->|ya| DENY1[DENY global_denylist - argv tokenized, no skip]
    L1 -->|tidak| L2{Environment forbids?}
    L2 -->|ya| DENY2[DENY environment_policy]
    L2 -->|tidak| L3{Engagement ACTIVE + in-scope + not forbidden?}
    L3 -->|tidak| DENY3[DENY engagement_*]
    L3 -->|ya| L4{Actor allows tool?}
    L4 -->|tidak| DENY4[DENY actor_not_permitted]
    L4 -->|ya| L5{Capability resolve?}
    L5 -->|tidak| DENY5[DENY tool_not_in_matrix]
    L5 -->|ya| L6[Compute risk = maxcapability,pattern,tainted]
    L6 --> L6B{risk PROHIBITED?}
    L6B -->|ya| DENY6[DENY action_policy_prohibited]
    L6B -->|tidak| L6C{approval_mode REQUIRE_HUMAN or risk HIGH or tainted?}
    L6C -->|ya| APPROVAL[REQUIRE_APPROVAL + action_hash]
    L6C -->|tidak| ALLOW[ALLOW]
```

## 6. Approval Flow

```mermaid
flowchart TD
    REQ[REQUIRE_APPROVAL + action_hash] --> CHECK{ApprovalRegistry.is_approved hash?}
    CHECK -->|ya, status=approved| CONSUME[Eksekusi + status to CONSUMED]
    CHECK -->|tidak| CREATE[Buat/refresh ApprovalRecord pending]
    CREATE --> NOTIFY[Notifikasi operator - CLI prompt / bot owner-only]
    NOTIFY --> HUMAN{Human approve/deny/timeout?}
    HUMAN -->|approve| APPROVED[status=approved] --> CHECK
    HUMAN -->|deny| DENIED[status=denied, blocked]
    HUMAN -->|timeout| EXPIRED[status=expired, blocked]
    HUMAN -->|killswitch| KILLED2[status=killed]
```

## 7. Tool Execution Flow

```mermaid
flowchart TD
    ALLOW2[Policy ALLOW / approval CONSUMED] --> RATE{Rate/concurrency budget?}
    RATE -->|tidak cukup| DEFER[DEFER rate_limited]
    RATE -->|ok| SANDBOX[Sandbox setrlimit + start_new_session]
    SANDBOX --> REGISTER[RunningActionRegistry.register pid,pgid]
    REGISTER --> RUN["shell_runner.run_argv / requests.request"]
    RUN --> DEREGISTER[RunningActionRegistry.deregister]
    DEREGISTER --> AUDIT2["AuditLogSink.write sync jika risk>=MEDIUM"]
    AUDIT2 --> RESULT[ExecutionResult]
```

## 8. Evidence Flow

```mermaid
flowchart TD
    RESULT2[ExecutionResult] --> MATCH{match intent.evidence_schema / claim_ids?}
    MATCH -->|tidak| SKIP[Tidak ada auto-store]
    MATCH -->|ya| STORE["EvidenceVault.store - redact + correlation_id"]
    STORE --> APPEND[ClaimStore.append_evidence ke claim existing]
    APPEND --> FALLBACK{EvidencePipeline temukan evidence yatim?}
    FALLBACK -->|ya| ATTACH[Tempel sebagai referensi - TIDAK tulis vault baru]
    FALLBACK -->|tidak| DONE2[Selesai]
```

## 9. Finding Validation Flow

```mermaid
flowchart TD
    CLAIM2[Claim confidence tinggi + evidence_refs terisi] --> PROMOTE{Layak jadi Finding?}
    PROMOTE -->|ya| FINDING[FindingsDB.add_finding state=CANDIDATE]
    FINDING --> VERIFY2[VerifierAgent.verify]
    VERIFY2 --> WEIGHT{captured_via broker_execution vs llm_assertion}
    WEIGHT --> DECIDE{Cukup bukti?}
    DECIDE -->|ya| VERIFIED2["state -> VERIFIED - transition guard"]
    DECIDE -->|tidak| FP[state -> FALSE_POSITIVE / needs_more_evidence]
```

## 10. Model Routing Flow (role-based, TANPA auto-routing — ADR-006)

```mermaid
flowchart TD
    CALL[Agent butuh LLM] --> ROLE["role literal hardcoded: planner|executor|verifier|reporter|recon"]
    ROLE --> REQ2[ModelRequest role=X]
    REQ2 --> ROUTER2[LLMProviderRouter.generate]
    ROUTER2 --> RESOLVE["_role_config role -> provider+model dari config statis"]
    RESOLVE --> FAILOVER{Provider available?}
    FAILOVER -->|tidak| NEXT[Coba provider berikutnya di failover_order]
    FAILOVER -->|ya| CALLLLM[Panggil provider.chat]
    CALLLLM --> NORM2["ModelResponse ternormalisasi - usage untuk cost budget"]
```

## 11. Checkpoint/Resume Flow

```mermaid
flowchart TD
    RESTART[CLI restart / resume session_id] --> LOAD[SessionStore.load]
    LOAD --> ENGID{engagement_id ada?}
    ENGID -->|ya| FETCH[EngagementStore.fetch - status fresh]
    ENGID -->|tidak, sesi lama| MIGRATE[Compat shim - derive Scope view]
    FETCH --> STATUS{status ACTIVE/PAUSED?}
    STATUS -->|EXPIRED/ABORTED/COMPLETED| BLOCK2[Tidak bisa lanjut eksekusi - read-only]
    STATUS -->|ACTIVE/PAUSED| REHYDRATE[Rehydrate AssetGraph, EvidenceVault, ClaimStore, ActionMemory]
    REHYDRATE --> RESUME2[Lanjut sesi]
```

## 12. Failure/Retry Flow

```mermaid
flowchart TD
    ERR[Exception saat execute] --> KIND{ModelResponse.error.kind atau ExecutionResult.deny_reason}
    KIND -->|timeout| KILL2[RunningActionRegistry.kill - process-group, tidak leak]
    KIND -->|rate_limit| BACKOFF[Backoff, jangan retry mutation otomatis]
    KIND -->|policy_denied/engagement_not_active| STOPLOOP["Loop stop segera - jangan habiskan max_steps"]
    KIND -->|provider unavailable| FAILOVERNEXT[Failover ke provider berikutnya, max 1x backup]
    KILL2 --> AUDITERR[audit_log event_type=execution.failed atau invalid_state_transition]
    BACKOFF --> AUDITERR
    STOPLOOP --> AUDITERR
    FAILOVERNEXT --> AUDITERR
```
