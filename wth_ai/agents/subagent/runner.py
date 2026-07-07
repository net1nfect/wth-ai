"""Run subagents in isolated context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wth_ai.agents.subagent.registry import SPECS, get_spec


@dataclass
class SubagentRun:
    agent_id: str
    status: str = "pending"
    summary: str = ""


@dataclass
class SubagentRunner:
    orch: Any
    depth: int = 0
    runs: list[SubagentRun] = field(default_factory=list)

    def status_text(self) -> str:
        if not self.runs:
            return "No subagent runs in this session."
        lines = ["## Subagent status", ""]
        for r in self.runs[-10:]:
            lines.append(f"- **{r.agent_id}**: {r.status} — {r.summary[:80]}")
        return "\n".join(lines)

    def run(self, agent_id: str, prompt: str) -> str:
        from wth_ai.agents.subagent.registry import MAX_DEPTH

        if self.depth >= MAX_DEPTH:
            return f"Subagent depth limit ({MAX_DEPTH}) reached."
        spec = get_spec(agent_id)
        if not spec:
            return f"Unknown subagent: {agent_id}. Try `/subagents list`."

        run = SubagentRun(agent_id=agent_id, status="running")
        self.runs.append(run)
        bus = getattr(self.orch, "_ui_bus", None)
        if bus:
            bus.emit("subagent_start", agent_id=agent_id, prompt=prompt[:120])

        target = self.orch.state.scope.target or "target"
        skill_ctx = ""
        for sid in spec.skill_ids:
            sk = self.orch.skills.get(sid)
            if sk:
                skill_ctx += sk.load_content(4000) + "\n"

        if agent_id == "recon":
            from wth_ai.cli.handlers.recon_handler import handle_recon
            result = handle_recon(self.orch, [target])
        elif agent_id == "reporter":
            result = self.orch._report()
        elif agent_id in {"injection", "xss", "auth", "authz", "ssrf", "infra"}:
            goal = f"[{agent_id}] {prompt}\nTarget: {target}\n\nSkill context:\n{skill_ctx[:6000]}"
            result = self.orch._assess_loop(f"/plan {goal}")
        elif agent_id == "ctf":
            goal = f"[CTF] {prompt}\n{skill_ctx[:4000]}"
            result = self.orch._assess_loop(f"/plan {goal}")
        else:
            result = f"Subagent {agent_id} not implemented."

        run.status = "done"
        run.summary = result[:200]
        if hasattr(self.orch, "state"):
            runs = self.orch.state.metadata.setdefault("subagent_runs", [])
            runs.append({"agent_id": agent_id, "status": run.status, "summary": run.summary})
        if bus:
            bus.emit("subagent_end", agent_id=agent_id, summary=run.summary)
        return f"## Subagent: {agent_id}\n\n{result}"
