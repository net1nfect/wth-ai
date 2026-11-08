"""Executor modules — static, dynamic, fuzzer, dependency, log."""

from __future__ import annotations

from abc import ABC, abstractmethod

from wth_ai.config import WTHConfig
from wth_ai.executor.evidence import EvidencePipeline
from wth_ai.executor.runner_v2 import ExecutorRunnerV2
from wth_ai.executor.sandbox import Sandbox
from wth_ai.models import ExecutorResult, ExecutorTask
from wth_ai.tools.runner import ManifestToolRunner


class BaseModule(ABC):
    name: str = "base"

    def __init__(self, runner: ManifestToolRunner, config: WTHConfig) -> None:
        self.runner = runner
        self.config = config
        self.evidence = EvidencePipeline()
        self.runner_v2 = ExecutorRunnerV2(runner.registry)

    @abstractmethod
    def execute(self, task: ExecutorTask, sandbox: Sandbox) -> ExecutorResult:
        ...


class StaticAnalyzer(BaseModule):
    name = "static"

    def execute(self, task: ExecutorTask, sandbox: Sandbox) -> ExecutorResult:
        tool = self.runner_v2.resolve_tool("semgrep") or self.runner_v2.resolve_tool("bandit")
        cmd = f"{tool} --config auto {task.target}" if tool else ""
        commands = task.command_plan or ([{"command": cmd, "purpose": task.goal}] if cmd else [])
        return self.runner_v2.execute_plans(task, commands)


class DynamicTester(BaseModule):
    name = "dynamic"

    def execute(self, task: ExecutorTask, sandbox: Sandbox) -> ExecutorResult:
        probe = self.runner_v2.probe_with_diff_brain(task)
        if probe:
            return probe
        if not task.command_plan and task.target:
            chain = self.runner_v2.run_dynamic_chain(task)
            if chain:
                return chain
        target = task.target
        httpx = self.runner_v2.resolve_tool("httpx")
        plans = task.command_plan or [
            {"command": f"{httpx or 'httpx'} -u {target} -silent -status-code -title", "purpose": "probe"},
            {"command": f"curl -sI {target}", "purpose": "headers"},
        ]
        return self.runner_v2.execute_plans(task, plans)


class Fuzzer(BaseModule):
    name = "fuzzer"

    def execute(self, task: ExecutorTask, sandbox: Sandbox) -> ExecutorResult:
        ffuf = self.runner_v2.resolve_tool("ffuf") or self.runner_v2.resolve_tool("feroxbuster")
        plans = task.command_plan or [
            {"command": f"{ffuf or 'ffuf'} -u {task.target}/FUZZ -w /usr/share/wordlists/dirb/common.txt -mc 200,301,302 -t 10", "purpose": task.goal},
        ]
        return self.runner_v2.execute_plans(task, plans)


class DependencyScanner(BaseModule):
    name = "dependency"

    def execute(self, task: ExecutorTask, sandbox: Sandbox) -> ExecutorResult:
        trivy = self.runner_v2.resolve_tool("trivy") or self.runner_v2.resolve_tool("grype")
        plans = task.command_plan or [
            {"command": f"{trivy or 'trivy'} fs {task.target}", "purpose": "CVE scan"},
        ]
        return self.runner_v2.execute_plans(task, plans)


class LogParser(BaseModule):
    name = "log"

    def execute(self, task: ExecutorTask, sandbox: Sandbox) -> ExecutorResult:
        raw = task.command_plan[0].get("command", "") if task.command_plan else task.goal
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()][:50]
        evidence = [ln for ln in lines if any(k in ln.lower() for k in ("error", "vuln", "cve", "403", "401", "sql"))]
        return ExecutorResult(
            task_id=task.id,
            status="success",
            permission_check={"mode": task.permission_required.value, "allowed": True},
            commands_used=["log_parser"],
            output_summary="\n".join(lines[:20]),
            evidence=evidence,
            observations=[f"Parsed {len(lines)} lines"],
            risk="low",
            recommended_next_step="Verify signals",
            errors="none",
        )


MODULES: dict[str, type[BaseModule]] = {
    "static": StaticAnalyzer,
    "dynamic": DynamicTester,
    "fuzzer": Fuzzer,
    "dependency": DependencyScanner,
    "log": LogParser,
}
