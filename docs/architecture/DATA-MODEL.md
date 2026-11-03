# Data Model — WTH-AI Target Architecture

> Skema lengkap seluruh entity/dataclass target, dengan relasi. Rasional tiap keputusan ada di ADR terkait (`docs/adr/`). Ini adalah versi FINAL pasca second-pass critique — sequencing/revisi dari critique sudah diintegrasikan (ditandai eksplisit).

## 1. `ActionIntent` — Batas "LLM PROPOSES"

```python
@dataclass
class ActionIntent:
    intent_id: str
    actor: str                                # supervisor|planner|executor|recon
    tool_name: str
    arguments: dict[str, Any]
    task_id: str = ""
    claim_ids: list[str] = field(default_factory=list)    # lihat §5 — HARUS sudah ada SEBELUM broker jalan
    permission_required: str = "read_only"    # KLAIM proposer, BUKAN keputusan final
    risk_hint: str = "low"                    # advisory
    justification: str = ""
    evidence_schema: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
```

Diajukan agent/planner ke `ExecutionBroker`. Tidak membawa keputusan izin — hanya deklarasi niat. `permission_required`/`risk_hint` dicatat ke audit sebagai advisory, TIDAK PERNAH dipakai sebagai keputusan (penegakan literal ADR-010 "POLICY DECIDES").

## 2. `BrokerContext` — Provenance & Trust

```python
@dataclass
class BrokerContext:
    engagement_id: str
    session_id: str
    tainted: bool = False           # lihat §2.1 — revisi lokasi penyimpanan pasca critique
    tainted_excerpt: str = ""
    correlation_id: str = field(default_factory=lambda: f"COR-{uuid4().hex[:10]}")
```

### 2.1 `tainted` — Revisi Lokasi Penyimpanan (pasca second-pass critique)

**Keputusan awal** (draft pertama): simpan di instance `AgentToolLoop`. **DIKOREKSI**: ada **3 titik instansiasi `AgentToolLoop` independen** (`SupervisorAgent._run_loop`, `ExecutorAgent._execute_with_tools`, `PlannerAgent._probe_phase`), masing-masing objek baru per pemanggilan, tidak saling kenal — kalau `tainted` disimpan di situ, taint dari fetch eksternal di dalam sub-loop `ExecutorAgent` tidak akan pernah terbawa balik ke loop `SupervisorAgent` yang memanggilnya (padahal itu skenario risiko utama).

**Keputusan final**: `tainted_since_last_human_turn` disimpan di **`SupervisorContext`** (`agents/supervisor_context.py`) — objek yang SUDAH dibagi ke semua actor lewat `dispatch_supervisor_tool(ctx, ...)` dan bertahan sepanjang lifetime orchestrator.
- **Set**: di `tool_loop.py:104-108`, tepat sebelum tool-result masuk sebagai pesan `role:"tool"`. **Default-taint (fail-closed)**: SEMUA tool men-trigger taint KECUALI whitelist kecil murni-internal (`scope_show`, `session_status`, `list_findings`, `query_graph`) — bukan allowlist tool-tertentu-yang-taint, supaya tool baru (mis. MCP server baru) yang menyentuh data eksternal tidak lolos tanpa taint karena lupa didaftarkan.
- **Reset**: di titik masuknya pesan manusia asli — `SupervisorAgent.run()` (saat `role:"user"` baru di-append), termasuk pseudo-turn "approve" di `_handle_approval()` (genuinely pesan baru dari manusia).

## 3. `Engagement` — Aggregate Root Otorisasi

