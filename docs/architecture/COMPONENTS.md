# Component Architecture — Target

## Control Plane (keputusan, bukan eksekusi)

| Komponen | Lokasi target | Peran |
|---|---|---|
| `Engagement`/`EngagementStore` | `wth_ai/engagement/model.py`, `engagement/store.py` (BARU) | Aggregate root otorisasi, FSM lifecycle |
| `PolicyEngine.evaluate_intent()` | `wth_ai/policy/engine.py` (BARU) | 6-layer fail-closed decision |
| `Capability`/`PermissionProfile` | `wth_ai/policy/capability.py` (BARU), adapter di `permission_manager.py` | Resolusi granular risk/approval per tool |
| `ApprovalRegistry` (diperluas) | `wth_ai/policy/approval.py` (extend `approvals.py`) | Action-hash, single-use, standing approval |
| `KillSwitch` | `wth_ai/killswitch.py` (BARU) | Emergency stop 3 permukaan |

## Execution Plane (eksekusi nyata)

| Komponen | Lokasi target | Peran |
|---|---|---|
| `ExecutionBroker` | `wth_ai/execution/broker.py` (BARU) | Chokepoint tunggal — satu-satunya pemanggil primitive |
| `RunningActionRegistry` | `wth_ai/execution/registry.py` (BARU) | Process-group tracking untuk kill-switch |
| `shell_runner`/`tool_runner` | KEEP existing | Subprocess primitive (shell=False, argv) |
| `Sandbox` | KEEP existing + `resource.setrlimit` | cwd isolation, resource limit |
| `SecretVault` | KEEP existing (`vault.py`) | Resolve `vault:` reference di titik eksekusi |

## Agent Runtime

| Komponen | Lokasi target | Peran |
|---|---|---|
| `AgentToolLoop` | `wth_ai/agents/tool_loop.py` (REFACTOR) | ReAct loop, +`LoopBudget`, +`LoopStateMachine`, +kill-signal check |
| `SupervisorAgent`/`SupervisorContext` | `wth_ai/agents/supervisor*.py` (REFACTOR) | Entry point single-turn, pemegang `tainted` flag |
| `PlannerAgent`/`brain/*` | KEEP struktur, tambah `create_claim_from_hypothesis()` | Hunter→Critic→Feasibility, rule-based hypothesis |
| `ExecutorAgent`/`executor/vuln/*` | REFACTOR — wajib panggil broker | IDOR/SSRF/XSS/auth/race modules |
| `VerifierAgent` | REFACTOR — bobot `captured_via` | Satu-satunya yang boleh set `VERIFIED`/`FALSE_POSITIVE` |
| `ReportBuilderAgent` | REFACTOR — validasi `Claim` sebelum tulis | Grounding di titik paling visible |
| `GroundingFirewall` | KEEP + `validate_claim()` baru | Anti-halusinasi, regex + structured |
| `ClaimStore` | `wth_ai/claims/store.py` (BARU) | Sepupu `FindingsDB` pattern |
| `ContextBuilder` | `wth_ai/context/builder.py` (BARU) | Satukan 2 jalur context, `SOURCE_REGISTRY` |
| `LLMProviderRouter.generate()` | `wth_ai/llm/router.py` (REFACTOR aditif) | Canonical `ModelRequest`/`ModelResponse` |
| `model_catalog` | `wth_ai/llm/model_catalog.py` (BARU) | Capability registry terisi data nyata |

## Data/Evidence

| Komponen | Lokasi target | Peran |
|---|---|---|
| `AssetGraph` | KEEP + aktivasi edge type nganggur | Attack-surface knowledge, terima merge `EngagementMemory` |
| `EvidenceVault` | KEEP + `correlation_id` field | Exhibit locker, redaksi — HANYA broker yang boleh `.store()` |
| `AuditLogSink` | `wth_ai/audit/sink.py` (BARU) | Subscriber `EventBus`, tulis `audit_log` SQLite |
| `EventBus` | KEEP sepenuhnya + `EVENT_TYPES` baru | Pub/sub in-process, ring-buffer UI |

## Interfaces

| Komponen | Lokasi target | Peran |
|---|---|---|
| `wth-main.py`/`cli/entry.py` | REFACTOR (fix broken official entrypoint) | Click CLI |
| `PlatformRegistry` | REFACTOR jadi single source of truth | Command metadata, `/help`, autocomplete |
| `SlashRouter` | REFACTOR (serap tier-1 hardcoded) | Dispatch nyata |
| `bots/discord_bot.py`/`telegram_bot.py` | REFACTOR (fail-closed) | Kanal remote, di-cap `PermissionProfile` |
| `AgentTerminalBridge` | REFACTOR jadi facade broker | Titik integrasi tool_loop/supervisor_dispatch, TIDAK berubah signature |
| `ExecuteRpcServer` | REFACTOR (panggil broker) | RPC eksekusi kode |

## Kode Legacy (bukan komponen target — DEPRECATE)

`core/`, `agents/` root, `exploitation/`, `reconnaissance/`, `learning/`, `integration/`, `reporting/` root, `utils/`, `main.py` — orphaned, tidak masuk arsitektur target. Lihat `MIGRATION-MATRIX.md`.
