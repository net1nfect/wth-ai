"""WTH-AI dual-agent orchestrator — production cybersecurity AI CLI."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

from wth_ai.agents.chat import ChatAgent
from wth_ai.agents.supervisor import SupervisorAgent
from wth_ai.agents.supervisor_context import SupervisorCallbacks, SupervisorContext
from wth_ai.agents.executor import ExecutorAgent
from wth_ai.agents.planner import PlannerAgent
from wth_ai.agents.report_builder import ReportBuilderAgent
from wth_ai.agents.verifier import VerifierAgent
from wth_ai.approval_gate import ApprovalGate
from wth_ai.browser.controller import BrowserController, browser_allowed
from wth_ai import __version__
from wth_ai.config import WTHConfig
from wth_ai.findings_db import FindingsDB
from wth_ai.brain.chain_planner import ChainPlanner
from wth_ai.brain.hypothesis_engine import HypothesisEngine
from wth_ai.evidence.vault import EvidenceVault
from wth_ai.graph.asset_graph import AssetGraph
from wth_ai.models import (
    AssessmentPhase,
    ExecutorTask,
    FindingState,
    PermissionMode,
    SessionState,
)
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.permission_manager import PermissionManager
from wth_ai.loop.engagement_loop import EngagementLoop
from wth_ai.intel.feed import IntelFeed
from wth_ai.memory.engagement_memory import EngagementMemory
from wth_ai.memory.learning import HuntMemoryLearning
from wth_ai.mcp.client import MCPClient
from wth_ai.probe.diff_brain import ResponseDiffBrain
from wth_ai.recon.agent_bridge import ReconAgentBridge
from wth_ai.tools.doctor import ToolDoctor
from wth_ai.tools.registry import ToolRegistry
from wth_ai.tools.runner import ManifestToolRunner
from wth_ai.scope_manager import ScopeManager
from wth_ai.session_store import SessionStore
from wth_ai.skills.registry import SkillRegistry
from wth_ai.skills.fusion import SkillFusionEngine
from wth_ai.cli.recon_wizard import ReconWizard
from wth_ai.terminal.bridge import AgentTerminalBridge
from wth_ai.tool_runner import ToolRunner


def _finding_learning_label(finding: Any) -> str:
    """Stable label for a verified finding in the learning memory.

    Prefers the CWE; otherwise the first token of the title; otherwise
    ``"unnamed"``. A finding title is allowed to be empty (the
    ``FINDING_CANDIDATE`` block parser accepts a bare ``- title:``), and the
    pre-fix inline ``finding.cwe or finding.title.split()[0].lower()`` raised
    ``IndexError: list index out of range`` for exactly that case.
    """
    if getattr(finding, "cwe", ""):
        return str(finding.cwe)
    title = str(getattr(finding, "title", "") or "")
    return (title.split() or ["unnamed"])[0].lower()


class WTHOrchestrator:
    """User → SupervisorAgent → tool loop (full agentic)."""

    def __init__(self, config: WTHConfig | None = None, *, fresh: bool = False) -> None:
        self.config = config or WTHConfig.load()
        self.client = LLMProviderRouter(self.config)
        self.scope_mgr = ScopeManager()
        self.permission_mgr = PermissionManager()
        self.approval_gate = ApprovalGate()
        self.findings_db = FindingsDB()
        self.session_store = SessionStore(self.config)
        from wth_ai.cli.slash_router import build_slash_router
        self.slash_router = build_slash_router(self)
        self._load_plugins_into_slash()
        self._ui_bus = None
        self.skills = SkillRegistry(
            openclaw_path=self.config.skills.openclaw_path,
            local_path=self.config.skills.local_path,
        )
        self.skills.ensure_symlink()
        self.skill_count = self.skills.discover()
        self.skill_router = SkillFusionEngine(self.skills, self.config)
        self.tool_runner = ToolRunner(self.config, self.permission_mgr, self.scope_mgr)
        self.state = SessionState()
        self._approved_tasks: set[str] = set()
        self.tool_registry = ToolRegistry()
        self.manifest_runner = ManifestToolRunner(self.config, self.permission_mgr, self.scope_mgr, self.tool_registry)
        self.doctor = ToolDoctor(self.config)
        self.memory = EngagementMemory(self.state.session_id)
        self.learning = HuntMemoryLearning()
        self.diff_brain = ResponseDiffBrain()
        self.intel = IntelFeed(self.config)
        self.mcp = MCPClient(self.config)
        self.planner = PlannerAgent(self.config, self.client, self.skill_router, self.tool_registry)
        self.verifier = VerifierAgent(self.config, self.client)
        self.reporter = ReportBuilderAgent(self.config, self.client)
        self.chat = ChatAgent(self.config, self.client, self.skills, context_builder=self._chat_context_builder)
        self.supervisor = SupervisorAgent(self.config, self.client)
        self.browser = BrowserController(headless=self.config.browser.headless)
        self.asset_graph = AssetGraph(self.state.session_id)
        self.hypothesis_engine = HypothesisEngine()
        self.chain_planner = ChainPlanner()
        self.evidence_vault = EvidenceVault(self.state.session_id)
        if not fresh:
            self._try_load_latest()
            self.memory = EngagementMemory(self.state.session_id)
            self.asset_graph = AssetGraph(self.state.session_id)
            self.evidence_vault = EvidenceVault(self.state.session_id)
        # WU-113 (ADR-004): asset_graph diset pasca-konstruksi -- EngagementMemory
        # dibangun SEBELUM AssetGraph di urutan init ini (dan bisa dibangun
        # ulang di blok `if not fresh`), sama seperti alasan wiring
        # evidence_vault WU-117 di bawah.
        self.memory.asset_graph = self.asset_graph
        # WU-117 (PRD-009): tool_runner butuh evidence_vault untuk wiring
        # stop_condition (early-exit command_plan begitu evidence_schema
        # sudah terpenuhi) -- diset sebagai atribut pasca-konstruksi, bukan
        # constructor param, karena EvidenceVault dibuat SETELAH ToolRunner
        # di urutan init ini (dan bisa dibuat ulang di blok `if not fresh`).
        self.tool_runner.evidence_vault = self.evidence_vault
        self._reinit_terminal()
        self.executor = ExecutorAgent(
            self.config, self.client, self.tool_runner, self.manifest_runner,
            terminal=self.terminal, evidence_vault=self.evidence_vault, asset_graph=self.asset_graph,
        )
        self.planner.set_terminal(self.terminal, self.asset_graph, self.evidence_vault)
        self._stream_callback: Callable[[str], None] | None = None
        self._tool_trace = None
        self._cron_ticker = None
        self._init_supervisor_ctx()
        import os
        if os.environ.get("WTH_TEST_MODE") != "1" and os.environ.get("WTH_DISABLE_CRON") != "1":
            self._start_local_cron()

    def _start_local_cron(self) -> None:
        try:
            from wth_ai.cron.ticker import LocalCronTicker
            self._cron_ticker = LocalCronTicker(self._supervisor_ctx.job_manager)
            self._cron_ticker.start()
        except Exception:
            self._cron_ticker = None

    def _load_plugins_into_slash(self) -> None:
        try:
            from wth_ai.plugins.manager import PluginManager

            pm = PluginManager(self.config)
            loaded = pm.load_all()
            for cmd, handler in pm.slash_handlers().items():
                if cmd not in self.slash_router.handlers:
                    self.slash_router.handlers[cmd] = handler
            if loaded:
                self.state.metadata["plugins_loaded"] = loaded
        except Exception:
            pass

    def _init_supervisor_ctx(self) -> None:
        from wth_ai.jobs.manager import JobManager
        from wth_ai.memory.action_memory import ActionMemory
        profile = self.config.agent.recon_profile
        bridge = ReconAgentBridge(
            self.state.session_id, profile=profile, graph=self.asset_graph,
        )
        ctx = SupervisorContext(
            config=self.config,
            client=self.client,
            state=self.state,
            scope_mgr=self.scope_mgr,
            permission_mgr=self.permission_mgr,
            asset_graph=self.asset_graph,
            evidence_vault=self.evidence_vault,
            findings_db=self.findings_db,
            terminal=self.terminal,
            action_memory=ActionMemory(self.state.session_id),
            planner=self.planner,
            executor=self.executor,
            verifier=self.verifier,
            reporter=self.reporter,
            skill_router=self.skill_router,
            job_manager=JobManager(self.config),
            recon_bridge=bridge,
            approved_tasks=self._approved_tasks,
            persist_fn=self._persist,
            orchestrator=self,
        )
        self.supervisor.bind(ctx)
        self._supervisor_ctx = ctx

    def _reinit_terminal(self) -> None:
        self.terminal = AgentTerminalBridge(
            self.config, self.permission_mgr, self.scope_mgr, self.state.session_id,
            registry=self.tool_registry,
            on_line=self._on_terminal_line if self._verbose_ui_enabled() else None,
        )

    def _verbose_ui_enabled(self) -> bool:
        from wth_ai.cli.ui.activity_stream import verbose_enabled
        return verbose_enabled(self.config)

    def _on_terminal_line(self, line: str) -> None:
        from wth_ai.cli.ui.activity_stream import verbose_enabled
        if not verbose_enabled(self.config):
            return
        ui = getattr(self.config, "ui", None)
        activity_mode = getattr(ui, "activity_mode", "live") if ui else "live"
        stream = getattr(self, "_activity_stream", None)
        trace = self._tool_trace
        bus = self._ui_bus
        if stream and activity_mode == "stream":
            stream.on_subprocess_line(line)
        if trace and activity_mode == "live":
            trace.add_log_line(f"$ {line}")
        if bus:
            bus.emit("subprocess_line", line=line)

    def _patch_terminal_verbose(self) -> None:
        """Rebind terminal subprocess lines to verbose UI sinks."""
        if not hasattr(self, "terminal") or not hasattr(self.terminal, "runner"):
            return
        self.terminal.runner.on_line = self._on_terminal_line if self._verbose_ui_enabled() else None

    def set_stream_callback(self, callback: Callable[[str], None] | None) -> None:
        self._stream_callback = callback

    def _try_load_latest(self) -> None:
        loaded = self.session_store.load_latest()
        if loaded:
            self.state = loaded
            self.permission_mgr.set_mode(self.state.permission_mode)
            self.scope_mgr.scope = self.state.scope
            self.findings_db.load_from_dict({
                "findings": self.state.findings,
                "hypotheses": self.state.hypotheses,
            })

    def new_session(self) -> str:
        self.state = SessionState()
        self.permission_mgr.set_mode(PermissionMode.READ_ONLY_RECON)
        self.scope_mgr = ScopeManager()
        self.findings_db = FindingsDB()
        self._approved_tasks.clear()
        self.asset_graph = AssetGraph(self.state.session_id)
        self.memory.asset_graph = self.asset_graph  # WU-113: re-wire after new_session() rebuilds asset_graph
        self.evidence_vault = EvidenceVault(self.state.session_id)
        self._rewire_session_services()
        self._reinit_terminal()
        self.executor = ExecutorAgent(
            self.config, self.client, self.tool_runner, self.manifest_runner,
            terminal=self.terminal, evidence_vault=self.evidence_vault, asset_graph=self.asset_graph,
        )
        self.planner.set_terminal(self.terminal, self.asset_graph, self.evidence_vault)
        self._init_supervisor_ctx()
        self._persist()
        return self.state.session_id

    def _chat_context_builder(self):
        """ContextBuilder for ChatAgent, resolved lazily on every call.

        `ChatAgent` is constructed BEFORE `asset_graph`/`learning` wiring in
        `__init__`, so passing a ready-made builder would hand chat a
        half-built graph. Building on demand also means a `new_session()`
        swap is picked up without rewiring the agent.
        """
        from wth_ai.context.builder import ContextBuilder

        return ContextBuilder(
            action_memory=getattr(self, "action_memory", None),
            asset_graph=getattr(self, "asset_graph", None),
            learning=getattr(self, "learning", None),
            skill_router=getattr(self, "skill_router", None),
            jobs=self._jobs_recall_block,
        )

    def _jobs_recall_block(self, target: str) -> str:
        """Recent job activity for `target`, for injection into agent context.

        Read from the job store, so a brand-new session still knows that recon
        already ran against this host in a previous one.
        """
        from wth_ai.jobs.recall import recall_block

        return recall_block(limit=5, target=target)

    def _learn_from_autopilot_step(self, step: Any) -> None:
        """Catat teknik dari langkah autopilot yang BERBUKTI (punya TX)."""
        if not getattr(step, "evidence_ref", ""):
            return
        try:
            from wth_ai.skills.autowrite import SkillNote, record_skill

            payload_class = ""
            payload_id = str(step.probe.get("payload_id") or "")
            if payload_id:
                from wth_ai.hunt.payloads import get_payload

                payload = get_payload(payload_id)
                payload_class = getattr(payload, "vuln_class", "") if payload else ""
            record_skill(SkillNote(
                technique=str(step.title)[:80],
                vuln_class=payload_class or "adaptive",
                tech_stack=str(self.state.metadata.get("tech_profile", "")),
                evidence=[f"[TX:{step.evidence_ref}]"],
                pitfalls=[str(step.note)] if step.note else [],
            ))
        except Exception:  # noqa: BLE001
            pass

    def _handle_autopilot(self, args: list[str]) -> str:
        """`/autopilot [langkah]` — jalankan langkah uji berbatas secara otonom."""
        from wth_ai.hunt.autopilot import format_report, run_autopilot

        target = self.state.scope.target
        if not target:
            return "Set target dulu: `/target <host>` (autopilot tidak menebak target)."
        steps = int(args[0]) if args and args[0].isdigit() else 6
        bridge = getattr(self, "terminal", None)
        if bridge is None:
            return "Bridge terminal tidak tersedia; jalankan lewat sesi interaktif."

        def _probe(probe: dict[str, Any]) -> Any:
            # Scope + denylist + rate limit + audit tetap berlaku; `approved=False`
            # supaya gate destruktif yang memutuskan, bukan autopilot.
            return bridge.dispatch_tool("http_probe", probe, actor="supervisor", approved=False)

        report = run_autopilot(
            target=target, run_probe=_probe, state=self.state,
            max_cycles=2, max_steps_per_cycle=steps,
            destructive_allowed=bool(getattr(self.state.scope, "destructive_allowed", False)),
            learn=self._learn_from_autopilot_step,
        )
        self._persist()
        return format_report(report)

    def _handle_payloads(self, args: list[str]) -> str:
        """`/payloads [kelas] [--destructive]` — katalog payload berbatas."""
        from wth_ai.hunt.payloads import filter_payloads, format_payloads, get_payload

        if args and args[0] == "gen":
            goal = " ".join(args[2:]) if len(args) > 2 else "temukan bug pada alur ini"
            vuln_class = args[1] if len(args) > 1 else "adaptive"
            from wth_ai.hunt.generator import format_generation, generate_with_llm, save_generated

            router = getattr(self, "client", None)
            if router is None:
                return "Router LLM tidak tersedia."
            result = generate_with_llm(router, vuln_class=vuln_class, goal=goal,
                                       tech_stack=str(self.state.metadata.get("tech_profile", "")))
            if result.accepted:
                path = save_generated(result.accepted)
                result.saved_path = str(path) if path else ""
            return format_generation(result)

        if args and args[0] not in {"list", "semua"}:
            payload = get_payload(args[0])
            if payload is None:
                return f"`{args[0]}` tidak ada di katalog. Pakai `/payloads` untuk melihat daftar."
            lines = [
                f"## `{payload.id}` — {payload.title}",
                f"kelas: {payload.vuln_class} · blast: {payload.blast_radius} · "
                f"reversibel: {'ya' if payload.reversible else 'tidak'}",
                f"membuktikan: {payload.proves}",
                f"probe: {payload.probe.get('method', 'GET')} {payload.probe.get('url', '')}",
                f"pemulihan: {payload.cleanup or '(tidak ada perubahan data)'}",
            ]
            if payload.bounded_alternative:
                lines.append(f"padanan berbatas: {payload.bounded_alternative}")
            return "\n".join(lines)

        destructive_allowed = bool(getattr(self.state.scope, "destructive_allowed", False))
        items = filter_payloads(
            None,
            max_blast="single-object" if not destructive_allowed else "single-object",
            include_destructive=destructive_allowed,
        )
        header = (
            f"mode destruktif: {'AKTIF (opt-in)' if destructive_allowed else 'TERTUTUP'}"
            " — `/scope set destructive=yes` untuk membukanya"
        )
        return header + "\n\n" + format_payloads(items)

    def _handle_next(self, args: list[str]) -> str:
        """`/next` — langkah uji berikutnya yang disusun dari temuan + sinyal respons."""
        from wth_ai.hunt.proactive import format_next_tests, propose_next_tests

        stored = self.state.metadata.get("next_tests") or []
        if stored and not args:
            class _Stored:
                def __init__(self, data: dict) -> None:
                    self.__dict__.update(data)
                    self.priority = float(data.get("priority", 1.0))
                    self.risk = str(data.get("risk", "low"))
                    self.title = str(data.get("title", ""))
                    self.rationale = str(data.get("rationale", ""))
                    self.probe = data.get("probe", {})

            return format_next_tests([_Stored(item) for item in stored])
        pairs = self.state.metadata.get("request_pairs", [])
        findings = []
        try:
            findings = [f.to_dict() if hasattr(f, "to_dict") else dict(f) for f in self.findings_db.list_findings()]
        except Exception:  # noqa: BLE001
            pass
        return format_next_tests(propose_next_tests(findings=findings, request_pairs=pairs))

    def _handle_eval_act(self, args: list[str]) -> str:
        """`/eval act` — jalankan aksi self-improvement atas hasil pengukuran."""
        from wth_ai.eval.act import act_on_report, format_actions

        learning = getattr(self, "learning", None)
        findings_db = getattr(self, "findings_db", None)
        actions = act_on_report(None, learning=learning, findings_db=findings_db)
        return format_actions(actions)

    def _handle_usage(self, args: list[str]) -> str:
        """`/usage` — token & biaya yang tercatat di mesin ini."""
        from wth_ai.llm.usage import get_ledger

        return get_ledger().format_summary()

    def _handle_eval(self, args: list[str]) -> str:
        """`/eval` — jalankan harness pengukuran self-improvement (offline).

        Reports honestly when there is nothing to measure: an empty learning
        store means "self-improvement" is an aspiration, not a demonstrated fact.
        """
        from wth_ai.eval import compare, format_report, load_latest, run

        report, path = run()
        previous = load_latest(exclude=path.name)
        return format_report(report, comparison=compare(report, previous)) + f"\n\nLaporan: {path}"

    def _handle_jobs(self, args: list[str]) -> str:
        """`/jobs [target]` — riwayat job dari job store (bukan asset graph sesi)."""
        from wth_ai.jobs.recall import agent_report, recent_jobs

        target = " ".join(args).strip()
        return agent_report(recent_jobs(limit=10, target=target))

    def _rewire_session_services(self) -> None:
        """Point every long-lived service at the CURRENT session's objects.

        `new_session()` replaces `scope_mgr`, `evidence_vault`, `asset_graph` and
        the session id, but `tool_runner` and `manifest_runner` receive those by
        constructor injection in `__init__` and were never refreshed. Verified on
        the pre-fix code: after `new_session()`,
        `tool_runner.scope is orch.scope_mgr` was False -- a scope check answered
        by the PREVIOUS session's ScopeManager (letting through a target the new
        session rejects), and evidence written into the dead session's vault
        while `stop_condition` early-exit consulted that same stale vault.
        `EngagementMemory` had the same problem, so it is rebuilt here too.
        """
        for runner in (getattr(self, "tool_runner", None), getattr(self, "manifest_runner", None)):
            if runner is None:
                continue
            if hasattr(runner, "scope"):
                runner.scope = self.scope_mgr
            if hasattr(runner, "permission"):
                runner.permission = self.permission_mgr
            if hasattr(runner, "evidence_vault"):
                runner.evidence_vault = self.evidence_vault
        self.memory = EngagementMemory(self.state.session_id)
        self.memory.asset_graph = self.asset_graph

    def startup_message(self) -> str:
        from wth_ai.cli.theme import THEME
        ts = self.tool_registry.summary()
        target = self.state.scope.target or "unset"
        perm = self.state.permission_mode.value
        llm = "online" if self.client.available() else "required"
        return (
            f"[{THEME['text']}]wth v{__version__}[/] [{THEME['muted']}]realtime · TTST[/] [{THEME['muted']}]authorized only[/]\n"
            f"[{THEME['muted']}]target[/] {target} · [{THEME['muted']}]perm[/] {perm} · "
            f"[{THEME['muted']}]tools[/] {ts['installed']}/{ts['total']} · [{THEME['muted']}]llm[/] {llm}\n"
            f"[{THEME['muted']}]chat to hunt · e.g. scope example.com then recon/hunt[/]"
        )

    def _ui_callbacks(self) -> SupervisorCallbacks:
        trace = self._tool_trace
        bus = self._ui_bus
        stream = getattr(self, "_activity_stream", None)
        from wth_ai.cli.ui.activity_stream import verbose_enabled
        verbose = verbose_enabled(self.config)
        ui = getattr(self.config, "ui", None)
        activity_mode = getattr(ui, "activity_mode", "live") if ui else "live"
        use_stream = verbose and activity_mode == "stream"
        use_trace = verbose and activity_mode == "live"

        def on_start(name: str, args: dict) -> None:
            if trace and use_trace:
                trace.add_pending(name, args)
            if stream and use_stream:
                stream.on_tool_start(name, args)
            if bus:
                bus.emit("tool_start", name=name, args=args)

        def on_end(name: str, result: str, ms: int) -> None:
            if trace and use_trace:
                trace.complete(name, result, ms)
            if stream and use_stream:
                stream.on_tool_end(name, result, ms)
            if bus:
                bus.emit("tool_end", name=name, result=result, ms=ms)

        def on_phase(phase: str, pct: int) -> None:
            if trace and use_trace:
                trace.set_phase(phase, pct)
            if stream and use_stream:
                stream.on_phase(phase, pct)
            if self._supervisor_ctx:
                self._supervisor_ctx.current_phase = phase
            if bus:
                bus.emit("phase", phase=phase, pct=pct)

        def on_token(t: str) -> None:
            if stream and use_stream and getattr(getattr(self.config, "ui", None), "stream_tokens", True):
                stream.on_token(t)
            if self._stream_callback:
                self._stream_callback(t)
            if bus:
                bus.emit("token", text=t)

        def on_llm_start(role: str, provider: str, model: str, meta: dict) -> None:
            if trace and use_trace:
                host = meta.get("host", "")
                trace.set_llm(f"llm {role} · {model} · {provider}" + (f" · {host}" if host else ""))
            if stream and use_stream:
                stream.on_llm_start(role, provider, model, meta)
            if bus and verbose:
                bus.emit("llm_start", role=role, provider=provider, model=model, meta=meta)

        def on_llm_tick(role: str, elapsed_s: int) -> None:
            # Coarse interval: repainting the live panel every second pins the
            # terminal viewport to the bottom, so a long recon could not be scrolled
            # while it ran. `ui.live_refresh_seconds` (default 15) is the knob.
            interval = getattr(getattr(self.config, "ui", None), "live_refresh_seconds", 15)
            from wth_ai.cli.ui.tool_trace import should_update_elapsed

            if trace and use_trace and should_update_elapsed(elapsed_s, interval):
                trace.set_llm(f"llm {role} · waiting {elapsed_s}s…")
            if stream and use_stream and should_update_elapsed(elapsed_s, interval):
                stream.on_llm_tick(role, elapsed_s)
            if bus and verbose:
                bus.emit("llm_tick", role=role, elapsed_s=elapsed_s)

        def on_llm_end(role: str, model: str, ms: int, meta: dict) -> None:
            if trace and use_trace:
                trace.clear_llm()
            if stream and use_stream:
                stream.on_llm_end(role, model, ms, meta)
            if bus and verbose:
                bus.emit("llm_end", role=role, model=model, ms=ms, meta=meta)

        return SupervisorCallbacks(
            on_tool_start=on_start,
            on_tool_end=on_end,
            on_phase_change=on_phase,
            on_token=on_token,
            on_llm_start=on_llm_start,
            on_llm_tick=on_llm_tick,
            on_llm_end=on_llm_end,
        )

    def resume(self, session_id: str | None = None) -> str:
        loaded = self.session_store.load(session_id) if session_id else self.session_store.load_latest()
        if not loaded:
            return "Tidak ada session. Mulai baru atau ketik apa saja untuk chat."
        self.state = loaded
        self.permission_mgr.set_mode(self.state.permission_mode)
        self.scope_mgr.scope = self.state.scope
        self.findings_db.load_from_dict({"findings": self.state.findings, "hypotheses": self.state.hypotheses})
        self.asset_graph = AssetGraph(self.state.session_id)
        self.memory.asset_graph = self.asset_graph  # WU-113: re-wire after resume() rebuilds asset_graph
        self.evidence_vault = EvidenceVault(self.state.session_id)
        self._reinit_terminal()
        self.executor = ExecutorAgent(
            self.config, self.client, self.tool_runner, self.manifest_runner,
            terminal=self.terminal, evidence_vault=self.evidence_vault, asset_graph=self.asset_graph,
        )
        self.planner.set_terminal(self.terminal, self.asset_graph, self.evidence_vault)
        self._init_supervisor_ctx()
        summary = self.session_store.resume_context_summary(self.state)
        self._persist()
        return f"✓ Session resumed: {self.state.session_id}\n\n{summary}"

    def handle_input(self, user_input: str) -> str:
        text = user_input.strip()
        if not text:
            return ""
        lower = text.lower()
        # WU-118 (PRD-002 FR#1): fast-path RETAINED ONLY for bare (no `/`)
        # quit/exit/stop -- SlashRouter.parse() structurally requires a `/`
        # prefix, so these bare forms need a translation step here. Every
        # OTHER command (help, new-session, resume, doctor, status, sessions)
        # is now a regular SlashRouter.handlers entry (see
        # cli/slash_router.py::build_slash_router()) -- previously a SEPARATE
        # literal if/elif block here, unsynced with the /-prefixed form of
        # the SAME command already registered in SlashRouter.handlers.
        if lower in {"quit", "exit"}:
            self.request_quit()
        if lower == "stop":
            return self.supervisor.stop()

        if text.startswith("/"):
            routed = self.slash_router.route(text)
            if routed is not None:
                self._sync_state_from_db()
                self._persist()
                self._index_fts(text, routed)
                return routed

        from wth_ai.cli.handlers.setup_handler import apply_target_from_text
        target_hint = apply_target_from_text(self, text)
        if target_hint is not None:
            self._sync_state_from_db()
            return target_hint

        # Hybrid routing: chat vs security-assess based on intent.
        agent_mode = getattr(getattr(self.config, "agent", None), "mode", "hybrid")
        # WU-119: session-aware fallback -- follow-up tanpa keyword eksplisit
        # tapi di tengah engagement aktif (target+phase != INTAKE) -> "assess".
        intent = self.skill_router.detect_intent(text, session_state=self.state)
        if agent_mode in {"hybrid", "chat"} and intent == "chat":
            resp = self._chat_turn(text)
            self._index_fts(text, resp)
            return resp
        if agent_mode == "chat":
            resp = self._chat_turn(text)
            self._index_fts(text, resp)
            return resp
        resp = self._supervisor_turn(text)
        self._index_fts(text, resp)
        return resp

    def _bypass_label(self) -> str:
        """Penanda sesi bypass di meta balasan.

        Sesi yang memakai instruksi tambahan harus terlihat berbeda di
        transkrip; pembaca yang menyimpan output ini berhak tahu konteksnya.
        """
        profile = self._active_bypass()
        return f" · bypass: {profile}" if profile else ""

    def _active_bypass(self) -> str:
        return self._resolve_bypass(self.config)

    @staticmethod
    def _resolve_bypass(config: Any) -> str:
        """Nama profil bypass yang benar-benar berlaku untuk `config` ini.

        `config.agent.bypass` adalah satu-satunya sumber: `WTH_BYPASS` sudah
        dipindahkan ke situ saat config dimuat. Membaca env lagi di sini adalah
        bug yang pernah terjadi — label mengaku `bypass: X` sementara
        `load_bypass_block()` (yang hanya melihat config) tidak menambahkan blok
        apa pun, jadi transkrip berbohong tentang konteks sesi.

        Sengaja `staticmethod`: pemanggilnya bisa `WTHOrchestrator` maupun config
        saja, jadi `self` akan mengikat argumen pertama di dua jalur itu.
        """
        from wth_ai.agents.bypass import resolve_name

        profile = getattr(getattr(config, "agent", None), "bypass", "") or ""
        return resolve_name(profile) if profile else ""

    def _index_fts(self, user_text: str, response: str) -> None:
        try:
            from wth_ai.memory.fts import SessionFtsIndex
            SessionFtsIndex().index_turn(f"cli:{self.state.session_id}", user_text, response)
        except Exception:
            pass

    def _chat_turn(self, text: str) -> str:
        from wth_ai.llm.errors import LLMError, LLMUnavailableError
        try:
            # Urutan append penting: `ChatAgent.respond()` mengirim
            # `state.conversation` sebagai history DAN merakit "User: <text>"
            # sendiri. Menambahkan pesan user sebelum panggilan membuat setiap
            # turn terkirim dua kali; jadi history ditulis setelah balasan ada.
            result = self.chat.respond(text, self.state)
            reply = (result or {}).get("raw") or ""
            self.state.conversation.append({"role": "user", "content": text})
            self.state.conversation.append({"role": "assistant", "content": reply[:8000]})
            self._persist()
            return reply + f"\n\n*mode: chat · model: {(result or {}).get('model','')}{self._bypass_label()}*"
        except LLMUnavailableError as exc:
            return str(exc)
        except LLMError as exc:
            return f"Chat model error: {exc}"

    def _supervisor_turn(self, text: str) -> str:
        from wth_ai.llm.errors import LLMError, LLMUnavailableError
        try:
            result = self.supervisor.run(
                text, self.state,
                callbacks=self._ui_callbacks(),
                persist_fn=self._persist,
            )
            self._sync_state_from_db()
            self._persist()
            label = self._bypass_label()
            return result + label if label else result
        except LLMUnavailableError as exc:
            return str(exc)
        except LLMError as exc:
            return (
                "LLM tidak tersedia untuk chat agentic.\n"
                "Jalankan `wth setup` untuk API key dan model, atau set provider Ollama di config.\n\n"
                f"Detail: {exc}"
            )
        except Exception as exc:
            msg = str(exc)
            if "timed out" in msg.lower() or "timeout" in msg.lower():
                rc = self.client.role_config("planner")
                model, _ = self.client.model_for_role("planner")
                provider = self.client.provider_for_role("planner")
                host = getattr(provider, "base_url", rc.type)
                return (
                    f"LLM timeout — tidak ada respons dalam batas waktu.\n"
                    f"- Provider: {rc.type} · model: {model}\n"
                    f"- Endpoint: {host}\n\n"
                    "Coba:\n"
                    "  `/model list` — cek model aktif\n"
                    "  `/provider` — ganti provider atau model lebih ringan\n"
                    "  pastikan 9router/Ollama masih jalan di mesin lokal\n\n"
                    f"Detail: {msg}"
                )
            raise

    def _chat_loop(self, user_input: str) -> str:
        text = user_input.lower()
        if any(k in text for k in ("skill", "testing apa", "bisa apa", "kemampuan", "fitur", "bisa testing")):
            if not self.client.available():
                return self.chat.capabilities_summary(self.skills)

        self.state.conversation.append({"role": "user", "content": user_input})
        if self.client.available():
            result = self.chat.respond(user_input, self.state)
            reply = result["raw"]
            meta = f"\n\n(model: {result['model']} | skills: {', '.join(result.get('skills_used', []))})"
        else:
            reply = self.chat.capabilities_summary(self.skills)
            meta = ""

        self.state.conversation.append({"role": "assistant", "content": reply[:8000]})
        self._persist()
        return reply + meta

    def _require_llm(self) -> str | None:
        """Return error message if LLM required but unavailable."""
        if self.config.require_llm and self.config.realtime_only and not self.client.available():
            return (
                "LLM tidak tersedia — eksekusi real-time membutuhkan API key.\n"
                "Jalankan: `wth-ai setup`"
            )
        return None

    def _assess_loop(self, user_input: str) -> str:
        err = self._require_llm()
        if err:
            return err
        if user_input.strip().lower().startswith("/plan") and not self.state.attack_surface:
            if not self.state.metadata.get("last_recon"):
                return (
                    "Attack surface kosong. Jalankan `/recon` dulu untuk memetakan target, "
                    "atau set scope: `/scope set target=domain.com authorized=yes`"
                )

        resume_ctx = ""
        if len(self.state.conversation) > self.config.session.max_conversation_turns:
            resume_ctx = self.session_store.resume_context_summary(self.state)
            self.state.conversation = self.state.conversation[-10:]

        self.state.current_phase = self.planner.infer_phase(user_input)
        self.state.conversation.append({"role": "user", "content": user_input})

        active_skills = self.skill_router.route(user_input, limit=3)
        tech_profile = self.state.metadata.get("tech_profile", "generic_web")
        recon_meta = self.state.metadata.get("last_recon", {})
        if recon_meta.get("adaptive_profile"):
            tech_profile = recon_meta["adaptive_profile"]
        playbook = self.skill_router.fuse(
            user_input,
            tech_profile=tech_profile,
            tech_stack=(self.state.metadata.get("attack_surface_obj") or {}).get("tech_stack", {}),
        )
        skill_ctx = playbook.to_context()
        learning_ctx = self.learning.build_planner_context(tech_profile)
        graph_ctx = self.asset_graph.export_for_planner() if self.asset_graph.nodes else ""
        tech_stack = (self.state.metadata.get("attack_surface_obj") or {}).get("tech_stack", {})
        pre_hypotheses = self.hypothesis_engine.generate(self.asset_graph, tech_stack)
        chains = self.chain_planner.plan_from_hypotheses(pre_hypotheses, self.state.scope.target or "")
        chain_dicts = [c.to_dict() for c in chains]
        self.state.metadata["pre_hypotheses"] = pre_hypotheses
        self.state.metadata["exploit_chains"] = chain_dicts

        plan_result = self.planner.plan(
            user_input, self.state,
            resume_context=resume_ctx,
            skill_context=skill_ctx,
            learning_context=learning_ctx,
            graph_context=graph_ctx,
            pre_hypotheses=pre_hypotheses,
            chain_suggestions=chain_dicts,
            on_token=self._stream_callback,
        )
        # Auto intel before plan
        if self.config.intel.enabled and self.state.metadata.get("attack_surface_obj"):
            self.intel.gather(None, self.state.scope.target)
        self.state.planner_summary = plan_result.get("planner_summary", "")
        self.state.conversation.append({"role": "assistant", "content": plan_result["raw"][:8000]})

        for hyp in plan_result.get("hypotheses", []):
            self.findings_db.add_hypothesis(hyp)
        for cand in plan_result.get("candidates", []):
            self.findings_db.add_finding(cand)

        tasks = plan_result.get("tasks", [])
        if not tasks and chains:
            tasks = self.chain_planner.chains_to_tasks(chains)
        if tasks:
            self.state.task_queue = [self._task_to_dict(t) for t in tasks]
            schemas = {t.id: t.evidence_schema for t in tasks if t.evidence_schema}
            if schemas:
                self.state.metadata["task_evidence_schemas"] = schemas

        self._sync_state_from_db()
        self._persist()

        parts = [plan_result["raw"]]
        if playbook.skill_ids:
            parts.append(f"\n*[Playbook: {', '.join(playbook.skill_ids[:5])} | confidence: {plan_result.get('confidence', '?')}]*")
        if plan_result.get("killed"):
            parts.append(f"\n*[Critic killed {len(plan_result['killed'])} weak hypotheses]*")
        if pre_hypotheses:
            parts.append(f"\n*[Graph hypotheses: {len(pre_hypotheses)} | chains: {len(chain_dicts)}]*")
        if tasks:
            parts.append(f"\n---\n**{len(tasks)} task queued** → `/execute`")
        return "\n".join(parts)

    def _run_recon(self, args: list[str]) -> str:
        """Legacy hook for EngagementLoop — delegates to supervisor."""
        target = self.state.scope.target or (args[0] if args else "")
        if not target:
            return "Set target first: scope example.com authorized"
        return self.supervisor.run(
            f"Run recon on {target}: discover subdomains, DNS, HTTP, headers, TLS, then finalize.",
            self.state,
            callbacks=self._ui_callbacks(),
            persist_fn=self._persist,
        )

    def _run_intel(self, args: list[str]) -> str:
        surface_obj = self.state.metadata.get("attack_surface_obj")
        surface = None
        if surface_obj:
            from wth_ai.models import AttackSurface, LiveHost, ParamCandidate
            surface = AttackSurface(
                target=surface_obj.get("target", ""),
                urls=surface_obj.get("urls", []),
                tech_stack=surface_obj.get("tech_stack", {}),
            )
        domain = self.state.scope.target or (args[0] if args else "")
        intel = self.intel.gather(surface, domain)
        return self.intel.format_report(intel)

    def _install_progress(self, msg: str) -> None:
        stream = getattr(self, "_activity_stream", None)
        trace = self._tool_trace
        bus = self._ui_bus
        ui = getattr(self.config, "ui", None)
        activity_mode = getattr(ui, "activity_mode", "live") if ui else "live"
        if stream and activity_mode == "stream":
            stream.on_install(msg)
        if trace and activity_mode == "live":
            trace.add_log_line(msg)
        if bus:
            bus.emit("activity_log", msg=msg)

    def _handle_tools(self, args: list[str]) -> str:
        if not args:
            from wth_ai.tools.platform import platform_summary
            return self.tool_registry.format_report() + f"\n\nPlatform: {platform_summary()}"
        if args[0] == "install":
            use_sudo = "--no-sudo" not in args
            parts = [a for a in args[1:] if not a.startswith("--")]
            category = parts[0] if parts else "all"
            progress = self._install_progress if getattr(self.config.ui, "verbose", True) else None
            return self.doctor.auto_install(category, use_sudo=use_sudo, on_progress=progress)
        return self.tool_registry.format_report(args[0])

    def _run_hunt(self, args: list[str]) -> str:
        err = self._require_llm()
        if err:
            return err
        goal = " ".join(args) if args else f"Audit {self.state.scope.target} — defensive vuln hunt"
        loop = EngagementLoop(self)
        try:
            return loop.run(goal)
        except Exception as exc:
            from wth_ai.llm.errors import LLMError, LLMUnavailableError
            import requests
            if isinstance(exc, (LLMError, LLMUnavailableError)):
                rc = self.client._role_config("planner")
                return (
                    f"LLM error during hunt ({rc.type}): {exc}\n"
                    f"Cek provider/model planner: `wth config show planner` atau `wth doctor`"
                )
            if isinstance(exc, requests.HTTPError):
                return (
                    f"LLM HTTP error during hunt: {exc}\n"
                    f"Cek model 9router (`wth model list`) — reporter/planner harus model yang ada di gateway."
                )
            raise

    def _browser_probe(self, args: list[str]) -> str:
        if not browser_allowed(self.permission_mgr.mode):
            return "Browser testing butuh `/permission standard` atau lebih tinggi."
        url = args[0] if args else (f"https://{self.state.scope.target}" if self.state.scope.target else "")
        if not url:
            return "Usage: `/browser https://target.com`"
        ok, msg = self.scope_mgr.is_in_scope(url)
        if not ok:
            return f"Blocked: {msg}"

        async def _run():
            try:
                session = await self.browser.navigate(url, bypass_cloudflare=True)
                await self.browser.stop()
                return session
            except Exception as exc:
                await self.browser.stop()
                raise exc

        try:
            session = asyncio.run(_run())
        except Exception as exc:
            return f"Browser error: {exc}\nPastikan: pip install nodriver"

        lines = [
            f"## Browser Probe — {url}",
            f"- Title: {session.title}",
            f"- Cloudflare detected: {session.cloudflare_detected}",
            f"- Cloudflare bypassed: {session.cloudflare_bypassed}",
            f"- Forms found: {len(session.forms)}",
            f"- Links: {len(session.links)}",
        ]
        if session.forms:
            lines.append("\n### Forms")
            for f in session.forms[:3]:
                lines.append(f"- {f.get('method', '?').upper()} {f.get('action', '')} ({len(f.get('inputs', []))} inputs)")
        if session.links:
            lines.append("\n### Sample links")
            for link in session.links[:8]:
                lines.append(f"- {link}")
        if session.errors:
            lines.append(f"\nErrors: {session.errors}")
        self.state.attack_surface.extend(session.links[:10])
        self._persist()
        return "\n".join(lines)

    def _show_skill(self, skill_id: str) -> str:
        if not skill_id:
            return "Usage: `/skill xss-cross-site-scripting`"
        skill = self.skills.get(skill_id)
        if not skill:
            matches = self.skills.search(skill_id, limit=5)
            if matches:
                return "Skill tidak ditemukan. Maksud kamu?\n" + "\n".join(f"- /skill {s.id}" for s in matches)
            return f"Skill '{skill_id}' tidak ada. Cek `/skills`"
        return f"# Skill: {skill.name}\n\n{skill.load_content(15000)}"

    def _list_sessions(self) -> str:
        sessions = self.session_store.list_sessions()
        if not sessions:
            return "Belum ada session tersimpan."
        lines = ["SAVED SESSIONS:"]
        for s in sessions[:10]:
            lines.append(f"- {s['session_id'][:8]}... | {s.get('target', '-')} | {s.get('phase')} | {s.get('updated_at', '')[:19]}")
        lines.append("\nResume: `/resume` atau `/resume <session_id>`")
        return "\n".join(lines)

    def _handle_scope(self, args: list[str]) -> str:
        if not args or args[0] != "set":
            return self.scope_mgr.display() + f"\n\n{self.permission_mgr.display()}"

        updates: dict[str, Any] = {}
        for arg in args[1:]:
            if "=" in arg:
                key, val = arg.split("=", 1)
                key, val = key.strip(), val.strip()
                if key == "in_scope":
                    updates["in_scope"] = [v.strip() for v in val.split(",")]
                elif key == "out_of_scope":
                    updates["out_of_scope"] = [v.strip() for v in val.split(",")]
                elif key == "authorized":
                    updates["authorized"] = val.lower() in {"yes", "true", "1"}
                elif key == "target":
                    from wth_ai.scope_intent import parse_scope_intent
                    intent = parse_scope_intent(f"set target {val}") or {"target": val, "authorized": True, "in_scope": [val]}
                    updates.update({k: v for k, v in intent.items() if v is not None})
                elif key in ("accounts", "test_accounts"):
                    updates["test_accounts"] = [v.strip() for v in val.split(",") if v.strip()]
                elif key in ("destructive", "destructive_allowed"):
                    # Opt-in eksplisit per engagement. Nilai teks dinormalkan ke bool,
                    # kalau tidak `/scope set destructive=yes` tersimpan sebagai string
                    # dan gate-nya membaca string truthy -- benar, tapi rapuh.
                    enabled = val.lower() in {"yes", "true", "1", "on"}
                    updates["destructive_allowed"] = enabled
                    if enabled:
                        notes = updates.get("notes") or self.state.scope.notes or ""
                        if "destructive=on" not in notes:
                            updates["notes"] = (notes + " destructive=on").strip()
                else:
                    updates[key] = val
        self.scope_mgr.update_from_dict(updates)
        self.state.scope = self.scope_mgr.scope
        self._persist()
        return self.scope_mgr.display()

    def _propose_next_after_finding(self, finding: Any) -> str:
        """Proaktif: setelah ada temuan, susun langkah uji berikutnya.

        Hasilnya disimpan di state.metadata supaya bisa dibaca `/next`, dipakai agent,
        dan diprioritaskan oleh bobot hasil pengukuran (eval/act.py).
        """
        from wth_ai.hunt.proactive import format_next_tests, propose_next_tests

        pairs = self.state.metadata.get("request_pairs", []) if hasattr(self, "state") else []
        findings = []
        try:
            findings = [
                f.to_dict() if hasattr(f, "to_dict") else dict(f)
                for f in self.findings_db.list_findings()
            ]
        except Exception:  # noqa: BLE001
            findings = []
        if not findings:
            findings = [finding if isinstance(finding, dict) else getattr(finding, "__dict__", {})]
        proposals = propose_next_tests(findings=findings, request_pairs=pairs)
        self.state.metadata["next_tests"] = [p.to_dict() for p in proposals]
        return format_next_tests(proposals)

    def _write_skill_from_finding(self, finding: Any) -> str:
        """Tulis/perbarui skill otomatis dari temuan yang BERBUKTI. Tanpa bukti: tidak ada file."""
        from wth_ai.skills.autowrite import note_from_finding, record_skill

        note = note_from_finding(finding)
        path = record_skill(note)
        return str(path) if path else ""

    def _record_learning_outcome(self, finding: Any, *, verified: bool) -> None:
        """Record BOTH outcomes into the learning store.

        Found by the eval harness: `record_from_finding` was called from exactly
        one place in the codebase -- the `verified` branch. False positives went
        only to `FindingsDB`, so the learning store could never contain a
        failure and `success_rate()` was structurally incapable of reflecting
        one: it was a tally of wins dressed up as a precision. Every downstream
        consumer (`top_patterns`, `build_planner_context`, `engagement_nudge`)
        inherited that bias.

        `finding.cwe` is often empty and `title` can legitimately be empty (the
        FINDING_CANDIDATE parser accepts `- title:` with no value), so the label
        is derived defensively -- a bare `finding.title.split()[0]` used to
        raise IndexError here and abort the whole `/verify` run.
        """
        tech = self.state.metadata.get("tech_profile", "generic_web")
        self.learning.record_from_finding(
            tech,
            _finding_learning_label(finding),
            verified=verified,
            session_id=self.state.session_id,
        )
        if not verified:
            # Temuan gugur tidak menghasilkan langkah lanjutan (tidak ada yang perlu
            # dikejar) dan TIDAK menulis skill -- pelajaran dari false positive masuk
            # lewat store learning, tempat backfill eval/act.py juga menulis.
            return
        # Proaktif: begitu ada temuan terverifikasi, susun langkah uji berikutnya dan
        # simpan skill yang lahir dari buktinya. Keduanya dibungkus supaya kegagalan di
        # sini tidak pernah menggagalkan pelaporan temuan itu sendiri.
        try:
            self._propose_next_after_finding(finding)
        except Exception:  # noqa: BLE001
            pass
        try:
            path = self._write_skill_from_finding(finding)
            if path:
                self.state.metadata.setdefault("auto_skills", []).append(path)
        except Exception:  # noqa: BLE001
            pass

    def _handle_permission(self, args: list[str]) -> str:
        """`/permission [mode]` — one word sets the whole safety policy.

        Accepts the presets (`plan`, `read-only`, `ask`, `auto`, `yolo`) and the
        raw `PermissionMode` values via aliases, so muscle memory from the old
        spelling keeps working.
        """
        from wth_ai.permission_presets import apply_preset, describe_all

        if not args:
            return f"{self.permission_mgr.display()}\n\n{describe_all()}"
        try:
            preset = apply_preset(args[0].lower(), permission_mgr=self.permission_mgr)
        except ValueError as exc:
            return f"Error: {exc}"
        self.state.permission_mode = self.permission_mgr.mode
        self._persist()
        return f"✓ Preset aktif: {preset.name} — {preset.summary}"

    def _handle_research(self, args: list[str]) -> str:
        """`/research <query>` · `/research url <URL>` · `/research notes`.

        Research never needs approval (it reads public pages and touches no target
        asset) but every result is rendered with its epistemic status attached,
        so a search hit can never be mistaken for a confirmed finding.
        """
        from wth_ai.research import get_researcher

        usage = (
            "Pakai: /research <kata kunci>  |  /research url <URL>  |  /research browser <URL>  |  /research notes\n"
            "Status hasil: KANDIDAT — informasi publik bukan bukti tentang target ini."
        )
        if not args:
            return usage
        researcher = get_researcher(self.state.session_id)
        head = args[0].lower()
        if head in ("url", "fetch") and len(args) > 1:
            return researcher.report(researcher.fetch(args[1]))
        if head == "browser" and len(args) > 1:
            return researcher.report(researcher.browser_fetch(args[1]))
        if head == "notes":
            return researcher.context_block() or "Belum ada catatan riset di sesi ini."
        return researcher.report(researcher.search(" ".join(args)))

    def _handle_computer(self, args: list[str]) -> str:
        """`/computer <action> [arg...]` — drive the real desktop.

        Capability gating matches the agent path, but the APPROVAL dimension is
        deliberately not re-asked here: an operator typing the command into the
        REPL has already expressed intent, and prompting again is the kind of
        friction that gets a guardrail switched off entirely. The agent path
        (`computer_use` tool) still requires approval in `ask` mode.
        """
        from wth_ai.computer_use import INPUT_ACTIONS, ComputerUse
        from wth_ai.models import PermissionMode

        usage = (
            "Pakai: /computer <aksi> [arg]\n"
            "  observasi (read-only): detect · screenshot [path] · windows · screen_size · cursor\n"
            "  input (butuh minimal preset 'ask'): click X Y · move X Y · type <teks> · key <combo> · focus <window>"
        )
        if not args or args[0] in ("help", "-h", "--help"):
            return usage

        action = args[0].lower()
        rest = args[1:]

        if action in INPUT_ACTIONS and not self.permission_mgr.mode_sufficient(
            PermissionMode.STANDARD_TESTING
        ):
            return (
                f"DITOLAK: '{action}' menyuntik input ke desktop. Sekarang mode "
                f"{self.permission_mgr.mode.value}. Jalankan /permission ask (atau yolo) dulu."
            )

        kwargs: dict[str, Any] = {}
        try:
            if action in ("click", "move"):
                kwargs = {"x": int(rest[0]), "y": int(rest[1])}
            elif action == "type":
                kwargs = {"text": " ".join(rest)}
            elif action == "key":
                kwargs = {"combo": rest[0]}
            elif action == "focus":
                kwargs = {"window": " ".join(rest)}
            elif action == "screenshot" and rest:
                kwargs = {"path": rest[0]}
        except (IndexError, ValueError):
            return f"Argumen tidak lengkap untuk '{action}'.\n{usage}"

        result = ComputerUse().run_action(action, **kwargs)
        note = ""
        if action in INPUT_ACTIONS:
            note = "  [input desktop — dijalankan atas perintah langsung operator]"
        return result.describe() + note

    def _handle_approve(self, args: list[str]) -> str:
        if not args:
            return self.approval_gate.format_pending()
        aid = args[0]
        if aid.startswith("TASK-"):
            self._approved_tasks.add(aid)
            return f"✓ Task {aid} approved."
        if self.approval_gate.approve(aid):
            return f"✓ Approval {aid} granted."
        return f"Approval tidak ditemukan: {aid}"

    def _execute_next(self, task_id: str | None, *, run_all: bool = False) -> str:
        if not self.state.task_queue and not task_id:
            return "Task queue kosong. Coba `/plan` atau `/recon` dulu."

        if run_all:
            from wth_ai.executor.pool import ExecutorPool
            pool = ExecutorPool(
                max_workers=3,
                rate_limit_rps=self.scope_mgr.scope.rate_limit_rps,
            )
            tasks = [ExecutorTask.from_dict(t) for t in self.state.task_queue[:6]]

            def _run(task: ExecutorTask) -> ExecutorResult:
                approved = task.id in self._approved_tasks or self.approval_gate.is_approved_for_task(task.id)
                r = self.executor.execute(task, self.state, approved=approved)
                pairs = self.state.metadata.pop("request_pairs", [])
                if pairs:
                    self.evidence_vault.store_from_executor(task.id, pairs, task.evidence_schema)
                return r

            results = pool.run_all_sync(tasks, _run, approved_ids=self._approved_tasks)
            outputs = []
            done_ids = set()
            for item in results:
                result = item["result"]
                tid = item["task_id"]
                done_ids.add(tid)
                self.state.completed_tasks.append(result.to_dict())
                outputs.append(self.executor.format_result(result))
            self.state.task_queue = [t for t in self.state.task_queue if t.get("id") not in done_ids]
            self._persist()
            return f"## Executed {len(results)} tasks\n\n" + "\n\n---\n\n".join(outputs)

        task_dict = next((t for t in self.state.task_queue if t.get("id") == task_id), None) if task_id else self.state.task_queue[0]
        if not task_dict:
            return f"Task {task_id} tidak ada di queue."

        task = ExecutorTask.from_dict(task_dict)
        approved = task.id in self._approved_tasks or self.approval_gate.is_approved_for_task(task.id)
        result = self.executor.execute(task, self.state, approved=approved)
        pairs = self.state.metadata.pop("request_pairs", [])
        if pairs:
            self.evidence_vault.store_from_executor(task.id, pairs, task.evidence_schema)
        self.state.completed_tasks.append(result.to_dict())
        self.state.task_queue = [t for t in self.state.task_queue if t.get("id") != task.id]
        if result.status == "success" and result.output_summary:
            snippet = result.output_summary[:300]
            if snippet not in self.state.attack_surface:
                self.state.attack_surface.append(snippet)
        self.state.conversation.append({"role": "assistant", "content": f"EXECUTOR: {json.dumps(result.to_dict())[:4000]}"})
        self._persist()

        out = self.executor.format_result(result)
        if result.status == "needs_approval":
            aid = self.approval_gate.request(task.goal, result.output_summary, task.id)
            out += f"\n\nButuh approval: `/approve {task.id}`"
        return out

    def _verify(self, finding_id: str | None) -> str:
        err = self._require_llm()
        if err:
            return err
        findings = self.findings_db.list_findings("candidate")
        if finding_id:
            findings = [f for f in self.findings_db.list_findings() if f.id == finding_id]
        if not findings:
            return "Tidak ada candidate finding."

        outputs = []
        for finding in findings[:5]:
            import os
            if self.client.available():
                v = self.verifier.verify(finding, self.state)
            elif os.environ.get("WTH_TEST_MODE") == "1":
                v = self.verifier.verify_offline(finding)
            else:
                return "LLM tidak tersedia untuk verifikasi. Jalankan `wth-ai setup`."
            verdict = v.get("verdict", "needs_more_evidence")
            if verdict == "verified":
                self.findings_db.update_finding_state(finding.id, FindingState.VERIFIED)
                self._record_learning_outcome(finding, verified=True)
            elif verdict == "false_positive":
                self.findings_db.update_finding_state(finding.id, FindingState.FALSE_POSITIVE)
                self._record_learning_outcome(finding, verified=False)
            outputs.append(self.verifier.format_verification(v))

        self._sync_state_from_db()
        self._persist()
        return "\n\n".join(outputs)

    def _report(self) -> str:
        err = self._require_llm()
        if err:
            return err
        content = self.reporter.build(self.state, self.findings_db, evidence_vault=self.evidence_vault)
        path = self.reporter.save(content, self.state)
        self.state.current_phase = AssessmentPhase.REPORT
        self._persist()
        return f"Report: {path}\n\n{content[:5000]}{'...' if len(content) > 5000 else ''}"

    def _status_summary(self, prefix: str) -> str:
        return "\n".join([
            f"**{prefix}**",
            f"- Session: `{self.state.session_id[:12]}...`",
            f"- LLM: {'online' if self.client.available() else 'required — run wth-ai setup'}",
            f"- Skills: {self.skill_count}",
            f"- Phase: {self.state.current_phase.value}",
            f"- Permission: {self.state.permission_mode.value}",
            f"- Target: {self.state.scope.target or 'unset'}",
            f"- Tasks done: {len(self.state.completed_tasks)} | queued: {len(self.state.task_queue)}",
            f"- Findings: {len(self.findings_db.findings)} | Hypotheses: {len(self.findings_db.hypotheses)}",
        ])

    def _task_to_dict(self, task: ExecutorTask) -> dict[str, Any]:
        return {
            "id": task.id, "goal": task.goal, "target": task.target,
            "permission_required": task.permission_required.value,
            "allowed_tools": task.allowed_tools, "forbidden_actions": task.forbidden_actions,
            "command_plan": task.command_plan, "expected_evidence": task.expected_evidence,
            "stop_condition": task.stop_condition, "success_criteria": task.success_criteria,
            "approval_needed": task.approval_needed,
            "module": task.module,
            "evidence_schema": task.evidence_schema,
            "priority": task.priority, "depends_on": task.depends_on, "origin": task.origin,
        }

    def _sync_state_from_db(self) -> None:
        self.state.findings = [f.to_dict() for f in self.findings_db.findings.values()]
        self.state.hypotheses = [h.to_dict() for h in self.findings_db.hypotheses.values()]

    def _persist(self) -> None:
        if self.config.session.auto_save:
            self.session_store.save(self.state)

    def shutdown(self, *, reason: str = "quit") -> str:
        """Stop agents, background jobs, and persist session."""
        stopped_jobs = 0
        try:
            self.supervisor.stop()
        except Exception:
            pass

        jm = getattr(self._supervisor_ctx, "job_manager", None) if self._supervisor_ctx else None
        if jm is not None:
            for job in jm.list_jobs():
                if job.status in {"running", "queued", "pending"}:
                    jm.stop_job(job.job_id)
                    stopped_jobs += 1

        try:
            import asyncio
            asyncio.run(self.browser.stop())
        except Exception:
            pass

        self.state.current_phase = AssessmentPhase.STOPPED
        self._sync_state_from_db()
        self._persist()

        parts = ["✓ WTH-AI ditutup · session disimpan"]
        if stopped_jobs:
            parts.append(f"{stopped_jobs} job dihentikan")
        if reason and reason != "quit":
            parts.append(reason)
        return " · ".join(parts)

    def request_quit(self) -> None:
        """Shutdown and signal the REPL to exit."""
        from wth_ai.cli.quit import SessionQuit
        raise SessionQuit(self.shutdown())

    def run_interactive(self, *, local: bool = False) -> None:
        import os
        skip_gw = local or os.environ.get("WTH_LOCAL", "") == "1" or os.environ.get("WTH_SKIP_GATEWAY", "") == "1"
        if not skip_gw:
            try:
                from wth_ai.gateway.client import GatewayClient, gateway_available
                from wth_ai.gateway.remote_orch import RemoteOrchestrator
                if gateway_available():
                    RemoteOrchestrator(GatewayClient()).run_interactive()
                    return
            except Exception:
                pass

        from wth_ai.cli.terminal_shell import run_terminal_shell
        run_terminal_shell(self)

    def run_once(self, command: str) -> str:
        return self.handle_input(command)