```python
class Engagement:                             # class biasa, BUKAN @dataclass — lihat ADR-011 §enforcement
    id: str                                   # ENG-<uuid8>
    name: str
    created_by: str                           # "cli-user" | "bot:discord:<user_id>" | "system" — NEVER agent actor string
    created_at: str; updated_at: str
    authorization_source: AuthorizationSource | None
    approved_by: str | None                   # transisi READY->ACTIVE — HARUS human, guard eksplisit
    targets: list[TargetScope]
    excluded_assets: list[str]
    allowed_operations: list[str]             # nama Capability, ADDITIVE terhadap permission_profile
    forbidden_operations: list[str]           # SELALU menang, dicek Policy Layer 3 (Engagement)
    permission_profile: str                   # id PermissionProfile
    rate_limits: RateLimitPolicy
    concurrency: ConcurrencyPolicy
    time_window: TimeWindow
    credentials_ref: list[str]                # HANYA "vault:<name>"
    evidence_storage: str
    audit_ref: str
    notes: str = ""

    @property
    def status(self) -> EngagementStatus: ...  # read-only
    def transition(self, event: str, actor: str) -> TransitionResult: ...  # SATU-SATUNYA mutator

@dataclass
class TargetScope:
    pattern: str                              # syntax SAMA dengan ScopeManager._host_matches_pattern
    asset_type: str = "host"                  # host|url_prefix|cidr|api_base
    methods: list[str] = field(default_factory=lambda: ["GET", "HEAD"])
    notes: str = ""

@dataclass
class AuthorizationSource:
    kind: str          # bug_bounty_program|signed_saw|written_client_authorization|lab_disposable|ctf_platform_rules
    reference: str
    verified_by: str    # HARUS actor manusia
    verified_at: str

@dataclass
class RateLimitPolicy:
    default_rps: float = 2.0
    per_capability: dict[str, float] = field(default_factory=dict)

@dataclass
class ConcurrencyPolicy:
    max_parallel_executions: int = 1
    max_parallel_per_target: int = 1

@dataclass
class TimeWindow:
    not_before: str | None = None
    expires_at: str | None = None
```

**Relasi dengan `Scope`/`SessionState` existing**: migrasi 3 fase (lihat PRD-003). `SessionState` dapat field aditif `engagement_id: str`. `Scope` jadi VIEW turunan (derived), bukan source of truth, selama masa transisi.

## 4. `Capability` & `PermissionProfile` — Sisi "POLICY DECIDES"

```python
@dataclass
class Capability:
    name: str                    # "shell.readonly" | "shell.interpreter_passthrough" | "http.mutate" | "code.execute_python" | ...
    tools: frozenset[str]        # argv[0] basename, EXACT match — fix bug substring (ADR-009)
    methods: frozenset[str] = frozenset()
    scope_pattern: str = "inherit"
    rate_limit_rps: float | None = None
    risk: RiskLevel = RiskLevel.LOW              # LOW|MEDIUM|HIGH|PROHIBITED
    approval_mode: ApprovalMode = ApprovalMode.AUTO   # AUTO|NOTIFY|REQUIRE_HUMAN|ALWAYS_DENY
    allow_credential_injection: bool = False
    max_concurrency: int | None = None

@dataclass
class PermissionProfile:
    id: str                      # cocok PermissionMode.value existing
    label: str
    capabilities: tuple[Capability, ...]
    default_approval_mode: ApprovalMode
```

## 5. `Claim` — Batas "EVIDENCE PROVES"

```python
@dataclass
class Claim:
    claim_id: str                                   # "CLAIM-xxxxxxxx"
    text: str
    claim_type: str = "observation"                 # observation|hypothesis_test_result|vulnerability
    vuln_class: str = ""
    finding_id: str = ""
    task_id: str = ""
    evidence_refs: list[str] = field(default_factory=list)   # ["TX:xxxx","EV:xxxx"] — HANYA prefix ini valid
    confidence: float = 0.0
    verification_status: FindingState = FindingState.HYPOTHESIS   # REUSE enum existing
    severity_hint: str = "info"
    source: str = ""            # "llm:planner" | "rule:hypothesis_engine" | "executor:idor"
    captured_via: str = "llm_assertion"   # "broker_execution:<execution_id>" | "llm_assertion" — bobot beda di verifier
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
```

### 5.1 Sequencing — Revisi Pasca Second-Pass Critique

**Keputusan awal** (draft pertama): `Claim` dibuat POST-HOC oleh `EvidencePipeline` setelah `ExecutorResult` selesai. **DIKOREKSI**: kontrak broker (`ActionIntent.claim_ids` dicek SEBELUM eksekusi untuk auto-store evidence) mengasumsikan `Claim` sudah ada SEBELUM dispatch.

