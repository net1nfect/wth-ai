"""`/subagents` slash handler."""

from __future__ import annotations

from typing import Any

from wth_ai.agents.subagent.runner import SubagentRunner
from wth_ai.platform.registry import get_platform_registry


def handle_subagents(orch: Any, args: list[str]) -> str:
    sub = args[0] if args else "list"
    if sub == "list":
        return get_platform_registry().subagent_list_text()
    if sub == "status":
        runner = SubagentRunner(orch)
        return runner.status_text()
    if sub == "run" and len(args) > 1:
        agent_id = args[1]
        prompt = " ".join(args[2:]) if len(args) > 2 else f"Analyze {orch.state.scope.target or 'target'}"
        runner = SubagentRunner(orch)
        return runner.run(agent_id, prompt)
    return get_platform_registry().subagent_list_text()
