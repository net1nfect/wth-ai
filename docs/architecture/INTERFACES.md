# Pseudo-Interfaces — Target Architecture

> Signature tingkat desain untuk komponen inti. Bukan implementasi final — detail lengkap ada di PRD masing-masing.

```python
class ExecutionBroker:
    def execute(self, intent: ActionIntent, context: BrokerContext) -> ExecutionResult:
        """SATU-SATUNYA chokepoint eksekusi subprocess/HTTP/code-exec.
        Pipeline: schema validation -> actor/trust resolution -> Engagement fetch-fresh
        -> scope check -> PolicyEngine.evaluate_intent() -> ApprovalGate (action-hash)
        -> rate-limit/concurrency -> sandbox -> EXECUTION -> audit (sync utk risk>=MEDIUM)
        -> evidence (auto-store, append ke Claim yang sudah exist)."""
        ...


class PolicyEngine:
    def evaluate_intent(
        self, intent: ActionIntent, engagement: Engagement,
        actor_profile: ActorProfile, context: BrokerContext,
    ) -> PolicyDecision:
        """6 layer hierarkis, TIDAK ADA fallthrough implisit ke ALLOW:
        L0 kill-switch -> L1 global-safety-denylist -> L2 environment
        -> L3 engagement(status/scope/forbidden) -> L4 agent-capability
        -> L5 tool-policy(Capability resolve) -> L6 action-policy(risk+tainted+rate)."""
        ...


class ApprovalRegistry:
    def compute_action_hash(self, intent: ActionIntent, engagement: Engagement) -> str:
        """sha256 dari {engagement_id, capability, tool_name, normalized_target,
        canonicalized_argv_or_request, risk} — SELURUH argv/URL/body, bukan cuma nama tool.
        Command 'mirip tapi beda argumen' -> hash BEDA -> approval terpisah wajib."""
        ...

    def request_approval(self, intent: ActionIntent, action_hash: str) -> ApprovalRecord: ...
    def is_approved(self, action_hash: str) -> bool:
        """Single-use: status -> CONSUMED setelah 1 eksekusi sukses."""
        ...


class Engagement:
    @property
    def status(self) -> EngagementStatus: ...   # read-only

    def transition(self, event: str, actor: str) -> TransitionResult:
        """SATU-SATUNYA mutator. Guard per transisi (lihat DATA-MODEL.md #3).
        READY->ACTIVE mensyaratkan actor BUKAN string actor agent."""
        ...


class EngagementStore:
    def fetch(self, engagement_id: str) -> Engagement:
        """Broker WAJIB panggil ini di SETIAP execute() — fresh dari SQLite,
        BUKAN trust in-memory reference (ADR-011)."""
        ...


class LoopStateMachine:                         # rename dari EngagementStateMachine
    @property
    def state(self) -> LoopState: ...           # read-only
    def transition(self, new: LoopState, *, reason: str = "") -> LoopState:
        """Emit event + (WTH_STRICT_SM=1) raise InvalidTransitionError kalau ilegal.
        Dipakai AgentToolLoop DAN EngagementLoop/HuntLoop, bukan cuma satu."""
        ...


class LoopBudget:
    def record_step(self, *, failed: bool, progress_metric: float | None = None) -> None: ...
    def exhausted(self) -> tuple[bool, str]:
        """Dicek SEJAJAR dengan kill-signal tiap iterasi AgentToolLoop.run()."""
        ...


class ClaimStore:
    def create_claim_from_hypothesis(self, hypothesis: Hypothesis, task_id: str) -> Claim:
        """Dipanggil saat PLANNING — claim_id masuk ActionIntent.claim_ids
        SEBELUM broker execute() (lihat DATA-MODEL.md §5.1)."""
        ...
    def append_evidence(self, claim_id: str, evidence_ref: str) -> Claim:
        """Dipanggil broker setelah eksekusi sukses — APPEND, bukan create baru."""
        ...


class LLMProviderRouter:
    def generate(self, request: ModelRequest) -> ModelResponse:
        """Method inti baru. chat_for_role/chat_with_tools_for_role/complete()
        jadi adapter tipis di atas ini (strangler-fig, ADR-003)."""
        ...


class ContextBuilder:
    def build(
        self, state: SessionState, *, sources: list[str], char_budget: int,
    ) -> str:
        """SOURCE_REGISTRY: action_memory|md_context|engagement_graph|learning|
        skill_fusion|fts_recall|jobs. Dipakai KEDUA jalur (chat & /plan) dengan
        subset sources berbeda, bukan implementasi berbeda (ADR-004)."""
        ...


class KillSwitch:
    def activate(self, *, engagement_id: str | None = None) -> None:
        """3 permukaan: (a) RunningActionRegistry.kill() -> os.killpg() untuk
        subprocess blocking; (b) ThreadPoolExecutor tanpa `with`, shutdown(wait=False,
        cancel_futures=True) untuk job worker thread; (c) bulk-transition approval
        pending -> KILLED. Tidak pernah menyentuh EvidenceVault/audit_log (append-only)."""
        ...


class RunningActionRegistry:
    def register(self, execution_id: str, pid: int, pgid: int, engagement_id: str) -> None: ...
    def kill(self, *, engagement_id: str | None = None, execution_id: str | None = None,
              grace_seconds: int = 3) -> None:
        """SIGTERM ke process-group, tunggu grace_seconds, SIGKILL kalau masih hidup."""
        ...
```