**Keputusan final**: `ClaimStore` (sepupu pattern `FindingsDB` — in-memory + flush ke `SessionState.claims` field baru) dapat entry point `create_claim_from_hypothesis()` dipanggil saat **PLANNING** (bukan saat executor selesai) — `claim_id` yang dihasilkan masuk ke `ActionIntent.claim_ids` sebelum broker jalan. Broker lalu **APPEND** `evidence_refs` ke claim yang SUDAH ada, tidak membuat claim baru.

`EvidencePipeline`/classifier turun perannya jadi **FALLBACK** (menangkap evidence yatim yang tidak terpasang ke claim manapun) — dan **TIDAK LAGI menulis `EvidenceVault` sendiri**, hanya membaca `execution_id`/`evidence_ref` yang sudah ditulis broker dan menempelkannya sebagai referensi. Prefix id `EVD-` (yang selama ini dipakai `Evidence`/`EvidencePipeline` sendiri) **pensiun** — hanya `TX-` (transcript) dan `EV-` (EvidenceVault) yang jadi referensi valid, sesuai apa yang sudah dikenali `GroundingFirewall.TX_REF`/`EV_REF` secara nyata.

`VerifierAgent.verify()` memberi bobot lebih ke `Claim` dengan `captured_via="broker_execution:*"` (dibuktikan via aksi nyata ter-broker) dibanding `"llm_assertion"` (klaim naratif LLM yang mengutip teks target) saat menilai kelayakan naik ke `VERIFIED`.

## 6. `ModelRequest`/`ModelResponse` — Sisi "EXECUTOR EXECUTES"

```python
@dataclass
class ModelRequest:
    role: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] = "auto"
    response_format: dict[str, Any] | None = None
    timeout: int | None = None
    max_tokens: int | None = None
    stream: bool = False
    on_token: Callable[[str], None] | None = None

@dataclass
class ModelResponse:
    status: str                                    # "success" | "error"
    provider: str; model: str; role: str | None = None
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)   # fondasi LoopBudget.max_cost_usd
    used_backup: bool = False
    error: dict[str, Any] | None = None
    raw: Any = None
```

## 7. `LoopBudget`

```python
@dataclass
class LoopBudget:
    max_steps: int = 50                    # ganti nama max_iters, default sama (tidak breaking)
    max_wall_seconds: float = 600.0
    max_cost_usd: float | None = None      # None = tidak dilacak, nunggu ModelResponse.usage terisi
    max_consecutive_failures: int = 3      # REUSE sinyal ActionMemory.should_block()
    no_progress_window: int = 3            # REUSE delta asset_graph.coverage_pct yang sudah dihitung tapi belum dibandingkan
    steps: int = 0
    started_at: float = field(default_factory=time.monotonic)
    consecutive_failures: int = 0
    progress_snapshots: list[float] = field(default_factory=list)

    def record_step(self, *, failed: bool, progress_metric: float | None = None) -> None: ...
    def exhausted(self) -> tuple[bool, str]: ...   # (stop?, reason)
```

Dicek setiap iterasi **sejajar** dengan kill-signal check (`self._stopped`/`EventBus.killswitch.*` — temuan critique: `self._stopped` di `SupervisorAgent` di-set tapi tidak pernah dibaca hari ini, WU-004 wajib menutup ini).

## 8. `ExecutorTask` (field baru, aditif)

```python
@dataclass
class ExecutorTask:
    # ...field existing tidak berubah...
    priority: int = 5                      # BARU — selaras Hypothesis/GraphNode.priority yang sudah ada
    depends_on: list[str] = field(default_factory=list)   # BARU, ADVISORY sort — BUKAN hard DAG (ADR-002)
    origin: str = "llm_json"               # BARU — llm_json|text_block|chain_planner|rule_engine
```

`stop_condition`/`success_criteria` (field lama, sebelumnya dead) di-WIRE ke `EvidenceVault.validate_schema()` — dicek tiap iterasi sebelum lanjut sisa `command_plan`.

## 9. `ExecutionResult` (dari broker)

