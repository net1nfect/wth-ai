"""Tool-aware executor runner with fallbacks and live output."""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Callable

from wth_ai.models import ExecutorResult, ExecutorTask
from wth_ai.probe.diff_brain import ResponseDiffBrain
from wth_ai.tools.pipeline import ToolChainOrchestrator
from wth_ai.tools.registry import ToolRegistry


# tool_id -> fallback tool_ids
TOOL_FALLBACKS: dict[str, list[str]] = {
    "nuclei": ["nikto", "wapiti"],
    "httpx": ["curl"],
    "subfinder": ["assetfinder", "amass"],
    "ffuf": ["feroxbuster", "gobuster"],
    "katana": ["gau", "waybackurls"],
    "semgrep": ["bandit"],
    "trivy": ["grype"],
}


class ExecutorRunnerV2:
    """Resolve tools, run with fallbacks, stream stdout live."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.chains = ToolChainOrchestrator(self.registry)
        self.diff_brain = ResponseDiffBrain()
        self.on_line = on_line or self._default_on_line

    def _default_on_line(self, line: str) -> None:
        if sys.stdout.isatty():
            print(f"  │ {line[:200]}", flush=True)

    def resolve_tool(self, tool_id: str) -> str | None:
        if self.registry.is_installed(tool_id):
            return self.registry.binary_path(tool_id)
        for alt in TOOL_FALLBACKS.get(tool_id, []):
            if self.registry.is_installed(alt):
                return self.registry.binary_path(alt)
        return None

    def run_shell_live(self, cmd: str, *, timeout: int = 300) -> tuple[str, str, int]:
        from wth_ai.shell_runner import run_command

        stdout_lines: list[str] = []

        def _on_line(line: str) -> None:
            stdout_lines.append(line)
            self.on_line(line)

        stdout, stderr, code = run_command(cmd, timeout=timeout, on_line=_on_line)
        return stdout or "\n".join(stdout_lines), stderr, code

    def probe_with_diff_brain(self, task: ExecutorTask) -> ExecutorResult | None:
        schema = task.evidence_schema or {}
        vuln_type = str(schema.get("type", "")).lower()
        if vuln_type not in {"idor", "xss", "sqli", "ssrf"}:
            return None
        from wth_ai.models import ParamCandidate
        from urllib.parse import urlparse

        url = task.target
        param = ""
        if "?" in url:
            for pair in urlparse(url).query.split("&"):
                if "=" in pair:
                    param = pair.split("=", 1)[0]
                    break
        if not param:
            return None

        findings = self.diff_brain.probe_param(ParamCandidate(url=url, param=param))
        if not findings:
            return None
        best = max(findings, key=lambda f: f.anomaly_score)
        return ExecutorResult(
            task_id=task.id,
            status="success",
            permission_check={"mode": task.permission_required.value, "allowed": True},
            commands_used=[f"diff_brain:{best.mutation}"],
            output_summary=f"{best.signal} score={best.anomaly_score} on {param}",
            evidence=[f"{best.signal} score={best.anomaly_score} param={param}"],
            observations=[f"mutation={best.mutation}"],
            risk="high" if best.anomaly_score >= 0.7 else "medium",
            recommended_next_step="/verify",
            errors="none",
        )

    def run_dynamic_chain(self, task: ExecutorTask) -> ExecutorResult | None:
        target = task.target.replace("https://", "").replace("http://", "").split("/")[0]
        if not target:
            return None
        result = self.chains.chain_subdomain_dns_http(target)
        if result.returncode != 0 and not result.parsed.get("hosts"):
            return None
        hosts = result.parsed.get("hosts", [])
        summary = "\n".join(h.raw or h.url for h in hosts[:15])
        return ExecutorResult(
            task_id=task.id,
            status="success",
            permission_check={"mode": task.permission_required.value, "allowed": True},
            commands_used=["chain:subdomain_dns_http"],
            output_summary=summary or result.stdout[:2000],
            evidence=summary.splitlines()[:10],
            observations=[f"live_hosts={len(hosts)}"],
            risk="low",
            recommended_next_step="/plan",
            errors=result.stderr[:200] if result.returncode != 0 else "none",
        )

    def execute_plans(self, task: ExecutorTask, plans: list[dict[str, str]]) -> ExecutorResult:
        used, output, evidence, obs, errors = [], [], [], [], []
        for plan in plans[:5]:
            cmd = plan.get("command", "")
            if not cmd:
                continue
            # Resolve first token as tool id if possible
            tool_token = cmd.split()[0] if cmd.split() else ""
            if self.registry.tools.get(tool_token) and not self.registry.is_installed(tool_token):
                resolved = self.resolve_tool(tool_token)
                if resolved:
                    cmd = cmd.replace(tool_token, resolved, 1)
                else:
                    obs.append(f"skip missing tool: {tool_token}")
                    continue
            stdout, stderr, code = self.run_shell_live(cmd)
            used.append(cmd)
            if stdout:
                output.append(stdout[:3000])
                evidence.extend(stdout.splitlines()[:15])
            obs.append(f"exit {code}")
            if stderr:
                errors.append(stderr[:200])
        return ExecutorResult(
            task_id=task.id,
            status="success" if used and not errors else ("failed" if errors else "success"),
            permission_check={"mode": task.permission_required.value, "allowed": True},
            commands_used=used,
            output_summary="\n".join(output)[:3000] or "No output",
            evidence=[e[:300] for e in evidence],
            observations=obs,
            risk="low",
            recommended_next_step="Send to verifier",
            errors="; ".join(errors) or "none",
        )
