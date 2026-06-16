"""Main H1T4M-AI engagement coordinator."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

from agents import AGENT_CLASSES
from exploitation.execution_engine import ExecutionEngine
from exploitation.payload_generator import PayloadGenerator
from exploitation.vulnerability_classifier import VulnerabilityClassifier
from learning.reinforcement_engine import ReinforcementEngine
from reconnaissance import PHASES
from reporting.report_generator import ReportGenerator
from utils.validators import enforce_scope

from .config import H1T4MConfiguration
from .database import ResultsDatabase
from .logger import audit, get_logger
from .models import AsyncMessageQueue, SharedKnowledgeBase
from .state_machine import ExecutionState, ExecutionStateMachine
from .threat_intel_manager import ThreatIntelligenceManager


class H1T4M_Orchestrator:
    """Coordinate authorized agents, phases, persistence, and reporting."""

    def __init__(self, config: H1T4MConfiguration | None = None) -> None:
        self.config = config or H1T4MConfiguration()
        self.logger = get_logger("h1t4m.orchestrator")
        self.message_queue = AsyncMessageQueue()
        self.knowledge_base = SharedKnowledgeBase()
        self.execution_state = ExecutionStateMachine()
        self.results_storage = ResultsDatabase(self.config.database.url)
        self.threat_intel = ThreatIntelligenceManager(self.config.threat_intel, self.logger)
        self.agents: dict[str, Any] = {}
        self.engagement_id: str | None = None
        self.engagement: dict[str, Any] = {}
        self._shutdown = asyncio.Event()
        self._active_tasks: set[asyncio.Task[Any]] = set()

    async def initialize_engagement(
        self, target: str, scope: list[str], objectives: list[str]
    ) -> str:
        """Validate authorization and initialize engagement services."""
        await self.execution_state.transition(ExecutionState.INITIALIZING, "engagement initialization")
        normalized = enforce_scope(target, scope, self.config.engagement.authorization_proof)
        self.engagement = {
            "target": normalized,
            "scope": scope,
            "objectives": objectives,
            "authorization_verified": True,
            "dry_run": self.config.exploitation.dry_run,
        }
        self.engagement_id = self.results_storage.create_engagement(self.engagement)
        await self.message_queue.register("coordinator")
        for name in self.config.agents.enable_agents:
            agent = AGENT_CLASSES[name](self.knowledge_base, self.message_queue, get_logger(f"h1t4m.agent.{name}", self.engagement_id))
            await agent.initialize(
                {"communication_timeout": self.config.agents.agent_communication_timeout}
            )
            self.agents[name] = agent
        await self.threat_intel.initialize_feeds()
        await self.knowledge_base.update("engagement", self.engagement)
        audit(self.logger, "engagement_initialized", {"engagement_id": self.engagement_id, "target": normalized})
        return self.engagement_id

    async def execute_reconnaissance_pipeline(
        self, context: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute selected reconnaissance phases concurrently over supplied evidence."""
        self._require_engagement()
        context = {"target": self.engagement["target"], "mode": "passive", **(context or {})}
        phases = self.config.reconnaissance.phases_to_run
        for number in phases:
            state = ExecutionState[f"PHASE{number}_RECONNAISSANCE"]
            await self.execution_state.transition(state, f"scheduled phase {number}")
        tasks = [asyncio.create_task(PHASES[number]().run(context)) for number in phases]
        self._active_tasks.update(tasks)
        try:
            results = await asyncio.gather(*tasks)
        finally:
            self._active_tasks.difference_update(tasks)
        await self.execution_state.transition(ExecutionState.ANALYZING_RESULTS, "reconnaissance complete")
        await self.knowledge_base.update("reconnaissance", results)
        for result in results:
            observations = result.get("observations", [])
            if observations:
                self.results_storage.store_finding(
                    self.engagement_id or "",
                    {
                        "type": f"recon_{result['name']}",
                        "severity": "info",
                        "evidence": observations,
                    },
                )
        return results

    async def autonomous_exploitation_workflow(
        self, findings: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """Prioritize and safely validate findings under configured controls."""
        self._require_engagement()
        await self.execution_state.transition(ExecutionState.TIER1_CLASSIFICATION, "classify findings")
        classifier = VulnerabilityClassifier()
        prioritized = classifier.prioritize(findings or [])
        await self.execution_state.transition(ExecutionState.TIER2_PAYLOAD_GEN, "generate inert canaries")
        generator = PayloadGenerator()
        requests = [generator.generate(item) for item in prioritized]
        await self.execution_state.transition(ExecutionState.TIER3_EXECUTION, "controlled validation")
        engine = ExecutionEngine(
            dry_run=self.config.exploitation.dry_run,
            rate_limit=self.config.security.requests_per_second,
        )
        results = [await engine.execute(request) for request in requests]
        await self.execution_state.transition(ExecutionState.TIER4_ESCALATION, "assessment-only escalation review")
        for finding, result in zip(prioritized, results):
            self.results_storage.store_exploitation(
                self.engagement_id or "",
                {"vuln_type": finding["type"], "success": result["success"], **result},
            )
        await self.knowledge_base.update("validation_results", results)
        return results

    async def coordinate_multi_agent_system(
        self, tasks: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Distribute tasks to enabled agents and collect results."""
        self._require_engagement()
        tasks = tasks or {}
        coroutines = {
            name: asyncio.create_task(agent.run(tasks.get(name, {})))
            for name, agent in self.agents.items()
        }
        self._active_tasks.update(coroutines.values())
        try:
            results = {name: await task for name, task in coroutines.items()}
        finally:
            self._active_tasks.difference_update(coroutines.values())
        for name, agent in self.agents.items():
            self.results_storage.update_agent_metrics(agent.agent_id, agent.generate_performance_report())
        return results

    async def enable_reinforcement_learning(
        self, samples: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Collect, train, update, and persist a lightweight learning model."""
        engine = ReinforcementEngine()
        engine.collect_metrics(samples or [])
        model = engine.train()
        if self.config.learning.model_persistence:
            engine.persist(Path("data/ml_models") / "strategy_scores.json")
        for sample in samples or []:
            self.results_storage.store_learning_data(self.engagement_id or "", "strategy", sample)
        await self.knowledge_base.update("learning_model", model)
        return {"model": model, "strategies": engine.update_strategies()}

    async def integrate_threat_intelligence(
        self, tech_stack: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Correlate discovered technologies with public threat intelligence."""
        if not tech_stack:
            return []
        matches = await self.threat_intel.correlate_vulnerabilities(tech_stack)
        await self.knowledge_base.update("threat_intelligence", matches)
        return matches

    async def generate_intelligent_report(self) -> list[str]:
        """Compile findings, visualizations, compliance, and remediation."""
        self._require_engagement()
        await self.execution_state.transition(ExecutionState.REPORTING, "generate final report")
        findings = [item["data"] | {"id": item["id"]} for item in self.results_storage.query_findings({"engagement_id": self.engagement_id})]
        generator = ReportGenerator()
        report = generator.compile(self.engagement, findings)
        outputs = generator.generate(
            self.engagement_id or "engagement", report, self.config.reporting.report_format
        )
        audit(self.logger, "report_generated", {"engagement_id": self.engagement_id, "outputs": outputs})
        return outputs

    async def execute_engagement_lifecycle(
        self,
        target: str | None = None,
        scope: list[str] | None = None,
        objectives: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run pre-checks, recon, safe validation, agents, cleanup, and reporting."""
        target = target or self.config.engagement.target
        scope = scope or self.config.engagement.scope
        objectives = objectives or self.config.engagement.objectives
        try:
            await self.initialize_engagement(target, scope, objectives)
            recon = await self.execute_reconnaissance_pipeline(context)
            findings = (context or {}).get("findings", [])
            validations = await self.autonomous_exploitation_workflow(findings)
            agents = await self.coordinate_multi_agent_system()
            learning = await self.enable_reinforcement_learning([])
            threat_intel = await self.integrate_threat_intelligence((context or {}).get("technologies", []))
            reports = await self.generate_intelligent_report()
            await self.execution_state.transition(ExecutionState.CLEANUP, "engagement cleanup")
            await self.execution_state.transition(ExecutionState.IDLE, "engagement complete")
            return {
                "engagement_id": self.engagement_id,
                "reconnaissance": recon,
                "validations": validations,
                "agents": {name: result.data for name, result in agents.items()},
                "learning": learning,
                "threat_intelligence": threat_intel,
                "reports": reports,
            }
        except asyncio.CancelledError:
            await self.graceful_shutdown()
            raise
        except Exception:
            self.logger.exception("Engagement lifecycle failed")
            await self.execution_state.transition(ExecutionState.ERROR, "unhandled engagement error")
            await self.graceful_shutdown()
            raise

    def install_signal_handlers(self) -> None:
        """Install graceful shutdown handlers when supported."""
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, lambda: asyncio.create_task(self.graceful_shutdown()))
            except NotImplementedError:
                pass

    async def graceful_shutdown(self) -> None:
        """Cancel managed tasks and record cleanup."""
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        for task in tuple(self._active_tasks):
            task.cancel()
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        audit(self.logger, "graceful_shutdown", {"engagement_id": self.engagement_id})

    def _require_engagement(self) -> None:
        if not self.engagement_id:
            raise RuntimeError("Engagement is not initialized")