```python
@dataclass
class ExecutionResult:
    execution_id: str
    status: str          # executed | blocked | approval_required | killed | error
    decision: PolicyDecision
    deny_reason: str | None = None   # BARU pasca critique — "engagement_not_active"|"policy_denied"|
                                       # "approval_required"|"rate_limited" — supaya loop bisa STOP SEGERA
                                       # (bukan habiskan max_iters retry ke deny yang sama)
    action_hash: str | None = None
    stdout: str = ""; stderr: str = ""; exit_code: int | None = None
    url: str | None = None; status_code: int | None = None
    evidence_ref: str | None = None
    audit_ref: str = ""
```

## 10. `audit_log` (SQLite, skema final pasca critique)

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,             -- AUD-<uuid8>
    ts TEXT NOT NULL,
    engagement_id TEXT, session_id TEXT,
    actor TEXT NOT NULL,             -- "supervisor"|"planner"|"executor"|"recon"|"operator:<id>"|"system"
    event_type TEXT NOT NULL,
    intent_id TEXT, action_hash TEXT, tool_name TEXT, capability TEXT, target TEXT, risk TEXT,
    tainted BOOLEAN NOT NULL DEFAULT 0,   -- BARU pasca critique: kolom eksplisit, bukan terkubur di JSON —
                                            -- supaya query "aksi tainted yang di-approve manual" vs "aksi bersih auto" 1 query
    decision TEXT,                   -- ALLOW|DENY|REQUIRE_APPROVAL|APPROVED|DENIED|CONSUMED|KILLED
    reason TEXT,
    execution_id TEXT, exit_code INTEGER, duration_ms INTEGER,
    evidence_ref TEXT, correlation_id TEXT,
    payload_json TEXT NOT NULL,       -- redacted SEBELUM insert
    integrity_hash TEXT NOT NULL      -- sha256(prev.integrity_hash + canonical(this_row)) — hash-chain append-only
);
CREATE INDEX idx_audit_engagement ON audit_log(engagement_id, ts);
CREATE INDEX idx_audit_correlation ON audit_log(correlation_id);
```

`EVENT_TYPES` (existing `events.py`) diperluas aditif: `intent.proposed`, `policy.decision`, `approval.requested/granted/denied/consumed/killed`, `execution.started/completed/failed/killed`, `evidence.stored`, `engagement.transitioned`, `killswitch.activated/resolved`, **`invalid_state_transition`** (BARU pasca critique — dipisah dari `execution.failed` generik, supaya transisi state ilegal dari `LoopStateMachine`/`Engagement` tidak tercampur error eksekusi biasa).

## 11. `EvidenceVault` Entry (field tambahan aditif)

```python
# entry existing + field baru:
{
  "id": "EV-xxxx", "finding_id": ..., "chain_step": ..., "graph_node_id": ...,
  "correlation_id": "COR-xxxx",   # BARU pasca critique — join Claim.evidence_refs -> EV:xxxx -> audit_log.tainted 1 query
  "timestamp": ..., "request": {...}, "response": {...}, "evidence_schema": {...}
}
```

## Diagram Relasi (ringkas)

```
Engagement (1) ──has-many──▶ TargetScope
Engagement (1) ──has-one───▶ AuthorizationSource
Engagement (1) ──scoped-by──▶ PermissionProfile ──has-many──▶ Capability

SessionState (1) ──references──▶ Engagement (aktif)
SessionState (1) ──has-many──▶ ExecutorTask (task_queue, flat + advisory depends_on)
SessionState (1) ──has-many──▶ Claim (claims, BARU)
SessionState (1) ──has-many──▶ Finding (findings, existing)

ActionIntent ──proposed-by──▶ actor (supervisor|planner|executor|recon)
ActionIntent ──carries──▶ claim_ids (harus sudah exist, dibuat saat PLANNING)
ActionIntent + BrokerContext ──in──▶ ExecutionBroker.execute() ──out──▶ ExecutionResult
ExecutionResult ──auto-writes──▶ EvidenceVault entry (match evidence_schema/claim_ids)
ExecutionResult ──appends-to──▶ Claim.evidence_refs (claim yang sudah ada, TIDAK membuat baru)
Claim ──promoted-to──▶ Finding (oleh VerifierAgent, saat confidence+evidence cukup)
Semua langkah di atas ──writes──▶ audit_log (sinkron untuk risk>=MEDIUM)
```
