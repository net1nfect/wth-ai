"""Exploit chain planner — multi-step attack sequences."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from wth_ai.models import ExecutorTask, PermissionMode


CHAIN_TEMPLATES: dict[str, list[dict[str, str]]] = {
    "idor": [
        {"step": "baseline_user_a", "action": "fetch_as_user_a", "proof": "own_data"},
        {"step": "swap_id_user_b", "action": "fetch_as_user_b_with_a_id", "proof": "cross_user_data"},
        {"step": "diff_response", "action": "compare_responses", "proof": "diff_user_data"},
    ],
    "ssrf": [
        {"step": "baseline", "action": "normal_request", "proof": "baseline"},
        {"step": "internal_probe", "action": "inject_internal_url", "proof": "ssrf_callback"},
    ],
    "xss": [
        {"step": "reflect_probe", "action": "inject_marker", "proof": "reflection"},
        {"step": "context_check", "action": "analyze_context", "proof": "executable_context"},
    ],
    "auth_jwt": [
        {"step": "capture_token", "action": "login_capture_jwt", "proof": "token_obtained"},
        {"step": "alg_none", "action": "jwt_alg_none", "proof": "forged_accepted"},
    ],
    "race": [
        {"step": "single_request", "action": "baseline_single", "proof": "baseline"},
        {"step": "concurrent_burst", "action": "race_n_requests", "proof": "duplicate_success"},
    ],
}


@dataclass
class ExploitChain:
    id: str
    title: str
    vuln_class: str
    target: str
    steps: list[dict[str, Any]]
    priority: int = 5
    evidence_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "vuln_class": self.vuln_class,
            "target": self.target,
            "steps": self.steps,
            "priority": self.priority,
            "evidence_schema": self.evidence_schema,
        }


class ChainPlanner:
    """Build exploit chains from hypotheses."""

    def plan_from_hypotheses(
        self,
        hypotheses: list[dict[str, Any]],
        target: str,
    ) -> list[ExploitChain]:
        chains: list[ExploitChain] = []
        for hyp in hypotheses[:15]:
            vc = hyp.get("vuln_class", "unknown")
            steps = hyp.get("chain_steps") or CHAIN_TEMPLATES.get(vc, [])
            if not steps and vc in CHAIN_TEMPLATES:
                steps = CHAIN_TEMPLATES[vc]
            if not steps:
                continue
            chains.append(ExploitChain(
                id=f"CHAIN-{uuid4().hex[:8]}",
                title=hyp.get("title", f"{vc} chain"),
                vuln_class=vc,
                target=hyp.get("component", target),
                steps=steps,
                priority=hyp.get("priority", 5),
                evidence_schema={
                    "type": vc,
                    "proof": steps[-1].get("proof", "manual") if steps else "manual",
                    "severity_min": "medium",
                },
            ))
        chains.sort(key=lambda c: -c.priority)
        return chains

    def chains_to_tasks(self, chains: list[ExploitChain]) -> list[ExecutorTask]:
        tasks: list[ExecutorTask] = []
        module_map = {
            "idor": "dynamic", "ssrf": "dynamic", "xss": "dynamic",
            "auth_jwt": "dynamic", "auth": "dynamic", "race": "dynamic",
            "sqli": "dynamic", "unknown": "dynamic",
        }
        for chain in chains[:8]:
            tasks.append(ExecutorTask(
                id=f"TASK-{uuid4().hex[:8]}",
                goal=chain.title,
                target=chain.target,
                permission_required=PermissionMode.STANDARD_TESTING,
                allowed_tools=["httpx", "curl", "diff_brain"],
                evidence_schema=chain.evidence_schema,
                module=module_map.get(chain.vuln_class, "dynamic"),
                command_plan=[{"command": s.get("action", ""), "purpose": s.get("step", "")} for s in chain.steps],
            ))
        return tasks
