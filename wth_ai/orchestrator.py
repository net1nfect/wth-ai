"""WTH-AI dual-agent orchestrator — production cybersecurity AI CLI."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from wth_ai.agents.chat import ChatAgent
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
    ExecutorResult,
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
from wth_ai.recon.orchestrator import ReconOrchestrator
from wth_ai.tools.doctor import ToolDoctor
from wth_ai.tools.registry import ToolRegistry
from wth_ai.tools.runner import ManifestToolRunner
from wth_ai.scope_manager import ScopeManager
from wth_ai.session_store import SessionStore
from wth_ai.skills.registry import SkillRegistry
from wth_ai.skills.fusion import SkillFusionEngine
from wth_ai.slash_commands import SlashCommandHandler
from wth_ai.cli.recon_wizard import ReconWizard
from wth_ai.cli.theme import PROMPT_SYMBOL
from wth_ai.terminal.bridge import AgentTerminalBridge
from wth_ai.tool_runner import ToolRunner

class WTHOrchestrator:
    """User → Intent Router → Chat | Planner | Executor | Verifier | Report"""

    def __init__(self, config: WTHConfig | None = None, *, fresh: bool = False) -> None:
        self.config = config or WTHConfig.load()
        self.client = LLMProviderRouter(self.config)
        self.scope_mgr = ScopeManager()
        self.permission_mgr = PermissionManager()
        self.approval_gate = ApprovalGate()
        self.findings_db = FindingsDB()
        self.session_store = SessionStore(self.config)
        self.slash = SlashCommandHandler()
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
        self.chat = ChatAgent(self.config, self.client, self.skills)
        self.recon = ReconOrchestrator(self.config)
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
        self._reinit_terminal()
        self.executor = ExecutorAgent(
            self.config, self.client, self.tool_runner, self.manifest_runner,
            terminal=self.terminal, evidence_vault=self.evidence_vault, asset_graph=self.asset_graph,
        )
        self.planner.set_terminal(self.terminal, self.asset_graph, self.evidence_vault)
        self._stream_callback: Callable[[str], None] | None = None

    def _reinit_terminal(self) -> None:
        self.terminal = AgentTerminalBridge(
            self.config, self.permission_mgr, self.scope_mgr, self.state.session_id,
            registry=self.tool_registry,
        )

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
        self.evidence_vault = EvidenceVault(self.state.session_id)
        self._reinit_terminal()
        self.executor = ExecutorAgent(
            self.config, self.client, self.tool_runner, self.manifest_runner,
            terminal=self.terminal, evidence_vault=self.evidence_vault, asset_graph=self.asset_graph,
        )
        self.planner.set_terminal(self.terminal, self.asset_graph, self.evidence_vault)
        self._persist()
        return self.state.session_id

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
            f"[{THEME['muted']}]type /help or /scope set target=domain.com authorized=yes[/]"
        )

    def resume(self, session_id: str | None = None) -> str:
        loaded = self.session_store.load(session_id) if session_id else self.session_store.load_latest()
        if not loaded:
            return "Tidak ada session. Mulai baru atau ketik apa saja untuk chat."
        self.state = loaded
        self.permission_mgr.set_mode(self.state.permission_mode)
        self.scope_mgr.scope = self.state.scope
        self.findings_db.load_from_dict({"findings": self.state.findings, "hypotheses": self.state.hypotheses})
        summary = self.session_store.resume_context_summary(self.state)
        self._persist()
        return f"✓ Session resumed: {self.state.session_id}\n\n{summary}"

    def handle_input(self, user_input: str) -> str:
        wizard_resp = ReconWizard.handle_input(self, user_input)
        if wizard_resp is not None:
            return wizard_resp

        cmd, args = self.slash.parse(user_input)

        if cmd == "/help" or (not cmd.startswith("/") and user_input.strip().lower() in {"help", "?", "bantuan"}):
            return self.slash.help_text()
        if cmd == "/status":
            return self._status_summary("WTH-AI Status")
        if cmd == "/new-session":
            sid = self.new_session()
            return f"Session baru: {sid}"
        if cmd == "/scope":
            return self._handle_scope(args)
        if cmd == "/permission":
            return self._handle_permission(args)
        if cmd == "/resume":
            return self.resume(args[0] if args else None)
        if cmd == "/sessions":
            return self._list_sessions()
        if cmd == "/approve":
            return self._handle_approve(args)
        if cmd == "/findings":
            return self.findings_db.format_summary_table()
        if cmd == "/hypotheses":
            return self.findings_db.format_hypotheses()
        if cmd == "/skills":
            return self.skills.format_catalog(args[0] if args else None)
        if cmd == "/skill":
            return self._show_skill(args[0] if args else "")
        if cmd == "/browser":
            return self._browser_probe(args)
        if cmd == "/stop":
            self.state.current_phase = AssessmentPhase.STOPPED
            self._persist()
            return self._status_summary("Testing dihentikan.")
        if cmd == "/recon":
            return self._run_recon(args)
        if cmd == "/doctor":
            return self.doctor.full_report()
        if cmd == "/intel":
            return self._run_intel(args)
        if cmd == "/mcp":
            return self.mcp.format_status()
        if cmd == "/tools":
            return self._handle_tools(args)
        if cmd == "/hunt":
            return self._run_hunt(args)
        if cmd == "/graph":
            return self.asset_graph.format_summary()
        if cmd in {"/plan", "/static", "/dynamic", "/patch", "/retest"}:
            return self._assess_loop(user_input)
        if cmd == "/execute":
            run_all = "--all" in args
            task_id = next((a for a in args if a != "--all"), None)
            return self._execute_next(task_id, run_all=run_all)
        if cmd == "/verify":
            return self._verify(args[0] if args else None)
        if cmd == "/report":
            return self._report()

        intent = self.skill_router.detect_intent(user_input)
        if intent == "command":
            return self.slash.help_text()
        if intent == "chat":
            return self._chat_loop(user_input)
        return self._assess_loop(user_input)

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
        fast = "--fast" in args
        focus = None
        if "--focus" in args:
            idx = args.index("--focus")
            if idx + 1 < len(args):
                focus = args[idx + 1].lower()
        clean_args = [a for a in args if a not in {"--fast", "--focus"} and not a.startswith("--")]
        if "--focus" in args:
            fi = args.index("--focus")
            if fi + 1 < len(args) and args[fi + 1] in clean_args:
                clean_args = [a for a in clean_args if a != args[fi + 1]]

        target = self.state.scope.target or (clean_args[0] if clean_args else "")
        if not target:
            return ReconWizard.start_target_prompt(self)
        ok, msg = self.scope_mgr.is_in_scope(target)
        if not ok:
            return f"Blocked: {msg}"

        self.state.current_phase = AssessmentPhase.RECON
        result = self.recon.run_sync(
            target,
            fast=fast,
            focus=focus,
            rate_limit_rps=self.scope_mgr.scope.rate_limit_rps,
            auto_install=True,
            terminal_bridge=self.terminal,
        )
        if result.get("error"):
            return f"Recon error: {result['error']}"

        surface = result.get("attack_surface")
        summary = result.get("summary", {})
        if surface:
            self.memory.update_from_recon(surface)
            self.state.metadata["attack_surface_obj"] = surface.to_dict()
            self.state.metadata["tech_profile"] = summary.get("adaptive_profile", "generic_web")
            self.asset_graph.add_from_recon(surface)
            from wth_ai.recon.deep.auth_surface import AuthSurfaceDetector
            for flow in AuthSurfaceDetector().detect(surface.urls):
                self.asset_graph.add_auth_flow(flow["url"], flow["flow_type"])
            for acc in self.state.scope.test_accounts:
                if ":" in acc:
                    role_name = acc.split(":", 1)[0].split("@")[0]
                    self.asset_graph.add_role(role_name)
            self.asset_graph.save()
            cov = self.asset_graph.coverage()
            self.state.metadata["graph_coverage"] = cov
            surface_items = surface.flat_observations()

            # Response-Diff Brain auto-probe
            try:
                pre_findings = self.diff_brain.probe_surface(surface, limit=15, scope=self.state.scope)
                if pre_findings:
                    self.state.metadata["pre_findings"] = pre_findings
            except Exception:
                pass
        else:
            surface_items = []

        self.state.known_assets = list(dict.fromkeys(self.state.known_assets + (surface.subdomains[:100] if surface else [])))
        for item in surface_items:
            if item not in self.state.attack_surface:
                self.state.attack_surface.append(item)

        self.state.metadata["last_recon"] = summary
        self.state.metadata["recon_artifacts"] = result.get("artifacts_dir", "")
        self._persist()

        lines = [
            f"## Recon — {target}",
            f"- Artifacts: `{result.get('artifacts_dir', '')}`",
            f"- Phases: {summary.get('phases_completed', 0)}",
            f"- Graph nodes: {self.asset_graph.coverage().get('nodes', 0)}",
            f"- Graph coverage: {self.state.metadata.get('graph_coverage', {}).get('coverage_pct', 0)}%",
            f"- Subdomains: {summary.get('subdomains', 0)}",
            f"- Live hosts: {summary.get('live_hosts', 0)}",
            f"- URLs: {summary.get('urls', 0)}",
            f"- API endpoints: {summary.get('api_endpoints', 0)}",
            f"- Parameters: {summary.get('parameters', 0)}",
            f"- JS files: {summary.get('js_files', 0)}",
            f"- Secrets: {summary.get('secrets', 0)}",
            f"- Nuclei: {summary.get('nuclei', 0)}",
            "",
        ]
        if result.get("warnings"):
            lines.append("### Warnings")
            for w in result["warnings"][:8]:
                lines.append(f"- {w}")
            lines.append("")

        if surface and surface.live_hosts:
            lines.append("### Live hosts (sample)")
            for h in surface.live_hosts[:10]:
                lines.append(f"- {h.raw or h.url}")
            lines.append("")

        if surface and surface.api_endpoints:
            lines.append("### API endpoints (sample)")
            for u in surface.api_endpoints[:8]:
                lines.append(f"- {u}")
            lines.append("")

        if surface and surface.nuclei:
            lines.append("### Nuclei (sample)")
            for f in surface.nuclei[:5]:
                lines.append(f"- {f}")
            lines.append("")

        if summary.get("matched_rules"):
            lines.append(f"- Adaptive rules: {', '.join(summary['matched_rules'])}")
        if summary.get("skipped_phases"):
            lines.append(f"- Skipped phases: {summary['skipped_phases']}")
        if self.state.metadata.get("pre_findings"):
            lines.append(f"- Pre-findings (diff brain): {len(self.state.metadata['pre_findings'])}")

        if summary.get("thin_surface"):
            lines.append(
                "> **5-Minute Rule:** Surface tipis — banyak 403/static, sedikit API/parameter. "
                "Pertimbangkan ganti target atau perluas scope."
            )
            lines.append("")

        lines.append(f"Lanjut: `/graph` · `/hunt` · `/plan Audit {target}`")
        return "\n".join(lines)

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

    def _handle_tools(self, args: list[str]) -> str:
        if not args:
            return self.tool_registry.format_report()
        if args[0] == "install":
            category = args[1] if len(args) > 1 else "all"
            return self.doctor.auto_install(category, use_sudo=True)
        return self.tool_registry.format_report(args[0])

    def _run_hunt(self, args: list[str]) -> str:
        err = self._require_llm()
        if err:
            return err
        goal = " ".join(args) if args else f"Audit {self.state.scope.target} — defensive vuln hunt"
        loop = EngagementLoop(self)
        return loop.run(goal)

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
                    updates["target"] = val
                    updates.setdefault("in_scope", [val])
                elif key in ("accounts", "test_accounts"):
                    updates["test_accounts"] = [v.strip() for v in val.split(",") if v.strip()]
                else:
                    updates[key] = val
        self.scope_mgr.update_from_dict(updates)
        self.state.scope = self.scope_mgr.scope
        self._persist()
        return self.scope_mgr.display()

    def _handle_permission(self, args: list[str]) -> str:
        if not args:
            return self.permission_mgr.display()
        try:
            self.permission_mgr.set_mode(args[0].lower())
            self.state.permission_mode = self.permission_mgr.mode
            self._persist()
            return self.permission_mgr.display()
        except ValueError as exc:
            return f"Error: {exc}"

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
                tech = self.state.metadata.get("tech_profile", "generic_web")
                self.learning.record_from_finding(
                    tech, finding.cwe or finding.title.split()[0].lower(),
                    verified=True, session_id=self.state.session_id,
                )
            elif verdict == "false_positive":
                self.findings_db.update_finding_state(finding.id, FindingState.FALSE_POSITIVE)
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
        }

    def _sync_state_from_db(self) -> None:
        self.state.findings = [f.to_dict() for f in self.findings_db.findings.values()]
        self.state.hypotheses = [h.to_dict() for h in self.findings_db.hypotheses.values()]

    def _persist(self) -> None:
        if self.config.session.auto_save:
            self.session_store.save(self.state)

    def run_interactive(self) -> None:
        from wth_ai.cli.repl import WTHRepl
        from wth_ai.cli.status_bar import render_status_bar
        from wth_ai.cli.theme import THEME
        from rich.console import Console

        console = Console(highlight=False)
        console.print(self.startup_message(), markup=True)

        def status_fn() -> str:
            ts = self.tool_registry.summary()
            return render_status_bar(
                self.state,
                self.config,
                tools_installed=ts["installed"],
                tools_total=ts["total"],
                llm_online=self.client.available(),
            )

        def handler(text: str) -> str:
            if self.config.models.streaming and text.strip().lower().startswith(("/plan", "/hunt", "/verify")):
                from rich.live import Live
                from rich.markdown import Markdown

                live_tokens: list[str] = []

                def stream_to_live(t: str) -> None:
                    live_tokens.append(t)
                    live.update(Markdown("".join(live_tokens) or "_…_"))

                self.set_stream_callback(stream_to_live)
                try:
                    with Live(Markdown("_…_"), refresh_per_second=12, console=console, transient=True) as live:
                        return self.handle_input(text)
                finally:
                    self.set_stream_callback(None)
            return self.handle_input(text)

        repl = WTHRepl(
            handler, banner="", status_fn=status_fn,
            prompt_fn=lambda: ReconWizard.dynamic_prompt(self.state) or PROMPT_SYMBOL,
        )
        repl.run()

    def run_once(self, command: str) -> str:
        return self.handle_input(command)
